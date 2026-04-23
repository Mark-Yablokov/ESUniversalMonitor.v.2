# panels/mantigora_panel.py

"""
Панель управления высоковольтным источником питания серии Mantigora (HT/HP).
Протокол: команды одним байтом [1] установка кодов, [2] активация, [3] отключение, [5] измерение.
"""

import time
import struct
from typing import Optional

from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QComboBox, QGroupBox, QFormLayout, QMessageBox,
    QCheckBox, QSpinBox, QDoubleSpinBox, QTextEdit
)

from drivers.mantigora_driver import MantigoraDriver
from panels.base_device_panel import BaseDevicePanel


class MantigoraPanel(BaseDevicePanel):
    """
    Панель для работы с источниками питания Mantigora.
    Поддерживает модели HT и HP серий с различной мощностью.
    """

    def __init__(self, config: Optional[dict] = None, parent=None):
        self.driver: Optional[MantigoraDriver] = None
        self._update_timer: Optional[QTimer] = None
        # Словарь коэффициентов для преобразования код-напряжение/ток
        self.kv_dict = {2: 32, 6: 10.67, 10: 6.4, 20: 3.2, 30: 2.133}
        self.ki_dict = {
            60: {2: 2.133, 6: 6.4, 10: 10.6, 20: 21.33, 30: 32},
            15: {2: 8.533, 6: 25.6, 10: 42.4, 20: 85.32, 30: 128},
            6:  {2: 21.33, 6: 64, 10: 106, 20: 213.3, 30: 320}
        }
        super().__init__(config, parent)

    def _setup_ui(self):
        """Создание пользовательского интерфейса."""
        main_layout = QVBoxLayout(self)

        # --- Подключение ---
        conn_group = QGroupBox("Подключение")
        form = QFormLayout(conn_group)

        self.port_combo = QComboBox()
        self.port_combo.addItems(["COM1", "COM2", "COM3", "COM4", "COM5", "COM6"])
        self.port_combo.setEditable(True)
        form.addRow("COM-порт:", self.port_combo)

        self.model_combo = QComboBox()
        self.model_combo.addItems(["Mantigora HT", "Mantigora HP"])
        form.addRow("Модель:", self.model_combo)

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
        self.connect_btn.clicked.connect(self.toggle_connection)
        self.disconnect_btn = QPushButton("Отключить")
        self.disconnect_btn.clicked.connect(self.disconnect_device)
        self.disconnect_btn.setEnabled(False)
        btn_layout.addWidget(self.connect_btn)
        btn_layout.addWidget(self.disconnect_btn)
        form.addRow(btn_layout)

        self.status_label = QLabel("Статус: Отключено")
        form.addRow(self.status_label)

        main_layout.addWidget(conn_group)

        # --- Управление выходом ---
        out_group = QGroupBox("Выходные параметры")
        out_layout = QFormLayout(out_group)

        self.voltage_spin = QDoubleSpinBox()
        self.voltage_spin.setRange(0.0, 30000.0)
        self.voltage_spin.setSuffix(" В")
        self.voltage_spin.setDecimals(1)
        out_layout.addRow("Напряжение:", self.voltage_spin)

        self.current_spin = QDoubleSpinBox()
        self.current_spin.setRange(0.0, 10000.0)
        self.current_spin.setSuffix(" мкА")
        self.current_spin.setDecimals(1)
        out_layout.addRow("Ток (уставка):", self.current_spin)

        apply_btn = QPushButton("Применить настройки и активировать")
        apply_btn.clicked.connect(self.apply_settings)
        out_layout.addRow(apply_btn)

        self.output_check = QCheckBox("Выход включен")
        self.output_check.toggled.connect(self.toggle_output)
        self.output_check.setEnabled(False)
        out_layout.addRow(self.output_check)

        self.disable_btn = QPushButton("Сбросить выход (команда 3)")
        self.disable_btn.clicked.connect(self.disable_output)
        self.disable_btn.setEnabled(False)
        out_layout.addRow(self.disable_btn)

        main_layout.addWidget(out_group)

        # --- Измерения ---
        meas_group = QGroupBox("Измеренные значения")
        meas_layout = QFormLayout(meas_group)

        self.meas_voltage_label = QLabel("--- В")
        meas_layout.addRow("U изм.:", self.meas_voltage_label)
        self.meas_current_label = QLabel("--- мкА")
        meas_layout.addRow("I изм.:", self.meas_current_label)

        self.continuous_check = QCheckBox("Непрерывное обновление")
        self.continuous_check.toggled.connect(self.toggle_continuous_update)
        meas_layout.addRow(self.continuous_check)

        main_layout.addWidget(meas_group)

        # --- Лог ---
        log_group = QGroupBox("События")
        log_layout = QVBoxLayout(log_group)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(120)
        log_layout.addWidget(self.log_text)
        main_layout.addWidget(log_group)

        # Таймер обновления
        self._update_timer = QTimer()
        self._update_timer.timeout.connect(self.update_measurements)
        self._update_timer.setInterval(1000)

    def toggle_connection(self):
        """Подключение или отключение в зависимости от текущего состояния."""
        if self.driver and self.driver.is_connected():
            self.disconnect_device()
        else:
            self.connect_device()

    def connect_device(self) -> bool:
        """Установить соединение с устройством."""
        port = self.port_combo.currentText().strip()
        model = self.model_combo.currentText()
        series = self.series_combo.currentText()
        power_str = self.power_combo.currentText()
        max_kv_str = self.max_voltage_combo.currentText()

        if not port:
            QMessageBox.warning(self, "Ошибка", "Не указан COM-порт")
            return False

        hp_mode = (series.upper() == 'HP')
        power_w = int(power_str.split()[0])  # "60 Вт" -> 60
        max_kv = int(max_kv_str.split()[0])  # "20 кВ" -> 20

        # Сохраняем коэффициенты для дальнейших расчётов
        self.kv = self.kv_dict[max_kv]
        self.ki = self.ki_dict[power_w][max_kv]

        try:
            self.driver = MantigoraDriver(port=port, model=model, hp_mode=hp_mode)
            if self.driver.connect():
                self._on_connected()
                self.log_message(f"Подключено к {model} (серия {series}, {power_w} Вт, макс {max_kv} кВ) на порту {port}")
                return True
            else:
                QMessageBox.warning(self, "Ошибка", "Не удалось подключиться к устройству")
                self.driver = None
                return False
        except Exception as e:
            QMessageBox.critical(self, "Исключение", f"Ошибка подключения: {str(e)}")
            self.driver = None
            return False

    def disconnect_device(self):
        """Разорвать соединение."""
        if self.driver:
            self.driver.disconnect()
            self.driver = None
        self._on_disconnected()
        self.log_message("Отключено")

    def _on_connected(self):
        """Обновить UI при успешном подключении."""
        self.status_label.setText("Статус: Подключено")
        self.connect_btn.setEnabled(False)
        self.disconnect_btn.setEnabled(True)
        self.output_check.setEnabled(True)
        self.disable_btn.setEnabled(True)
        self.voltage_spin.setEnabled(True)
        self.current_spin.setEnabled(True)
        if self.continuous_check.isChecked():
            self._update_timer.start()

    def _on_disconnected(self):
        """Обновить UI при отключении."""
        self.status_label.setText("Статус: Отключено")
        self.connect_btn.setEnabled(True)
        self.disconnect_btn.setEnabled(False)
        self.output_check.setEnabled(False)
        self.disable_btn.setEnabled(False)
        self.output_check.setChecked(False)
        self.voltage_spin.setEnabled(False)
        self.current_spin.setEnabled(False)
        self._update_timer.stop()
        self.meas_voltage_label.setText("--- В")
        self.meas_current_label.setText("--- мкА")

    def apply_settings(self):
        """Применить заданные напряжение и ток и активировать выход (команды 1+2)."""
        if not self.driver or not self.driver.is_connected():
            QMessageBox.warning(self, "Ошибка", "Устройство не подключено")
            return

        voltage = self.voltage_spin.value()
        current_ua = self.current_spin.value()

        # Преобразование в коды
        code_u = int(voltage * self.kv)
        code_i = int(current_ua * self.ki)

        # Ограничение 16 бит
        code_u = max(0, min(65535, code_u))
        code_i = max(0, min(65535, code_i))

        try:
            # Команда [1] - передать коды напряжения и тока
            data = struct.pack('<HH', code_i, code_u)  # ток младшим? Согласно протоколу: сначала 2 байта тока, потом 2 байта напряжения
            self.driver.send_command(1, data)
            # Команда [2] - активация
            self.driver.send_command(2)
            self.output_check.setChecked(True)
            self.log_message(f"Установлены параметры: U={voltage:.1f} В (код {code_u}), I={current_ua:.1f} мкА (код {code_i}). Выход активирован.")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось применить настройки: {str(e)}")

    def disable_output(self):
        """Отключить выход (команда 3)."""
        if not self.driver or not self.driver.is_connected():
            return
        try:
            self.driver.send_command(3)
            self.output_check.setChecked(False)
            self.log_message("Выход отключен командой [3].")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось отключить выход: {str(e)}")

    def toggle_output(self, enabled: bool):
        """Обработчик чекбокса: включить или выключить выход."""
        if enabled:
            # Если надо включить, вызываем apply_settings повторно (он активирует)
            self.apply_settings()
        else:
            self.disable_output()

    def toggle_continuous_update(self, checked: bool):
        """Включение/выключение периодического опроса измерений."""
        if not self.driver or not self.driver.is_connected():
            self.continuous_check.blockSignals(True)
            self.continuous_check.setChecked(False)
            self.continuous_check.blockSignals(False)
            return

        if checked:
            self._update_timer.start()
        else:
            self._update_timer.stop()

    def update_measurements(self):
        """Обновить поля измеренных значений (команда 5)."""
        if not self.driver or not self.driver.is_connected():
            return

        try:
            # Отправляем команду 5 и читаем 5 байт ответа
            response = self.driver.query_command(5, response_length=5)
            if len(response) >= 5:
                # Байты: [1-2] ток (16 бит), [3-4] напряжение (16 бит), [5] = 13
                code_i = struct.unpack('<H', response[0:2])[0]
                code_u = struct.unpack('<H', response[2:4])[0]
                voltage = code_u / self.kv
                current_ua = code_i / self.ki
                self.meas_voltage_label.setText(f"{voltage:.1f} В")
                self.meas_current_label.setText(f"{current_ua:.1f} мкА")
        except Exception as e:
            self.log_message(f"Ошибка измерения: {str(e)}")

    def log_message(self, text: str):
        """Добавить сообщение в лог с меткой времени."""
        timestamp = time.strftime("%H:%M:%S")
        self.log_text.append(f"[{timestamp}] {text}")

    # ---------- Реализация методов BaseDevicePanel ----------
    def get_device_id(self) -> str:
        return f"Mantigora_{self.port_combo.currentText()}"

    def get_measurement(self) -> dict:
        if self.driver and self.driver.is_connected():
            try:
                resp = self.driver.query_command(5, response_length=5)
                if len(resp) >= 5:
                    code_i = struct.unpack('<H', resp[0:2])[0]
                    code_u = struct.unpack('<H', resp[2:4])[0]
                    return {
                        'voltage': code_u / self.kv,
                        'current': code_i / self.ki,
                        'output_enabled': self.output_check.isChecked()
                    }
            except Exception:
                pass
        return {'voltage': None, 'current': None, 'output_enabled': False}

    def closeEvent(self, event):
        """Освобождение ресурсов при закрытии."""
        if self._update_timer:
            self._update_timer.stop()
        self.disconnect_device()
        super().closeEvent(event)