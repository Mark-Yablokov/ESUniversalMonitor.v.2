"""
Автор: m.yablokov
rigol_panel.py

Панель устройства Rigol DM3068 (мультиметр) для UniversalMonitor.
Добавлена ручная опция переключения в милли-единицы (мВ/мА).

Исправления (22.04.2026):
  - from base_device_panel → from panels.base_device_panel
"""

import time
import csv
from datetime import datetime
from typing import Dict, Any, Optional, Tuple

import pyvisa
from PyQt5.QtWidgets import (
    QLabel, QLineEdit, QComboBox, QDoubleSpinBox,
    QCheckBox, QSpinBox, QPushButton, QHBoxLayout,
    QMessageBox, QWidget, QApplication
)
from PyQt5.QtCore import QThread, pyqtSignal

from panels.base_device_panel import BaseDevicePanel


# ================== Драйвер ==================
class RigolDM3068:
    def __init__(self, ip_address='192.168.0.125'):
        self.ip_address = ip_address
        self.visa_address = f'TCPIP0::{ip_address}::INSTR'
        self.rm = None
        self.device = None
        self.is_connected = False
        self.current_mode = 'DCV'

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
        if self.device:
            self.device.close()
            self.is_connected = False

    def configure_measurement(self, mode, range_val=None, nplc=10):
        if not self.is_connected:
            raise ConnectionError("Device not connected")
        cmd_map = {
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
        func = cmd_map[mode]
        if range_val is not None:
            self.device.write(f'CONF:{func} {range_val}')
        else:
            self.device.write(f'CONF:{func} AUTO')
        self.device.write(f'SENS:{func}:NPLC {nplc}')
        self.device.write(f'SENS:{func}:ZERO:AUTO OFF')
        self.current_mode = mode

    def get_reading(self) -> Optional[float]:
        if not self.is_connected:
            return None
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

    def __init__(self, device_name: str, parent=None):
        super().__init__("Rigol", device_name, parent)
        self.driver: Optional[RigolDM3068] = None
        self.poll_thread: Optional[RigolPollingThread] = None
        self.current_mode_readable = 'Напряжение DC'
        self.milli_mode = False
        self._build_specific_ui()
        self._update_parameter_display()

    def _build_specific_ui(self):
        self.ip_edit = QLineEdit("192.168.0.125")
        self.add_setting_row("IP адрес:", self.ip_edit)

        mode_layout = QHBoxLayout()
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(list(self.MODE_CODES.keys()))
        self.mode_combo.currentTextChanged.connect(self._on_mode_changed)
        mode_layout.addWidget(self.mode_combo)
        self.milli_check = QCheckBox("мВ / мА")
        self.milli_check.toggled.connect(self._on_milli_toggled)
        mode_layout.addWidget(self.milli_check)
        mode_widget = QWidget()
        mode_widget.setLayout(mode_layout)
        self.add_setting_row("Режим:", mode_widget)

        range_layout = QHBoxLayout()
        self.auto_range_check = QCheckBox("Авто")
        self.auto_range_check.setChecked(True)
        self.auto_range_check.toggled.connect(self._on_auto_range_toggled)
        self.range_spin = QDoubleSpinBox()
        self.range_spin.setRange(0.001, 1000.0)
        self.range_spin.setValue(10.0)
        self.range_spin.setEnabled(False)
        range_layout.addWidget(self.auto_range_check)
        range_layout.addWidget(self.range_spin)
        range_widget = QWidget()
        range_widget.setLayout(range_layout)
        self.add_setting_row("Диапазон:", range_widget)

        self.nplc_spin = QSpinBox()
        self.nplc_spin.setRange(1, 100)
        self.nplc_spin.setValue(10)
        self.add_setting_row("NPLC:", self.nplc_spin)

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

    def _update_parameter_display(self):
        mode_readable = self.mode_combo.currentText()
        code = self.MODE_CODES.get(mode_readable, 'DCV')
        base_unit = self.BASE_UNITS.get(code, '')
        if self.milli_mode and code in ('DCV', 'ACV', 'DCI', 'ACI'):
            unit = 'м' + base_unit
        else:
            unit = base_unit
        self.set_parameters({mode_readable: {"unit": unit}})

    def _on_mode_changed(self, mode_readable: str):
        self.current_mode_readable = mode_readable
        self._update_parameter_display()

    def _on_milli_toggled(self, checked: bool):
        self.milli_mode = checked
        self._update_parameter_display()

    def _on_auto_range_toggled(self, checked: bool):
        self.range_spin.setEnabled(not checked)

    def _apply_measurement_config(self):
        if not self.is_connected or not self.driver:
            QMessageBox.warning(self, "Внимание", "Устройство не подключено")
            return
        mode_readable = self.mode_combo.currentText()
        mode_code = self.MODE_CODES.get(mode_readable, 'DCV')
        auto_range = self.auto_range_check.isChecked()
        range_val = None if auto_range else self.range_spin.value()
        nplc = self.nplc_spin.value()
        try:
            self.driver.configure_measurement(mode_code, range_val, nplc)
            self.log_event(f"Режим: {mode_readable}, диапазон: {'авто' if auto_range else range_val}, NPLC: {nplc}")
            self._update_parameter_display()
        except Exception as e:
            self.log_event(f"Ошибка конфигурации: {e}")
            QMessageBox.critical(self, "Ошибка", f"Не удалось применить настройки:\n{e}")

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
        param_name = self.mode_combo.currentText()
        unit = self.BASE_UNITS.get(self.MODE_CODES.get(param_name, 'DCV'), '')
        if self.milli_mode and self.driver.current_mode in ('DCV', 'ACV', 'DCI', 'ACI'):
            display_value = value * 1000.0
            unit = 'м' + unit
        else:
            display_value = value
        self.update_value_display(param_name, display_value)
        self.data_updated.emit(self.device_name, param_name, value, unit)
        self.log_measurement({param_name: display_value})

    def _on_poll_error(self, err_msg: str):
        self.log_event(err_msg)
        self.stop_polling()
        QMessageBox.critical(self, "Ошибка сбора данных", err_msg)

    def get_config(self) -> Dict[str, Any]:
        config = super().get_config()
        config.update({
            "ip": self.ip_edit.text(),
            "mode": self.mode_combo.currentText(),
            "auto_range": self.auto_range_check.isChecked(),
            "range": self.range_spin.value(),
            "nplc": self.nplc_spin.value(),
            "milli_mode": self.milli_mode,
        })
        return config

    def apply_config(self, config: Dict[str, Any]):
        super().apply_config(config)
        self.ip_edit.setText(config.get("ip", "192.168.0.125"))
        mode = config.get("mode", "Напряжение DC")
        idx = self.mode_combo.findText(mode)
        if idx >= 0:
            self.mode_combo.setCurrentIndex(idx)
        self.auto_range_check.setChecked(config.get("auto_range", True))
        self.range_spin.setValue(config.get("range", 10.0))
        self.nplc_spin.setValue(config.get("nplc", 10))
        self.milli_mode = config.get("milli_mode", False)
        self.milli_check.setChecked(self.milli_mode)
        self._update_parameter_display()
