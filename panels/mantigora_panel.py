# panels/mantigora_panel.py

"""
Автор: m.yablokov
Панель управления высоковольтным источником питания серии Mantigora (HT/HP).

Исправления (2026-04-23):
  - Конструктор приведён к сигнатуре BaseDevicePanel(device_type, device_name, parent).
  - Метод _setup_ui переименован в _init_ui (переопределяет BaseDevicePanel._init_ui).
  - MantigoraDriver создаётся с правильными параметрами (voltage_kv, power_w).
  - Управление выходом переведено на реальный API драйвера:
      set_voltage() / set_current_limit() / start() / stop() / read_measurement()
  - Добавлены методы set_voltage(), output_on(), output_off() для MantigoraGenerator.
  - Polling реализован через QTimer в UI-потоке (безопаснее для COM-порта).
  - Добавлены get_config() / apply_config() для сохранения и загрузки конфигурации.
"""

import time
from typing import Optional

# Минимальное допустимое напряжение для Mantigora.
# При уставке 0 В — выход выключается. При 0 < U < MIN — ошибка.
MANTIGORA_MIN_VOLTAGE = 10.0

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QGroupBox, QFormLayout, QMessageBox,
    QCheckBox, QDoubleSpinBox, QTextEdit
)

from drivers.mantigora_driver import MantigoraDriver
from panels.base_device_panel import BaseDevicePanel


class MantigoraPanel(BaseDevicePanel):
    """
    Панель для работы с источниками питания Mantigora HT/HP.

    Публичный интерфейс для MantigoraGenerator:
        panel.device_type    → "Mantigora HT"
        panel.is_connected   → bool
        panel.set_voltage(v) → установить уставку напряжения (В)
        panel.output_on()    → включить выход
        panel.output_off()   → выключить выход
    """

    DEVICE_TYPE = "Mantigora HT"

    def __init__(self, device_name: str, parent=None):
        # Инициализировать собственные поля ДО super().__init__,
        # потому что super() вызывает _init_ui(), которая к ним обращается.
        self.driver: Optional[MantigoraDriver] = None
        self._update_timer: Optional[QTimer] = None
        self._max_kv: int = 2
        self._power_w: int = 6

        # Передаём правильные device_type и device_name в базовый класс.
        super().__init__(self.DEVICE_TYPE, device_name, parent)

    # ── UI ───────────────────────────────────────────────────────────────────

    def _init_ui(self):
        """
        Переопределяет BaseDevicePanel._init_ui.
        Строит собственный UI панели Mantigora.
        """
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(8)

        # --- Подключение ---
        conn_group = QGroupBox("Подключение")
        form = QFormLayout(conn_group)

        self.port_combo = QComboBox()
        self.port_combo.addItems(["COM1", "COM2", "COM3", "COM4", "COM5", "COM6"])
        self.port_combo.setEditable(True)
        form.addRow("COM-порт:", self.port_combo)

        self.series_combo = QComboBox()
        self.series_combo.addItems(["HT", "HP"])
        form.addRow("Серия:", self.series_combo)

        self.power_combo = QComboBox()
        self.power_combo.addItems(["6 Вт", "15 Вт", "60 Вт"])
        form.addRow("Мощность:", self.power_combo)

        self.max_voltage_combo = QComboBox()
        self.max_voltage_combo.addItems(["2 кВ", "6 кВ", "10 кВ", "20 кВ", "30 кВ"])
        form.addRow("Макс. напряжение:", self.max_voltage_combo)

        btn_layout = QHBoxLayout()
        self.connect_btn = QPushButton("Подключить")
        self.connect_btn.clicked.connect(self._on_connect_clicked)
        self.disconnect_btn = QPushButton("Отключить")
        self.disconnect_btn.clicked.connect(self._on_disconnect_clicked)
        self.disconnect_btn.setEnabled(False)
        btn_layout.addWidget(self.connect_btn)
        btn_layout.addWidget(self.disconnect_btn)
        form.addRow(btn_layout)

        self.status_label = QLabel("● Не подключено")
        self.status_label.setStyleSheet("color: #f85149; font-weight: bold;")
        form.addRow(self.status_label)

        main_layout.addWidget(conn_group)

        # --- Управление выходом ---
        out_group = QGroupBox("Выходные параметры")
        out_layout = QFormLayout(out_group)

        self.voltage_spin = QDoubleSpinBox()
        self.voltage_spin.setRange(0.0, 30000.0)
        self.voltage_spin.setSuffix(" В")
        self.voltage_spin.setDecimals(1)
        self.voltage_spin.setEnabled(False)
        self.voltage_spin.setToolTip(
            f"0 В = выключить выход\n"
            f"Минимальное рабочее напряжение: {MANTIGORA_MIN_VOLTAGE:.0f} В"
        )
        out_layout.addRow("Напряжение:", self.voltage_spin)

        self.current_spin = QDoubleSpinBox()
        self.current_spin.setRange(0.0, 30.0)   # мА, макс. зависит от модели
        self.current_spin.setSuffix(" мА")
        self.current_spin.setDecimals(4)
        self.current_spin.setEnabled(False)
        out_layout.addRow("Ток макс.:", self.current_spin)

        apply_btn = QPushButton("Применить и активировать")
        apply_btn.clicked.connect(self.apply_output)
        out_layout.addRow(apply_btn)

        self.disable_btn = QPushButton("Выключить выход")
        self.disable_btn.clicked.connect(self.disable_output)
        self.disable_btn.setEnabled(False)
        out_layout.addRow(self.disable_btn)

        main_layout.addWidget(out_group)

        # --- Измеренные значения ---
        meas_group = QGroupBox("Измеренные значения")
        meas_layout = QFormLayout(meas_group)

        self.meas_voltage_label = QLabel("— В")
        meas_layout.addRow("U изм.:", self.meas_voltage_label)

        self.meas_current_label = QLabel("— мА")
        meas_layout.addRow("I изм.:", self.meas_current_label)

        self.continuous_check = QCheckBox("Непрерывное обновление (1 с)")
        self.continuous_check.toggled.connect(self._on_continuous_toggled)
        meas_layout.addRow(self.continuous_check)

        main_layout.addWidget(meas_group)

        # --- Лог событий ---
        # Атрибут self.event_log нужен методу log_event() из BaseDevicePanel.
        log_group = QGroupBox("События")
        log_layout = QVBoxLayout(log_group)
        self.event_log = QTextEdit()
        self.event_log.setReadOnly(True)
        self.event_log.setMaximumHeight(130)
        log_layout.addWidget(self.event_log)
        main_layout.addWidget(log_group)

        # Таймер опроса — работает в UI-потоке, безопасен для COM
        self._update_timer = QTimer()
        self._update_timer.timeout.connect(self._poll_measurements)
        self._update_timer.setInterval(1000)

        self._apply_styles()

    # ── Подключение / отключение ──────────────────────────────────────────────

    def connect_device(self) -> bool:
        """
        Создать MantigoraDriver и открыть COM-порт.
        Вызывается из BaseDevicePanel._on_connect_clicked().
        """
        port     = self.port_combo.currentText().strip()
        power_str = self.power_combo.currentText()         # "60 Вт"
        max_kv_str = self.max_voltage_combo.currentText()  # "20 кВ"

        if not port:
            QMessageBox.warning(self, "Ошибка", "Не указан COM-порт")
            return False

        try:
            self._power_w = int(power_str.split()[0])   # "60 Вт" → 60
            self._max_kv  = int(max_kv_str.split()[0])  # "20 кВ" → 20
        except (ValueError, IndexError) as e:
            QMessageBox.warning(self, "Ошибка", f"Неверный формат параметров: {e}")
            return False

        try:
            self.driver = MantigoraDriver(
                port=port,
                voltage_kv=self._max_kv,
                power_w=self._power_w,
            )
            self.driver.connect()  # бросает исключение при ошибке; возвращает True

            self.voltage_spin.setEnabled(True)
            self.current_spin.setEnabled(True)
            self.disable_btn.setEnabled(True)

            series = self.series_combo.currentText()
            self.log_event(
                f"Подключено: порт={port}, серия={series}, "
                f"{self._max_kv} кВ, {self._power_w} Вт"
            )
            return True

        except Exception as e:
            QMessageBox.critical(self, "Ошибка подключения", str(e))
            self.driver = None
            return False

    def disconnect_device(self):
        """
        Разорвать соединение.
        Вызывается из BaseDevicePanel._on_disconnect_clicked().
        """
        if self.driver:
            try:
                self.driver.disconnect()
            except Exception:
                pass
            self.driver = None

        self.voltage_spin.setEnabled(False)
        self.current_spin.setEnabled(False)
        self.disable_btn.setEnabled(False)
        self.meas_voltage_label.setText("— В")
        self.meas_current_label.setText("— мА")

    # ── Polling (QTimer вместо threading из BaseDevicePanel) ──────────────────

    def start_polling(self):
        """Запустить QTimer-опрос (вызывается из BaseDevicePanel._on_connect_clicked)."""
        if self.continuous_check.isChecked():
            self._update_timer.start()

    def stop_polling(self):
        """Остановить опрос (вызывается из BaseDevicePanel и MainWindow.closeEvent)."""
        if self._update_timer:
            self._update_timer.stop()

    def read_device_values(self) -> dict:
        """
        Заглушка — данные получаем через QTimer, а не через поток BaseDevicePanel.
        """
        return {}

    def _poll_measurements(self):
        """Периодический опрос (вызывается QTimer каждую секунду)."""
        if not self.driver or not self.driver.is_connected:
            return
        try:
            v, i_ma = self.driver.read_measurement()
            self.meas_voltage_label.setText(f"{v:.1f} В")
            self.meas_current_label.setText(f"{i_ma:.4f} мА")
            # Сигнал для Dashboard и CSV-истории
            self.data_updated.emit(self.device_name, "voltage", v, "В")
            self.data_updated.emit(self.device_name, "current", i_ma, "мА")
        except Exception as e:
            self.log_event(f"Ошибка измерения: {e}")

    def _on_continuous_toggled(self, checked: bool):
        """Включить/выключить автоопрос по чекбоксу."""
        if self.driver and self.driver.is_connected:
            if checked:
                self._update_timer.start()
            else:
                self._update_timer.stop()

    # ── Управление выходом ───────────────────────────────────────────────────

    def apply_output(self):
        """Установить уставки и включить выход (для кнопки в UI)."""
        if not self.driver or not self.driver.is_connected:
            QMessageBox.warning(self, "Ошибка", "Устройство не подключено")
            return
        voltage = self.voltage_spin.value()
        try:
            if voltage == 0.0:
                # 0 В — выключить выход
                self.driver.stop()
                self.log_event("Выход выключен (уставка 0 В)")
            elif voltage < MANTIGORA_MIN_VOLTAGE:
                QMessageBox.warning(
                    self, "Недопустимое напряжение",
                    f"Минимальное напряжение Mantigora: {MANTIGORA_MIN_VOLTAGE:.0f} В.\n"
                    f"Установите 0 В (выключить) или ≥ {MANTIGORA_MIN_VOLTAGE:.0f} В."
                )
            else:
                self.driver.set_voltage(voltage)
                self.driver.set_current_limit(self.current_spin.value())
                self.driver.start()
                self.log_event(
                    f"Выход активирован: U={voltage:.1f} В, "
                    f"I_max={self.current_spin.value():.4f} мА"
                )
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", str(e))

    def disable_output(self):
        """Выключить выход (для кнопки в UI)."""
        if not self.driver or not self.driver.is_connected:
            return
        try:
            self.driver.stop()
            self.log_event("Выход выключен")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", str(e))

    # ── Методы для MantigoraGenerator ────────────────────────────────────────

    def set_voltage(self, voltage_v: float):
        """
        Установить уставку напряжения (В).
        Используется MantigoraGenerator.set_point() и AutoRunThread.

        Правила:
          0 В          → выключить выход (drv.stop())
          < MIN (10 В) → ValueError, не передавать на устройство
          ≥ MIN        → установить и включить
        """
        if not self.driver or not self.driver.is_connected:
            return
        if voltage_v == 0.0:
            self.driver.stop()
            self.voltage_spin.setValue(0.0)
        elif voltage_v < MANTIGORA_MIN_VOLTAGE:
            raise ValueError(
                f"Mantigora: напряжение {voltage_v:.1f} В ниже минимума "
                f"({MANTIGORA_MIN_VOLTAGE:.0f} В). "
                f"Задайте 0 В (выкл.) или ≥ {MANTIGORA_MIN_VOLTAGE:.0f} В."
            )
        else:
            self.driver.set_voltage(voltage_v)
            self.voltage_spin.setValue(voltage_v)

    def output_on(self):
        """
        Активировать выход с текущими уставками.
        Используется MantigoraGenerator.set_point().
        """
        if self.driver and self.driver.is_connected:
            self.driver.start()

    def output_off(self):
        """
        Деактивировать выход.
        Используется MantigoraGenerator.output_off().
        """
        if self.driver and self.driver.is_connected:
            try:
                self.driver.stop()
            except Exception:
                pass

    # ── Интерфейс для вкладок измерений ──────────────────────────────────────

    def get_device_id(self) -> str:
        return f"Mantigora_{self.port_combo.currentText()}"

    def get_measurement(self) -> dict:
        """
        Считать текущие измеренные значения.
        Используется ManualGenerationTab и AutoTestWorker.
        """
        if self.driver and self.driver.is_connected:
            try:
                v, i_ma = self.driver.read_measurement()
                return {
                    'voltage': v,
                    'current': i_ma,
                    'output_enabled': self.driver.output_active,
                }
            except Exception:
                pass
        return {'voltage': None, 'current': None, 'output_enabled': False}

    # ── Конфигурация ─────────────────────────────────────────────────────────

    def get_config(self) -> dict:
        return {
            'type':    self.DEVICE_TYPE,   # "Mantigora HT" — используется при загрузке
            'name':    self.device_name,
            'port':    self.port_combo.currentText(),
            'series':  self.series_combo.currentText(),
            'power_w': self._power_w,
            'max_kv':  self._max_kv,
        }

    def apply_config(self, config: dict):
        """Восстановить настройки из сохранённого конфига."""
        port = config.get('port', 'COM1')
        idx = self.port_combo.findText(port)
        if idx >= 0:
            self.port_combo.setCurrentIndex(idx)
        else:
            self.port_combo.setCurrentText(port)

        series = config.get('series', 'HT')
        idx = self.series_combo.findText(series)
        if idx >= 0:
            self.series_combo.setCurrentIndex(idx)

        power_w = config.get('power_w', 6)
        idx = self.power_combo.findText(f"{power_w} Вт")
        if idx >= 0:
            self.power_combo.setCurrentIndex(idx)

        max_kv = config.get('max_kv', 2)
        idx = self.max_voltage_combo.findText(f"{max_kv} кВ")
        if idx >= 0:
            self.max_voltage_combo.setCurrentIndex(idx)

    # ── Завершение работы ────────────────────────────────────────────────────

    def closeEvent(self, event):
        """Освобождение ресурсов при закрытии вкладки."""
        self.stop_polling()
        self.disconnect_device()
        super().closeEvent(event)
