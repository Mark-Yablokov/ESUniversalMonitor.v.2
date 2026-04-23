"""
modbus_panel.py  v2.0.0 (17.04.2026)

Исправления (22.04.2026):
  - from base_device_panel → from panels.base_device_panel
  - import modBus → from drivers import modBus
  - Убран sys.path.append хак
"""

import struct
from typing import Dict, Any, Optional, List

from PyQt5.QtWidgets import (
    QLabel, QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox,
    QCheckBox, QPushButton, QHBoxLayout, QVBoxLayout,
    QMessageBox, QWidget, QTableWidget, QTableWidgetItem,
    QHeaderView, QDialog, QFormLayout, QDialogButtonBox,
    QInputDialog, QAbstractItemView
)
from PyQt5.QtCore import QThread, pyqtSignal, Qt

from panels.base_device_panel import BaseDevicePanel
from drivers import modBus


# ============================================================
# Диалог настройки одного регистра
# ============================================================
class ModbusRegisterDialog(QDialog):
    def __init__(self, register: Optional[Dict] = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Настройка регистра Modbus")
        self.setMinimumWidth(480)
        self.setStyleSheet("""
            QDialog { background-color: #161b22; }
            QLabel  { color: #e6edf3; }
            QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {
                background-color: #0d1117; color: #e6edf3;
                border: 1px solid #30363d; padding: 4px; border-radius: 3px;
            }
            QPushButton {
                background-color: #30363d; color: #e6edf3;
                border: none; padding: 6px 14px;
                border-radius: 4px; font-weight: bold;
            }
            QPushButton:hover { background-color: #00d4ff; color: #0d1117; }
        """)
        self.register = register if register else {}
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.name_edit = QLineEdit(self.register.get("name", ""))
        form.addRow("Имя параметра:", self.name_edit)

        self.addr_spin = QSpinBox()
        self.addr_spin.setRange(0, 65535)
        self.addr_spin.setValue(self.register.get("addr", 8193))
        form.addRow("Адрес регистра:", self.addr_spin)
        form.addRow("", QLabel("Адрес передаётся как есть (без вычитания 1)"))

        self.type_combo = QComboBox()
        self.type_combo.addItems(["int16", "uint16", "int32", "uint32", "float32"])
        self.type_combo.setCurrentText(self.register.get("type", "float32"))
        self.type_combo.currentTextChanged.connect(self._on_type_changed)
        form.addRow("Тип данных:", self.type_combo)

        self.count_spin = QSpinBox()
        self.count_spin.setRange(1, 10)
        default_count = 2 if self.register.get("type", "float32") in ("int32", "uint32", "float32") else 1
        self.count_spin.setValue(self.register.get("count", default_count))
        form.addRow("Кол-во регистров:", self.count_spin)

        self.byte_order_combo = QComboBox()
        self.byte_order_combo.addItems(["3-4-1-2", "4-3-2-1", "2-1-4-3", "1-2-3-4"])
        self.byte_order_combo.setCurrentText(self.register.get("byte_order", "3-4-1-2"))
        hint = QLabel(
            "3-4-1-2 = Word Swap (рекомендуется для ENMV-3)\n"
            "4-3-2-1 = Big Endian\n"
            "1-2-3-4 = Little Endian\n"
            "2-1-4-3 = Byte Swap"
        )
        hint.setStyleSheet("color: #8b949e; font-size: 10px;")
        form.addRow("Порядок байт:", self.byte_order_combo)
        form.addRow("", hint)

        self.scale_spin = QDoubleSpinBox()
        self.scale_spin.setRange(-1e9, 1e9)
        self.scale_spin.setValue(self.register.get("scale", 1.0))
        self.scale_spin.setDecimals(6)
        form.addRow("Масштаб (делитель):", self.scale_spin)
        form.addRow("", QLabel("Результат = сырое_значение / масштаб"))

        self.unit_edit = QLineEdit(self.register.get("unit", ""))
        form.addRow("Единицы:", self.unit_edit)

        layout.addLayout(form)

        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def _on_type_changed(self, dtype: str):
        self.count_spin.setValue(2 if dtype in ("int32", "uint32", "float32") else 1)

    def get_register(self) -> Dict[str, Any]:
        return {
            "name": self.name_edit.text().strip(),
            "addr": self.addr_spin.value(),
            "type": self.type_combo.currentText(),
            "count": self.count_spin.value(),
            "byte_order": self.byte_order_combo.currentText(),
            "scale": self.scale_spin.value(),
            "unit": self.unit_edit.text().strip(),
        }


# ============================================================
# Поток опроса
# ============================================================
class ModbusPollingThread(QThread):
    new_values    = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)

    def __init__(self, driver, registers: List[Dict], slave_id: int):
        super().__init__()
        self.driver    = driver
        self.registers = registers
        self.slave_id  = slave_id
        self._running  = False
        self.interval  = 2.0

    def run(self):
        self._running = True
        while self._running:
            try:
                values = self._poll_all()
                self.new_values.emit(values)
            except Exception as e:
                self.error_occurred.emit(str(e))
                break
            elapsed = 0.0
            step = 0.05
            while self._running and elapsed < self.interval:
                self.msleep(int(step * 1000))
                elapsed += step

    def stop(self):
        self._running = False
        self.quit()
        self.wait(3000)

    def _poll_all(self) -> dict:
        values = {}
        for reg in self.registers:
            addr = reg["addr"]
            count = reg.get("count", 2 if reg["type"] in ("int32", "uint32", "float32") else 1)

            cmd = modBus.ModBus_Cmd.readHoldingRegisters(self.slave_id, addr, count)
            resp = self.driver.request(cmd, parse=True)

            if resp and "reg" in resp and len(resp["reg"]) >= count:
                raw = resp["reg"][:count]
                values[reg["name"]] = self._convert(raw, reg)
            else:
                values[reg["name"]] = None
        return values

    @staticmethod
    def _convert(raw_data: List[int], reg: Dict) -> Optional[float]:
        dtype      = reg["type"]
        byte_order = reg.get("byte_order", "3-4-1-2")
        scale      = reg.get("scale", 1.0)
        if scale == 0:
            scale = 1.0
        try:
            if dtype in ("int16", "uint16"):
                val = raw_data[0]
                if dtype == "int16" and val >= 0x8000:
                    val -= 0x10000
                return val / scale

            if len(raw_data) < 2:
                return None

            A, B = divmod(raw_data[0], 256)
            C, D = divmod(raw_data[1], 256)

            ORDER_MAP = {
                "4-3-2-1": bytes([A, B, C, D]),
                "1-2-3-4": bytes([D, C, B, A]),
                "2-1-4-3": bytes([B, A, D, C]),
                "3-4-1-2": bytes([C, D, A, B]),
            }
            ordered = ORDER_MAP.get(byte_order, bytes([A, B, C, D]))

            if dtype == "float32":
                val = struct.unpack('>f', ordered)[0]
            elif dtype == "uint32":
                val = int.from_bytes(ordered, byteorder='big', signed=False)
            else:
                val = int.from_bytes(ordered, byteorder='big', signed=True)

            return val / scale

        except Exception as e:
            print(f"[ModbusPanel] Ошибка конвертации '{reg.get('name')}': {e}")
            return None


# ============================================================
# Таблица регистров
# ============================================================
class RegisterTableWidget(QTableWidget):
    COLUMNS = ["Имя", "Адрес", "Тип", "Порядок байт", "Масштаб", "Ед."]

    def __init__(self, parent=None):
        super().__init__(0, len(self.COLUMNS), parent)
        self.setHorizontalHeaderLabels(self.COLUMNS)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for col in range(1, len(self.COLUMNS)):
            self.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self.setMaximumHeight(160)
        self.setStyleSheet("""
            QTableWidget {
                background-color: #0d1117; color: #e6edf3;
                gridline-color: #30363d; border: 1px solid #30363d;
            }
            QHeaderView::section {
                background-color: #161b22; color: #8b949e;
                border: none; padding: 3px;
            }
            QTableWidget::item:selected { background-color: #1f6feb; }
        """)

    def refresh(self, registers: List[Dict]):
        self.setRowCount(0)
        for reg in registers:
            row = self.rowCount()
            self.insertRow(row)
            values = [
                reg.get("name", ""),
                str(reg.get("addr", "")),
                reg.get("type", ""),
                reg.get("byte_order", ""),
                str(reg.get("scale", 1.0)),
                reg.get("unit", ""),
            ]
            for col, val in enumerate(values):
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignCenter)
                self.setItem(row, col, item)

    def selected_index(self) -> int:
        rows = self.selectedIndexes()
        return rows[0].row() if rows else -1


# ============================================================
# Основная панель Modbus
# ============================================================
class ModbusPanel(BaseDevicePanel):
    def __init__(self, device_name: str, parent=None):
        super().__init__("Modbus", device_name, parent)
        self.driver: Optional[object] = None
        self.poll_thread: Optional[ModbusPollingThread] = None
        self.registers: List[Dict] = []
        self.slave_id: int = 1
        self.poll_interval: float = 2.0
        self._build_specific_ui()
        self._refresh_reg_table()

    def _build_specific_ui(self):
        self.conn_type_combo = QComboBox()
        self.conn_type_combo.addItems(["Modbus TCP", "Modbus RTU over TCP", "Modbus RTU (COM)"])
        self.conn_type_combo.currentTextChanged.connect(self._on_conn_type_changed)
        self.add_setting_row("Тип подключения:", self.conn_type_combo)

        self.tcp_widget = QWidget()
        tcp_layout = QHBoxLayout(self.tcp_widget)
        tcp_layout.setContentsMargins(0, 0, 0, 0)
        self.ip_edit = QLineEdit("192.168.0.83")
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(5000)
        tcp_layout.addWidget(QLabel("IP:"))
        tcp_layout.addWidget(self.ip_edit)
        tcp_layout.addWidget(QLabel("Порт:"))
        tcp_layout.addWidget(self.port_spin)

        self.rtu_widget = QWidget()
        rtu_layout = QHBoxLayout(self.rtu_widget)
        rtu_layout.setContentsMargins(0, 0, 0, 0)
        self.port_edit = QLineEdit("COM1")
        self.baud_spin = QSpinBox()
        self.baud_spin.setRange(300, 115200)
        self.baud_spin.setValue(9600)
        rtu_layout.addWidget(QLabel("COM:"))
        rtu_layout.addWidget(self.port_edit)
        rtu_layout.addWidget(QLabel("Baud:"))
        rtu_layout.addWidget(self.baud_spin)

        self.add_setting_widget(self.tcp_widget)
        self.add_setting_widget(self.rtu_widget)

        self.slave_spin = QSpinBox()
        self.slave_spin.setRange(1, 247)
        self.slave_spin.setValue(1)
        self.add_setting_row("Slave ID:", self.slave_spin)

        self.reg_table = RegisterTableWidget()
        self.add_setting_widget(self.reg_table)

        reg_btn_layout = QHBoxLayout()
        self.add_reg_btn  = QPushButton("＋ Добавить")
        self.edit_reg_btn = QPushButton("✎ Изменить")
        self.del_reg_btn  = QPushButton("✕ Удалить")
        self.add_reg_btn.clicked.connect(self._add_register)
        self.edit_reg_btn.clicked.connect(self._edit_register)
        self.del_reg_btn.clicked.connect(self._delete_register)
        for btn in (self.add_reg_btn, self.edit_reg_btn, self.del_reg_btn):
            reg_btn_layout.addWidget(btn)
        reg_btns = QWidget()
        reg_btns.setLayout(reg_btn_layout)
        self.add_setting_widget(reg_btns)

        poll_layout = QHBoxLayout()
        poll_layout.addWidget(QLabel("Интервал (сек):"))
        self.poll_interval_spin = QDoubleSpinBox()
        self.poll_interval_spin.setRange(0.2, 60.0)
        self.poll_interval_spin.setValue(self.poll_interval)
        self.poll_interval_spin.valueChanged.connect(self._on_poll_interval_changed)
        poll_layout.addWidget(self.poll_interval_spin)
        poll_w = QWidget()
        poll_w.setLayout(poll_layout)
        self.add_setting_row("Опрос:", poll_w)

        start_stop = QHBoxLayout()
        self.start_btn = QPushButton("▶ Старт")
        self.start_btn.clicked.connect(self.start_polling)
        self.stop_btn  = QPushButton("■ Стоп")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_polling)
        start_stop.addWidget(self.start_btn)
        start_stop.addWidget(self.stop_btn)
        ss_w = QWidget()
        ss_w.setLayout(start_stop)
        self.add_setting_widget(ss_w)

        self._on_conn_type_changed(self.conn_type_combo.currentText())

    def _on_conn_type_changed(self, text: str):
        is_tcp = "TCP" in text
        is_com = "COM" in text
        self.tcp_widget.setVisible(is_tcp)
        self.rtu_widget.setVisible(is_com)

    def _on_poll_interval_changed(self, value: float):
        self.poll_interval = value
        if self.poll_thread:
            self.poll_thread.interval = value

    def _refresh_reg_table(self):
        self.reg_table.refresh(self.registers)
        params = {reg["name"]: {"unit": reg.get("unit", "")} for reg in self.registers}
        self.set_parameters(params)

    def _add_register(self):
        dialog = ModbusRegisterDialog(parent=self)
        if dialog.exec_():
            reg = dialog.get_register()
            if not reg["name"]:
                QMessageBox.warning(self, "Ошибка", "Имя параметра не может быть пустым.")
                return
            if any(r["name"] == reg["name"] for r in self.registers):
                QMessageBox.warning(self, "Ошибка", f"Параметр «{reg['name']}» уже существует.")
                return
            self.registers.append(reg)
            self._refresh_reg_table()

    def _edit_register(self):
        idx = self.reg_table.selected_index()
        if idx < 0:
            QMessageBox.information(self, "Редактирование", "Выберите регистр в таблице.")
            return
        dialog = ModbusRegisterDialog(self.registers[idx], self)
        if dialog.exec_():
            self.registers[idx] = dialog.get_register()
            self._refresh_reg_table()

    def _delete_register(self):
        idx = self.reg_table.selected_index()
        if idx < 0:
            QMessageBox.information(self, "Удаление", "Выберите регистр в таблице.")
            return
        name = self.registers[idx]["name"]
        reply = QMessageBox.question(
            self, "Удаление регистра", f"Удалить регистр «{name}»?",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            del self.registers[idx]
            self._refresh_reg_table()

    def connect_device(self) -> bool:
        self.slave_id = self.slave_spin.value()
        conn_type = self.conn_type_combo.currentText()
        try:
            if conn_type == "Modbus TCP":
                self.driver = modBus.ModBus_TCP_Client(self.ip_edit.text(), self.port_spin.value())
            elif conn_type == "Modbus RTU over TCP":
                self.driver = modBus.ModBus_Socket_Client(self.ip_edit.text(), self.port_spin.value())
            elif conn_type == "Modbus RTU (COM)":
                self.driver = modBus.ModBus_Serial_Client(self.port_edit.text(), self.baud_spin.value())
            else:
                raise ValueError(f"Неизвестный тип подключения: {conn_type}")

            self.is_connected = True
            self.log_event(f"Подключено [{conn_type}]")
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self.status_label.setText("● Подключено")
            self.status_label.setStyleSheet("color: #3fb950; font-weight: bold;")
            return True
        except Exception as e:
            self.log_event(f"Ошибка подключения: {e}")
            QMessageBox.critical(self, "Ошибка подключения", str(e))
            return False

    def disconnect_device(self):
        self.stop_polling()
        if self.driver:
            try:
                self.driver.close()
            except Exception:
                pass
            self.driver = None
        self.is_connected = False
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.status_label.setText("● Не подключено")
        self.status_label.setStyleSheet("color: #f85149; font-weight: bold;")
        self.log_event("Отключено")

    def start_polling(self):
        if not self.is_connected or not self.driver:
            QMessageBox.warning(self, "Внимание", "Устройство не подключено.")
            return
        if self.poll_thread and self.poll_thread.isRunning():
            return
        if not self.registers:
            QMessageBox.warning(self, "Внимание", "Не задано ни одного регистра для опроса.")
            return
        self.poll_thread = ModbusPollingThread(self.driver, self.registers, self.slave_id)
        self.poll_thread.interval = self.poll_interval
        self.poll_thread.new_values.connect(self._on_new_values)
        self.poll_thread.error_occurred.connect(self._on_poll_error)
        self.poll_thread.start()
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.log_event(f"Опрос запущен (интервал {self.poll_interval:.1f} с)")

    def stop_polling(self):
        if self.poll_thread:
            self.poll_thread.stop()
            self.poll_thread = None
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.log_event("Опрос остановлен")

    def _on_new_values(self, values: Dict[str, Optional[float]]):
        for name, val in values.items():
            if val is not None:
                self.update_value_display(name, val)
                unit = next((r["unit"] for r in self.registers if r["name"] == name), "")
                self.data_updated.emit(self.device_name, name, val, unit)
                self.log_measurement({name: val})
            else:
                self.update_value_display(name, float("nan"))

    def _on_poll_error(self, err_msg: str):
        self.log_event(f"Ошибка опроса: {err_msg}")
        self.stop_polling()
        reply = QMessageBox.critical(
            self, "Ошибка связи", f"{err_msg}\n\nПопытаться переподключиться?",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self.disconnect_device()
            if self.connect_device():
                self.start_polling()

    def get_config(self) -> Dict[str, Any]:
        config = super().get_config()
        config.update({
            "conn_type": self.conn_type_combo.currentText(),
            "ip": self.ip_edit.text(),
            "port": self.port_spin.value(),
            "serial_port": self.port_edit.text(),
            "baudrate": self.baud_spin.value(),
            "slave_id": self.slave_spin.value(),
            "registers": self.registers,
            "poll_interval": self.poll_interval,
        })
        return config

    def apply_config(self, config: Dict[str, Any]):
        super().apply_config(config)
        ct = config.get("conn_type", "Modbus TCP")
        idx = self.conn_type_combo.findText(ct)
        if idx >= 0:
            self.conn_type_combo.setCurrentIndex(idx)
        self.ip_edit.setText(config.get("ip", "192.168.0.83"))
        self.port_spin.setValue(config.get("port", 5000))
        self.port_edit.setText(config.get("serial_port", "COM1"))
        self.baud_spin.setValue(config.get("baudrate", 9600))
        self.slave_spin.setValue(config.get("slave_id", 1))
        self.poll_interval = config.get("poll_interval", 2.0)
        self.poll_interval_spin.setValue(self.poll_interval)
        self.registers = config.get("registers", [])
        self._refresh_reg_table()
