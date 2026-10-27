"""
Автор: m.yablokov
rigol_panel.py

Панель устройства Rigol DM3068 (мультиметр) для UniversalMonitor.

Исправления (22.04.2026):
  - from base_device_panel → from panels.base_device_panel

Изменения (27.10.2026):
  - Режим измерения тока: выбор входа «200 мА» / «10 А» и диапазона из списка.
  - Чекбокс «мВ / мА» заменён на числовой коэффициент (показание * коэффициент)
    и необязательное поле единицы измерения.
  - Драйвер: Lock на обращения к прибору (опрос и настройка шли из разных потоков),
    NPLC / autozero отправляются только для режимов, где они есть,
    CONT отправляется без параметра диапазона.
  - В data_updated теперь уходит значение уже с коэффициентом.
  - Ответ-перегрузка прибора (9.9E37) не пишется в историю.
  - Убраны неиспользуемые импорты.
  - NPLC: целочисленный спинбокс заменён списком допустимых значений (0.02 ... 100).
"""

import math
import threading
from typing import Dict, Any, Optional

import pyvisa
from PyQt5.QtWidgets import (
    QLabel, QLineEdit, QComboBox, QDoubleSpinBox,
    QCheckBox, QPushButton, QHBoxLayout,
    QMessageBox, QWidget
)
from PyQt5.QtCore import QThread, pyqtSignal

from panels.base_device_panel import BaseDevicePanel


# ================== Драйвер ==================
class RigolDM3068:
    # Режим -> SCPI-функция
    FUNC_MAP = {
        'DCV': 'VOLT:DC',
        'ACV': 'VOLT:AC',
        'DCI': 'CURR:DC',
        'ACI': 'CURR:AC',
        'RES2W': 'RES',
        'RES4W': 'FRES',
        'FREQ': 'FREQ',
        'PER': 'PER',
        'CAP': 'CAP',
        'CONT': 'CONT'
    }
    # Режимы, у которых есть NPLC и autozero
    NPLC_MODES = {'DCV', 'DCI', 'RES2W', 'RES4W'}
    # Режимы, у которых нет параметра диапазона
    NO_RANGE_MODES = {'CONT'}

    def __init__(self, ip_address='192.168.0.125'):
        self.ip_address = ip_address
        self.visa_address = f'TCPIP0::{ip_address}::INSTR'
        self.rm = None
        self.device = None
        self.is_connected = False
        self.current_mode = 'DCV'
        # Один VISA-сеанс: опрос (поток) и настройка (GUI) не должны перемешиваться
        self._lock = threading.Lock()

    def connect(self):
        try:
            self.rm = pyvisa.ResourceManager()
            self.device = self.rm.open_resource(self.visa_address)
            self.device.timeout = 10000
            self.device.write_termination = '\n'
            self.device.read_termination = '\n'
            idn = self.device.query('*IDN?')
            self.device.write('CMDSET AGILENT')
            self.is_connected = True
            return True, idn.strip()
        except Exception as e:
            self.is_connected = False
            return False, str(e)

    def disconnect(self):
        with self._lock:
            if self.device:
                try:
                    self.device.close()
                except Exception:
                    pass
                self.device = None
            self.is_connected = False

    def configure_measurement(self, mode, range_val=None, nplc=10):
        if not self.is_connected:
            raise ConnectionError("Device not connected")
        func = self.FUNC_MAP[mode]
        with self._lock:
            if mode in self.NO_RANGE_MODES:
                self.device.write(f'CONF:{func}')
            elif range_val is not None:
                self.device.write(f'CONF:{func} {range_val:g}')
            else:
                self.device.write(f'CONF:{func} AUTO')
            if mode in self.NPLC_MODES:
                self.device.write(f'SENS:{func}:NPLC {nplc:g}')
                self.device.write(f'SENS:{func}:ZERO:AUTO OFF')
            self.current_mode = mode

    def get_reading(self) -> Optional[float]:
        if not self.is_connected:
            return None
        with self._lock:
            return float(self.device.query('READ?'))


# ================== Поток опроса ==================
class RigolPollingThread(QThread):
    new_value = pyqtSignal(float)
    error_occurred = pyqtSignal(str)

    def __init__(self, driver):
        super().__init__()
        self.driver = driver
        self.running = False
        self.interval = 2.0

    def run(self):
        self.running = True
        while self.running:
            try:
                value = self.driver.get_reading()
                if value is not None:
                    self.new_value.emit(value)
                else:
                    self.error_occurred.emit("Нет данных от прибора")
            except Exception as e:
                self.error_occurred.emit(f"Ошибка сбора данных: {str(e)}")
                self.running = False
                break
            self.msleep(int(self.interval * 1000))

    def stop(self):
        self.running = False
        self.wait()


# ================== Панель устройства ==================
class RigolPanel(BaseDevicePanel):
    MODE_CODES = {
        'Напряжение DC': 'DCV',
        'Напряжение AC': 'ACV',
        'Ток DC': 'DCI',
        'Ток AC': 'ACI',
        'Сопротивление 2W': 'RES2W',
        'Сопротивление 4W': 'RES4W',
        'Частота': 'FREQ',
        'Период': 'PER',
        'Ёмкость': 'CAP',
        'Прозвонка': 'CONT'
    }

    BASE_UNITS = {
        'DCV': 'В', 'ACV': 'В',
        'DCI': 'А', 'ACI': 'А',
        'RES2W': 'Ом', 'RES4W': 'Ом',
        'FREQ': 'Гц', 'PER': 'с',
        'CAP': 'Ф', 'CONT': 'Ом'
    }

    # --- Токовые режимы: какие диапазоны (в амперах) доступны на каком входе ---
    # ВАЖНО: сверьте списки с руководством по DM3068. Правятся только здесь.
    TERMINAL_200MA = '200 мА'
    TERMINAL_10A = '10 А'
    CURRENT_RANGES = {
        'DCI': {
            TERMINAL_200MA: [0.0002, 0.002, 0.02, 0.2],
            TERMINAL_10A: [1.0, 10.0],
        },
        'ACI': {
            TERMINAL_200MA: [0.02, 0.2],
            TERMINAL_10A: [1.0, 10.0],
        },
    }

    # Режимы, где ручной диапазон не выбирается (всегда авто)
    AUTO_ONLY_MODES = {'FREQ', 'PER', 'CAP', 'CONT'}
    # Режимы, где работает NPLC
    NPLC_MODES = RigolDM3068.NPLC_MODES
    # Допустимые значения NPLC у DM3068 (сверьте с руководством, правится только здесь)
    NPLC_VALUES = [0.02, 0.2, 1, 10, 100]
    NPLC_DEFAULT = 10

    # Автоприставка к единице по коэффициенту: (коэффициент, приставка)
    PREFIX_BY_SCALE = [
        (1e-6, 'М'), (1e-3, 'к'),
        (1e3, 'м'), (1e6, 'мк'), (1e9, 'н'),
    ]

    # Значения прибора выше этого порога считаем «перегрузкой» (прибор отдаёт 9.9E37)
    OVERLOAD_THRESHOLD = 9.0e37

    def __init__(self, device_name: str, parent=None):
        super().__init__("Rigol", device_name, parent)
        self.driver: Optional[RigolDM3068] = None
        self.poll_thread: Optional[RigolPollingThread] = None
        # Режим, который реально применён (под этим именем идут данные)
        self.active_mode_readable = 'Напряжение DC'
        # Коэффициент, на который умножается показание
        self.scale_factor = 1.0
        # Что выбрал пользователь в чекбоксе «Авто» (в токовых режимах он блокируется)
        self._user_auto = True
        self._overload_logged = False
        self._build_specific_ui()
        self._update_parameter_display()

    # ---------- Построение интерфейса ----------
    def _build_specific_ui(self):
        self.ip_edit = QLineEdit("192.168.0.125")
        self.add_setting_row("IP адрес:", self.ip_edit)

        # Режим измерения
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(list(self.MODE_CODES.keys()))
        self.add_setting_row("Режим:", self.mode_combo)

        # Вход тока (активен только в режимах «Ток DC» / «Ток AC»)
        self.terminal_combo = QComboBox()
        self.terminal_combo.addItems([self.TERMINAL_200MA, self.TERMINAL_10A])
        self.add_setting_row("Вход тока:", self.terminal_combo)

        # Подсказка про физическое подключение щупа
        self.terminal_hint = QLabel("")
        self.terminal_hint.setStyleSheet("color: #d29922; font-weight: bold;")
        self.add_setting_widget(self.terminal_hint)

        # Диапазон: «Авто» + ручной ввод (для U, R) + список (для токов)
        range_layout = QHBoxLayout()
        self.auto_range_check = QCheckBox("Авто")
        self.auto_range_check.setChecked(True)
        self.range_spin = QDoubleSpinBox()
        self.range_spin.setRange(0.001, 1000.0)
        self.range_spin.setValue(10.0)
        self.range_spin.setEnabled(False)
        self.range_combo = QComboBox()
        range_layout.addWidget(self.auto_range_check)
        range_layout.addWidget(self.range_spin)
        range_layout.addWidget(self.range_combo)
        range_widget = QWidget()
        range_widget.setLayout(range_layout)
        self.add_setting_row("Диапазон:", range_widget)

        self.nplc_combo = QComboBox()
        for v in self.NPLC_VALUES:
            self.nplc_combo.addItem(f"{v:g}", v)
        self.nplc_combo.setCurrentIndex(self.NPLC_VALUES.index(self.NPLC_DEFAULT))
        self.add_setting_row("NPLC:", self.nplc_combo)

        # Коэффициент и единица измерения
        scale_layout = QHBoxLayout()
        self.scale_edit = QLineEdit("1")
        self.scale_edit.setMaximumWidth(90)
        self.scale_edit.setToolTip(
            "Показание прибора умножается на это число.\n"
            "1000 — увеличить в 1000 раз (А → мА), 0,001 — уменьшить.\n"
            "Можно вводить с запятой или точкой."
        )
        self.unit_edit = QLineEdit("")
        self.unit_edit.setMaximumWidth(70)
        self.unit_edit.setPlaceholderText("авто")
        self.unit_edit.setToolTip(
            "Единица измерения после умножения.\n"
            "Пусто — подставится автоматически (при 1000 → «мА», «мВ» и т.д.)."
        )
        scale_layout.addWidget(self.scale_edit)
        scale_layout.addWidget(QLabel("Ед.:"))
        scale_layout.addWidget(self.unit_edit)
        scale_widget = QWidget()
        scale_widget.setLayout(scale_layout)
        self.add_setting_row("Коэффициент:", scale_widget)

        self.apply_config_btn = QPushButton("Применить настройки")
        self.apply_config_btn.clicked.connect(self._apply_measurement_config)
        self.add_setting_widget(self.apply_config_btn)

        self.add_setting_widget(QLabel(""))

        poll_layout = QHBoxLayout()
        poll_layout.addWidget(QLabel("Интервал (сек):"))
        self.poll_interval_spin = QDoubleSpinBox()
        self.poll_interval_spin.setRange(0.2, 60.0)
        self.poll_interval_spin.setValue(self.poll_interval)
        self.poll_interval_spin.valueChanged.connect(self._on_poll_interval_changed)
        poll_layout.addWidget(self.poll_interval_spin)
        poll_widget = QWidget()
        poll_widget.setLayout(poll_layout)
        self.add_setting_row("Опрос:", poll_widget)

        btn_layout = QHBoxLayout()
        self.start_btn = QPushButton("▶ Старт")
        self.start_btn.clicked.connect(self.start_polling)
        self.stop_btn = QPushButton("■ Стоп")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_polling)
        btn_layout.addWidget(self.start_btn)
        btn_layout.addWidget(self.stop_btn)
        btn_widget = QWidget()
        btn_widget.setLayout(btn_layout)
        self.add_setting_widget(btn_widget)

        # Сигналы подключаем после того, как все виджеты созданы и заполнены
        self.mode_combo.currentTextChanged.connect(self._on_mode_changed)
        self.terminal_combo.currentTextChanged.connect(self._on_terminal_changed)
        self.auto_range_check.toggled.connect(self._on_auto_range_toggled)
        self.scale_edit.editingFinished.connect(self._on_scale_changed)
        self.unit_edit.editingFinished.connect(self._on_unit_changed)

        # Начальное состояние элементов диапазона
        self._populate_current_ranges()
        self._update_range_controls()

    # ---------- Вспомогательные методы ----------
    def _mode_code(self) -> str:
        """Код выбранного в списке режима (DCV, DCI, ...)."""
        return self.MODE_CODES.get(self.mode_combo.currentText(), 'DCV')

    @staticmethod
    def _fmt_current(amps: float) -> str:
        """0.0002 -> '200 мкА', 0.2 -> '200 мА', 10 -> '10 А'."""
        if amps >= 1:
            return f"{amps:g} А"
        if amps >= 1e-3:
            return f"{amps * 1e3:g} мА"
        return f"{amps * 1e6:g} мкА"

    @staticmethod
    def _parse_scale(text: str) -> Optional[float]:
        """'0,001' / '1000' -> float. Некорректный ввод и 0 -> None."""
        try:
            value = float(text.strip().replace(',', '.'))
        except ValueError:
            return None
        if not math.isfinite(value) or value == 0:
            return None
        return value

    def _resolve_unit(self, code: str) -> str:
        """Единица измерения с учётом коэффициента."""
        custom = self.unit_edit.text().strip()
        if custom:
            return custom
        base = self.BASE_UNITS.get(code, '')
        for factor, prefix in self.PREFIX_BY_SCALE:
            if math.isclose(self.scale_factor, factor, rel_tol=1e-9):
                return prefix + base
        return base

    def _populate_current_ranges(self):
        """Заполняет список диапазонов тока под выбранные режим и вход."""
        code = self._mode_code()
        ranges = self.CURRENT_RANGES.get(code, {}).get(self.terminal_combo.currentText(), [])
        self.range_combo.blockSignals(True)
        self.range_combo.clear()
        for r in ranges:
            self.range_combo.addItem(self._fmt_current(r), r)
        if ranges:
            # По умолчанию — самый грубый диапазон (самый безопасный)
            self.range_combo.setCurrentIndex(len(ranges) - 1)
        self.range_combo.blockSignals(False)

    def _select_current_range(self, value: float):
        for i in range(self.range_combo.count()):
            if math.isclose(self.range_combo.itemData(i), value, rel_tol=1e-9):
                self.range_combo.setCurrentIndex(i)
                return

    def _update_range_controls(self):
        """Включает/выключает элементы диапазона, NPLC и подсказку под текущий режим."""
        code = self._mode_code()
        is_current = code in self.CURRENT_RANGES
        auto_only = code in self.AUTO_ONLY_MODES

        self.terminal_combo.setEnabled(is_current)
        self.range_combo.setVisible(is_current)
        self.range_combo.setEnabled(is_current)
        self.range_spin.setVisible(not is_current)

        # Чекбокс «Авто»: в токовых режимах всегда выкл. и заблокирован,
        # в FREQ/PER/CAP/CONT всегда вкл. и заблокирован, иначе — как выбрал пользователь
        self.auto_range_check.blockSignals(True)
        if is_current:
            self.auto_range_check.setChecked(False)
            self.auto_range_check.setEnabled(False)
        elif auto_only:
            self.auto_range_check.setChecked(True)
            self.auto_range_check.setEnabled(False)
        else:
            self.auto_range_check.setChecked(self._user_auto)
            self.auto_range_check.setEnabled(True)
        self.auto_range_check.blockSignals(False)

        self.range_spin.setEnabled(
            not is_current and not auto_only and not self.auto_range_check.isChecked()
        )
        self.nplc_combo.setEnabled(code in self.NPLC_MODES)

        if is_current:
            self.terminal_hint.setText(
                f"⚠ Щуп должен быть в клемме «{self.terminal_combo.currentText()}»"
            )
        else:
            self.terminal_hint.setText("")

    def _update_parameter_display(self):
        """Сообщает базовой панели имя параметра и его единицу измерения."""
        name = self.active_mode_readable
        code = self.MODE_CODES.get(name, 'DCV')
        self.set_parameters({name: {"unit": self._resolve_unit(code)}})

    # ---------- Обработчики элементов ----------
    def _on_mode_changed(self, mode_readable: str):
        self._populate_current_ranges()
        self._update_range_controls()
        # Пока прибор не подключён — имя параметра меняем сразу.
        # После подключения новый режим вступает в силу по кнопке «Применить».
        if not self.is_connected:
            self.active_mode_readable = mode_readable
            self._update_parameter_display()

    def _on_terminal_changed(self, _text: str):
        self._populate_current_ranges()
        self._update_range_controls()

    def _on_auto_range_toggled(self, checked: bool):
        self._user_auto = checked
        self._update_range_controls()

    def _on_scale_changed(self, log: bool = True):
        text = self.scale_edit.text()
        value = self._parse_scale(text)
        if value is None:
            self.scale_edit.setStyleSheet("border: 1px solid #f85149;")
            self.log_event(
                f"Некорректный коэффициент '{text}' — остаётся {self.scale_factor:g}"
            )
            return
        self.scale_edit.setStyleSheet("")
        self.scale_factor = value
        self._update_parameter_display()
        if log:
            self.log_event(f"Коэффициент: {value:g}")

    def _on_unit_changed(self):
        self._update_parameter_display()

    # ---------- Применение настроек к прибору ----------
    def _apply_measurement_config(self):
        if not self.is_connected or not self.driver:
            QMessageBox.warning(self, "Внимание", "Устройство не подключено")
            return
        mode_readable = self.mode_combo.currentText()
        mode_code = self.MODE_CODES.get(mode_readable, 'DCV')
        nplc = self.nplc_combo.currentData()

        if mode_code in self.CURRENT_RANGES:
            range_val = self.range_combo.currentData()
            if range_val is None:
                QMessageBox.warning(self, "Внимание", "Не выбран диапазон тока")
                return
            terminal = self.terminal_combo.currentText()
            range_desc = f"{self._fmt_current(range_val)}, клемма {terminal}"
        elif mode_code in self.AUTO_ONLY_MODES or self.auto_range_check.isChecked():
            range_val = None
            range_desc = "авто"
        else:
            range_val = self.range_spin.value()
            range_desc = f"{range_val:g}"

        try:
            self.driver.configure_measurement(mode_code, range_val, nplc)
            self.active_mode_readable = mode_readable
            nplc_desc = f"{nplc:g}" if mode_code in self.NPLC_MODES else "—"
            self.log_event(
                f"Режим: {mode_readable}, диапазон: {range_desc}, NPLC: {nplc_desc}"
            )
            self._update_parameter_display()
        except Exception as e:
            self.log_event(f"Ошибка конфигурации: {e}")
            QMessageBox.critical(self, "Ошибка", f"Не удалось применить настройки:\n{e}")

    # ---------- Подключение / опрос ----------
    def connect_device(self) -> bool:
        ip = self.ip_edit.text().strip()
        self.driver = RigolDM3068(ip)
        success, info = self.driver.connect()
        if success:
            self.is_connected = True
            self.log_event(f"Подключено к {info}")
            self._apply_measurement_config()
            self.status_label.setText("● Подключено")
            self.status_label.setStyleSheet("color: #3fb950; font-weight: bold;")
            self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(False)
            self.start_polling()
        else:
            self.log_event(f"Ошибка подключения: {info}")
        return success

    def disconnect_device(self):
        self.stop_polling()
        if self.driver:
            self.driver.disconnect()
            self.driver = None
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)

    def read_device_values(self) -> Dict[str, float]:
        return {}

    def start_polling(self):
        if not self.is_connected:
            QMessageBox.warning(self, "Внимание", "Устройство не подключено")
            return
        if self.poll_thread and self.poll_thread.isRunning():
            return
        self.poll_thread = RigolPollingThread(self.driver)
        self.poll_thread.interval = self.poll_interval
        self.poll_thread.new_value.connect(self._on_new_value)
        self.poll_thread.error_occurred.connect(self._on_poll_error)
        self.poll_thread.start()
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.log_event("Опрос запущен")

    def stop_polling(self):
        if self.poll_thread:
            self.poll_thread.stop()
            self.poll_thread = None
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.log_event("Опрос остановлен")

    def _on_new_value(self, value: float):
        # Прибор отдаёт 9.9E37 при перегрузке / выходе за диапазон — в историю не пишем
        if abs(value) >= self.OVERLOAD_THRESHOLD:
            if not self._overload_logged:
                self.log_event("Перегрузка / значение вне диапазона (прибор вернул 9.9E37)")
                self._overload_logged = True
            return
        self._overload_logged = False

        # Имя параметра — режим, который реально применён на приборе
        param_name = self.active_mode_readable
        code = self.MODE_CODES.get(param_name, 'DCV')
        display_value = value * self.scale_factor
        unit = self._resolve_unit(code)

        self.update_value_display(param_name, display_value)
        self.data_updated.emit(self.device_name, param_name, display_value, unit)
        self.log_measurement({param_name: display_value})

    def _on_poll_error(self, err_msg: str):
        self.log_event(err_msg)
        self.stop_polling()
        QMessageBox.critical(self, "Ошибка сбора данных", err_msg)

    # ---------- Сохранение / загрузка конфигурации ----------
    def get_config(self) -> Dict[str, Any]:
        config = super().get_config()
        config.update({
            "ip": self.ip_edit.text(),
            "mode": self.mode_combo.currentText(),
            "auto_range": self._user_auto,
            "range": self.range_spin.value(),
            "nplc": self.nplc_combo.currentData(),
            "current_input": self.terminal_combo.currentText(),
            "current_range": self.range_combo.currentData(),
            "scale": self.scale_factor,
            "unit_override": self.unit_edit.text().strip(),
        })
        return config

    def apply_config(self, config: Dict[str, Any]):
        super().apply_config(config)
        self.ip_edit.setText(config.get("ip", "192.168.0.125"))

        mode = config.get("mode", "Напряжение DC")
        idx = self.mode_combo.findText(mode)
        if idx >= 0:
            self.mode_combo.setCurrentIndex(idx)

        terminal = config.get("current_input", self.TERMINAL_200MA)
        tidx = self.terminal_combo.findText(terminal)
        if tidx >= 0:
            self.terminal_combo.setCurrentIndex(tidx)

        self._user_auto = bool(config.get("auto_range", True))
        self.range_spin.setValue(config.get("range", 10.0))
        # Старые конфиги хранили NPLC целым числом 1..100 — берём ближайшее допустимое
        saved_nplc = float(config.get("nplc", self.NPLC_DEFAULT))
        nearest = min(self.NPLC_VALUES, key=lambda v: abs(v - saved_nplc))
        self.nplc_combo.setCurrentIndex(self.NPLC_VALUES.index(nearest))

        # Список диапазонов тока и выбранный в нём диапазон
        self._populate_current_ranges()
        saved_range = config.get("current_range")
        if saved_range is not None:
            self._select_current_range(float(saved_range))

        # Коэффициент. Старые конфиги: milli_mode=True равно коэффициенту 1000
        if "scale" in config:
            self.scale_edit.setText(f"{config['scale']:g}")
        elif config.get("milli_mode", False):
            self.scale_edit.setText("1000")
        else:
            self.scale_edit.setText("1")
        self.unit_edit.setText(config.get("unit_override", ""))
        self._on_scale_changed(log=False)

        self._update_range_controls()
        self.active_mode_readable = self.mode_combo.currentText()
        self._update_parameter_display()