"""
Автор: m.yablokov
panels/base_device_panel.py
Базовый класс для панели устройства в UniversalMonitor.

Изменения (23.04.2026):
  - Усилены стили: явный color:#e6edf3 на QLabel и QFormLayout
  - Фон всех дочерних виджетов явно задан как #0d1117 / #161b22
"""

import os
import csv
import threading
import time
from datetime import datetime
from typing import Optional, List, Dict, Any

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel,
    QPushButton, QTextEdit, QLineEdit, QComboBox, QSpinBox,
    QDoubleSpinBox, QCheckBox, QFormLayout, QGridLayout,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox,
    QFileDialog
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QBrush


class BaseDevicePanel(QWidget):
    data_updated = pyqtSignal(str, str, float, str)

    def __init__(self, device_type: str, device_name: str, parent=None):
        super().__init__(parent)
        self.device_type = device_type
        self.device_name = device_name
        self.is_connected = False
        self.is_polling = False
        self.poll_thread: Optional[threading.Thread] = None
        self.poll_interval = 2.0
        self.history_file: Optional[str] = None
        self.csv_writer_lock = threading.Lock()
        self.last_values: Dict[str, float] = {}
        self.parameters: Dict[str, Dict[str, Any]] = {}
        self._init_ui()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(8)

        top_widget = QWidget()
        top_layout = QHBoxLayout(top_widget)
        top_layout.setContentsMargins(0, 0, 0, 0)

        self.settings_group = QGroupBox("Настройки подключения")
        self.settings_layout = QFormLayout(self.settings_group)
        self.settings_layout.setLabelAlignment(Qt.AlignRight)
        self.settings_layout.setSpacing(6)

        btn_layout = QHBoxLayout()
        self.connect_btn = QPushButton("Подключить")
        self.connect_btn.clicked.connect(self._on_connect_clicked)
        self.disconnect_btn = QPushButton("Отключить")
        self.disconnect_btn.setEnabled(False)
        self.disconnect_btn.clicked.connect(self._on_disconnect_clicked)
        btn_layout.addWidget(self.connect_btn)
        btn_layout.addWidget(self.disconnect_btn)

        self.status_label = QLabel("● Не подключено")
        self.status_label.setStyleSheet("color: #f85149; font-weight: bold;")

        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.addWidget(self.settings_group)
        left_layout.addLayout(btn_layout)
        left_layout.addWidget(self.status_label)
        left_layout.addStretch()

        self.values_group = QGroupBox("Текущие значения")
        values_layout = QVBoxLayout(self.values_group)

        poll_layout = QHBoxLayout()
        poll_lbl = QLabel("Интервал опроса (сек):")
        poll_lbl.setStyleSheet("color: #e6edf3;")
        poll_layout.addWidget(poll_lbl)
        self.poll_interval_spin = QDoubleSpinBox()
        self.poll_interval_spin.setRange(0.2, 60.0)
        self.poll_interval_spin.setValue(self.poll_interval)
        self.poll_interval_spin.valueChanged.connect(self._on_poll_interval_changed)
        poll_layout.addWidget(self.poll_interval_spin)
        poll_layout.addStretch()
        values_layout.addLayout(poll_layout)

        self.values_table = QTableWidget()
        self.values_table.setColumnCount(3)
        self.values_table.setHorizontalHeaderLabels(["Параметр", "Значение", "Ед. изм."])
        self.values_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.values_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.values_table.setAlternatingRowColors(True)
        values_layout.addWidget(self.values_table)

        top_layout.addWidget(left_widget, 1)
        top_layout.addWidget(self.values_group, 2)

        logs_widget = QWidget()
        logs_layout = QHBoxLayout(logs_widget)
        logs_layout.setContentsMargins(0, 0, 0, 0)

        event_group = QGroupBox("Лог событий")
        event_layout = QVBoxLayout(event_group)
        event_btn_layout = QHBoxLayout()
        clear_event_btn = QPushButton("Очистить")
        clear_event_btn.clicked.connect(lambda: self.event_log.clear())
        save_event_btn = QPushButton("Сохранить в TXT")
        save_event_btn.clicked.connect(self._save_event_log)
        event_btn_layout.addWidget(clear_event_btn)
        event_btn_layout.addWidget(save_event_btn)
        event_btn_layout.addStretch()
        event_layout.addLayout(event_btn_layout)
        self.event_log = QTextEdit()
        self.event_log.setReadOnly(True)
        self.event_log.setMaximumHeight(120)
        event_layout.addWidget(self.event_log)

        meas_group = QGroupBox("Лог измерений")
        meas_layout = QVBoxLayout(meas_group)
        meas_btn_layout = QHBoxLayout()
        self.log_meas_check = QCheckBox("Запись измерений")
        self.log_meas_check.setChecked(True)
        clear_meas_btn = QPushButton("Очистить")
        clear_meas_btn.clicked.connect(lambda: self.meas_log.clear())
        save_meas_btn = QPushButton("Сохранить в CSV")
        save_meas_btn.clicked.connect(self._save_meas_log)
        meas_btn_layout.addWidget(self.log_meas_check)
        meas_btn_layout.addWidget(clear_meas_btn)
        meas_btn_layout.addWidget(save_meas_btn)
        meas_btn_layout.addStretch()
        meas_layout.addLayout(meas_btn_layout)
        self.meas_log = QTextEdit()
        self.meas_log.setReadOnly(True)
        self.meas_log.setMaximumHeight(120)
        meas_layout.addWidget(self.meas_log)

        logs_layout.addWidget(event_group)
        logs_layout.addWidget(meas_group)

        main_layout.addWidget(top_widget, 2)
        main_layout.addWidget(logs_widget, 1)
        self._apply_styles()

    def _apply_styles(self):
        self.setStyleSheet("""
            QWidget {
                background-color: #0d1117;
                color: #e6edf3;
                font-family: Consolas;
                font-size: 10pt;
            }
            QLabel {
                color: #e6edf3;
                background-color: transparent;
            }
            QGroupBox {
                background-color: #161b22;
                border: 1px solid #30363d;
                border-radius: 5px;
                margin-top: 10px;
                font-weight: bold;
                color: #e6edf3;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
                color: #00d4ff;
            }
            QPushButton {
                background-color: #30363d;
                border: none;
                border-radius: 4px;
                padding: 6px 12px;
                color: #e6edf3;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #00d4ff; color: #0d1117; }
            QPushButton:disabled { background-color: #21262d; color: #8b949e; }
            QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {
                background-color: #0d1117;
                border: 1px solid #30363d;
                border-radius: 3px;
                padding: 4px;
                color: #e6edf3;
            }
            QTableWidget {
                background-color: #0d1117;
                color: #e6edf3;
                gridline-color: #30363d;
            }
            QTableWidget::item {
                border-bottom: 1px solid #30363d;
                color: #e6edf3;
            }
            QTableWidget::item:alternate {
                background-color: #161b22;
                color: #e6edf3;
            }
            QHeaderView::section {
                background-color: #161b22;
                color: #00d4ff;
                font-weight: bold;
                padding: 5px;
                border: none;
                border-bottom: 1px solid #30363d;
            }
            QTextEdit {
                background-color: #0d1117;
                border: 1px solid #30363d;
                border-radius: 3px;
                color: #e6edf3;
            }
            QCheckBox {
                spacing: 8px;
                color: #e6edf3;
            }
            QCheckBox::indicator {
                width: 14px;
                height: 14px;
                border: 1px solid #30363d;
                border-radius: 3px;
                background-color: #0d1117;
            }
            QCheckBox::indicator:checked {
                background-color: #00d4ff;
                border-color: #00d4ff;
            }
            QFormLayout QLabel {
                color: #e6edf3;
            }
        """)

    def add_setting_row(self, label: str, widget: QWidget):
        lbl = QLabel(label)
        lbl.setStyleSheet("color: #e6edf3; background-color: transparent;")
        self.settings_layout.addRow(lbl, widget)

    def add_setting_widget(self, widget: QWidget):
        self.settings_layout.addRow(widget)

    def set_parameters(self, params: Dict[str, Dict[str, Any]]):
        self.parameters = params
        self._rebuild_values_table()

    def _rebuild_values_table(self):
        self.values_table.setRowCount(len(self.parameters))
        for row, (name, info) in enumerate(self.parameters.items()):
            item_name = QTableWidgetItem(name)
            item_name.setForeground(QColor("#e6edf3"))
            self.values_table.setItem(row, 0, item_name)

            val_item = QTableWidgetItem("—")
            val_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            val_item.setForeground(QColor("#e6edf3"))
            self.values_table.setItem(row, 1, val_item)

            unit_item = QTableWidgetItem(info.get("unit", ""))
            unit_item.setForeground(QColor("#8b949e"))
            self.values_table.setItem(row, 2, unit_item)

    def update_value_display(self, param_name: str, value: float):
        self.last_values[param_name] = value
        for row, name in enumerate(self.parameters.keys()):
            if name == param_name:
                val_item = QTableWidgetItem(f"{value:.6g}")
                val_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                val_item.setForeground(QColor("#00d4ff"))
                self.values_table.setItem(row, 1, val_item)
                break

    def log_event(self, message: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.event_log.append(f"[{timestamp}] {message}")

    def log_measurement(self, data: Dict[str, float]):
        if not self.log_meas_check.isChecked():
            return
        timestamp = datetime.now().strftime("%H:%M:%S")
        parts = [f"{k}={v:.6g}" for k, v in data.items()]
        self.meas_log.append(f"[{timestamp}] " + ", ".join(parts))

    def _save_event_log(self):
        content = self.event_log.toPlainText()
        if not content.strip():
            return
        filename, _ = QFileDialog.getSaveFileName(
            self, "Сохранить лог событий", f"{self.device_name}_events.txt", "Text Files (*.txt)")
        if filename:
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(content)

    def _save_meas_log(self):
        content = self.meas_log.toPlainText()
        if not content.strip():
            return
        filename, _ = QFileDialog.getSaveFileName(
            self, "Сохранить лог измерений", f"{self.device_name}_measurements.csv", "CSV Files (*.csv)")
        if filename:
            with open(filename, 'w', encoding='utf-8') as f:
                f.write("Timestamp;Parameters\n")
                f.write(content)

    def connect_device(self) -> bool:
        raise NotImplementedError

    def disconnect_device(self):
        raise NotImplementedError

    def _on_connect_clicked(self):
        try:
            success = self.connect_device()
            if success:
                self.is_connected = True
                self.connect_btn.setEnabled(False)
                self.disconnect_btn.setEnabled(True)
                self.status_label.setText("● Подключено")
                self.status_label.setStyleSheet("color: #3fb950; font-weight: bold;")
                self.log_event("Подключение установлено")
                self.start_polling()
        except Exception as e:
            self.log_event(f"Ошибка подключения: {e}")
            QMessageBox.critical(self, "Ошибка", f"Не удалось подключиться:\n{e}")

    def _on_disconnect_clicked(self):
        self.stop_polling()
        try:
            self.disconnect_device()
        except Exception as e:
            self.log_event(f"Ошибка при отключении: {e}")
        self.is_connected = False
        self.connect_btn.setEnabled(True)
        self.disconnect_btn.setEnabled(False)
        self.status_label.setText("● Не подключено")
        self.status_label.setStyleSheet("color: #f85149; font-weight: bold;")
        self.log_event("Отключено")

    def start_polling(self):
        if not self.is_connected:
            return
        if self.is_polling:
            return
        self.is_polling = True
        self.poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self.poll_thread.start()

    def stop_polling(self):
        self.is_polling = False
        if self.poll_thread and self.poll_thread.is_alive():
            self.poll_thread.join(timeout=2.0)
        self.poll_thread = None

    def _poll_loop(self):
        while self.is_polling:
            try:
                data = self.read_device_values()
                if data:
                    for name, value in data.items():
                        QTimer.singleShot(0, lambda n=name, v=value: self._update_gui_and_emit(n, v))
                    QTimer.singleShot(0, lambda d=data: self.log_measurement(d))
            except Exception as e:
                self.log_event(f"Ошибка в цикле опроса: {e}")
            time.sleep(self.poll_interval)

    def _update_gui_and_emit(self, param_name: str, value: float):
        self.update_value_display(param_name, value)
        unit = self.parameters.get(param_name, {}).get("unit", "")
        self.data_updated.emit(self.device_name, param_name, value, unit)

    def read_device_values(self) -> Dict[str, float]:
        raise NotImplementedError

    def _on_poll_interval_changed(self, value: float):
        self.poll_interval = value

    def get_config(self) -> Dict[str, Any]:
        return {"type": self.device_type, "name": self.device_name,
                "poll_interval": self.poll_interval}

    def apply_config(self, config: Dict[str, Any]):
        self.poll_interval = config.get("poll_interval", 2.0)
        self.poll_interval_spin.setValue(self.poll_interval)
