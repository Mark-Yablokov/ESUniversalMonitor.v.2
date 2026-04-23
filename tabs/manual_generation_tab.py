# tabs/manual_generation_tab.py

"""
Вкладка для ручной генерации тестовых сигналов и сравнения с измерениями.

Исправления (2026-04-23):
  - toggle_generator_connection: is_connected() → is_connected (property, без скобок).
  - toggle_output: enable_output() заменён на прямой вызов output_off().
  - apply_settings: generator.apply_settings() → generator.apply_settings()
    (теперь работает через BaseGenerator.apply_settings → set_point).
"""

import json
import os
from datetime import datetime
from typing import Dict, List, Optional, Any

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QGroupBox, QFormLayout, QTextEdit, QTableWidget,
    QTableWidgetItem, QHeaderView, QFileDialog, QMessageBox,
    QDoubleSpinBox, QCheckBox, QSpinBox
)

# Импорт структур данных из общего модуля
from core.measurement_types import TestPoint, ToleranceSpec, ParameterLink

from generators import PTSGenerator, MantigoraGenerator


class ManualGenerationTab(QWidget):
    """Вкладка ручного управления генерацией и измерением."""

    def __init__(self, device_panels: List = None, parent=None):
        super().__init__(parent)
        self.device_panels = device_panels or []
        self.generator = None
        self.current_test_point: Optional[TestPoint] = None
        self.measurement_history: List[Dict] = []

        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # --- Выбор генератора ---
        gen_group = QGroupBox("Генератор")
        gen_layout = QHBoxLayout(gen_group)
        gen_layout.addWidget(QLabel("Тип:"))
        self.gen_type_combo = QComboBox()
        self.gen_type_combo.addItems(["PTS", "Mantigora"])
        gen_layout.addWidget(self.gen_type_combo)

        self.connect_gen_btn = QPushButton("Подключить")
        self.connect_gen_btn.clicked.connect(self.toggle_generator_connection)
        gen_layout.addWidget(self.connect_gen_btn)

        self.gen_status_label = QLabel("Не подключен")
        gen_layout.addWidget(self.gen_status_label)
        gen_layout.addStretch()

        layout.addWidget(gen_group)

        # --- Панель установки параметров ---
        set_group = QGroupBox("Установка выходных параметров")
        set_layout = QFormLayout(set_group)

        self.voltage_spin = QDoubleSpinBox()
        self.voltage_spin.setRange(0.0, 480.0)
        self.voltage_spin.setSuffix(" В")
        self.voltage_spin.setDecimals(3)
        set_layout.addRow("Напряжение:", self.voltage_spin)

        self.current_spin = QDoubleSpinBox()
        self.current_spin.setRange(0.0, 120.0)
        self.current_spin.setSuffix(" А")
        self.current_spin.setDecimals(6)
        set_layout.addRow("Ток:", self.current_spin)

        self.frequency_spin = QDoubleSpinBox()
        self.frequency_spin.setRange(45.0, 65.0)
        self.frequency_spin.setSuffix(" Гц")
        self.frequency_spin.setDecimals(3)
        self.frequency_spin.setValue(50.0)
        set_layout.addRow("Частота:", self.frequency_spin)

        self.phase_spin = QDoubleSpinBox()
        self.phase_spin.setRange(-180.0, 180.0)
        self.phase_spin.setSuffix(" °")
        self.phase_spin.setDecimals(3)
        set_layout.addRow("Угол (φ):", self.phase_spin)

        self.output_check = QCheckBox("Выход включен")
        self.output_check.toggled.connect(self.toggle_output)
        set_layout.addRow(self.output_check)

        btn_layout = QHBoxLayout()
        self.apply_btn = QPushButton("Применить")
        self.apply_btn.clicked.connect(self.apply_settings)
        btn_layout.addWidget(self.apply_btn)

        self.measure_btn = QPushButton("Измерить")
        self.measure_btn.clicked.connect(self.perform_measurement)
        btn_layout.addWidget(self.measure_btn)

        set_layout.addRow(btn_layout)
        layout.addWidget(set_group)

        # --- Допуски и автосравнение ---
        tol_group = QGroupBox("Допуски")
        tol_layout = QFormLayout(tol_group)

        self.tol_voltage_abs = QDoubleSpinBox()
        self.tol_voltage_abs.setRange(0.0, 100.0)
        self.tol_voltage_abs.setSuffix(" В")
        self.tol_voltage_abs.setDecimals(3)
        tol_layout.addRow("U абс.:", self.tol_voltage_abs)

        self.tol_voltage_rel = QDoubleSpinBox()
        self.tol_voltage_rel.setRange(0.0, 100.0)
        self.tol_voltage_rel.setSuffix(" %")
        self.tol_voltage_rel.setDecimals(3)
        tol_layout.addRow("U отн.:", self.tol_voltage_rel)

        self.tol_current_abs = QDoubleSpinBox()
        self.tol_current_abs.setRange(0.0, 10.0)
        self.tol_current_abs.setSuffix(" А")
        self.tol_current_abs.setDecimals(6)
        tol_layout.addRow("I абс.:", self.tol_current_abs)

        self.tol_current_rel = QDoubleSpinBox()
        self.tol_current_rel.setRange(0.0, 100.0)
        self.tol_current_rel.setSuffix(" %")
        self.tol_current_rel.setDecimals(3)
        tol_layout.addRow("I отн.:", self.tol_current_rel)

        self.compare_check = QCheckBox("Автоматически сравнивать с допусками")
        self.compare_check.setChecked(True)
        tol_layout.addRow(self.compare_check)

        layout.addWidget(tol_group)

        # --- Таблица результатов ---
        self.results_table = QTableWidget()
        self.results_table.setColumnCount(7)
        self.results_table.setHorizontalHeaderLabels(
            ["Время", "U уст", "I уст", "U изм", "I изм", "U в доп.", "I в доп."]
        )
        self.results_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.results_table, 2)

        # --- Кнопки управления историей ---
        hist_btn_layout = QHBoxLayout()
        self.save_csv_btn = QPushButton("Сохранить в CSV")
        self.save_csv_btn.clicked.connect(self.save_to_csv)
        hist_btn_layout.addWidget(self.save_csv_btn)

        self.clear_hist_btn = QPushButton("Очистить историю")
        self.clear_hist_btn.clicked.connect(self.clear_history)
        hist_btn_layout.addWidget(self.clear_hist_btn)

        layout.addLayout(hist_btn_layout)

        # --- Лог ---
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(100)
        layout.addWidget(self.log_text)

    def _connect_signals(self):
        pass

    def set_device_panels(self, panels: List):
        self.device_panels = panels

    def toggle_generator_connection(self):
        # Исправление: is_connected — @property, вызываем без скобок
        if self.generator and self.generator.is_connected:
            self.disconnect_generator()
        else:
            self.connect_generator()

    def connect_generator(self):
        gen_type = self.gen_type_combo.currentText()
        if gen_type == "PTS":
            self.generator = PTSGenerator()
        elif gen_type == "Mantigora":
            self.generator = MantigoraGenerator()
        else:
            return

        # Передаём панели устройств для поиска подходящего драйвера
        if self.generator.connect(self.device_panels):
            self.gen_status_label.setText(f"Подключен ({gen_type})")
            self.connect_gen_btn.setText("Отключить")
            self.log(f"Генератор {gen_type} подключен.")
        else:
            self.gen_status_label.setText("Ошибка подключения")
            self.log(f"Не удалось подключить генератор {gen_type}.")
            self.generator = None

    def disconnect_generator(self):
        if self.generator:
            self.generator.disconnect()
            self.generator = None
        self.gen_status_label.setText("Не подключен")
        self.connect_gen_btn.setText("Подключить")
        self.log("Генератор отключен.")

    def toggle_output(self, enabled: bool):
        # Исправление: is_connected — @property, без скобок
        if not self.generator or not self.generator.is_connected:
            self.output_check.blockSignals(True)
            self.output_check.setChecked(False)
            self.output_check.blockSignals(False)
            return
        try:
            if enabled:
                # Включение выхода управляется через apply_settings/set_point.
                # Если пользователь просто ставит галочку без применения параметров,
                # повторно отправляем текущие уставки.
                self.apply_settings()
            else:
                self.generator.output_off()
                self.log("Выход выключен.")
        except Exception as e:
            self.log(f"Ошибка управления выходом: {e}")

    def apply_settings(self):
        # Исправление: is_connected — @property, без скобок
        if not self.generator or not self.generator.is_connected:
            QMessageBox.warning(self, "Ошибка", "Генератор не подключен.")
            return

        settings = {
            'voltage': self.voltage_spin.value(),
            'current': self.current_spin.value(),
            'frequency': self.frequency_spin.value(),
            'phase': self.phase_spin.value()
        }
        try:
            # BaseGenerator.apply_settings() → set_point(settings)
            self.generator.apply_settings(settings)
            self.log(f"Установлены параметры: U={settings['voltage']} В, "
                     f"I={settings['current']} А, f={settings['frequency']} Гц.")
        except Exception as e:
            self.log(f"Ошибка установки параметров: {e}")

    def perform_measurement(self):
        """Выполнить измерение со всех подключённых панелей и сравнить с допусками."""
        if not self.device_panels:
            self.log("Нет подключённых измерительных устройств.")
            return

        measurement = self._collect_measurements()
        self._add_to_history(measurement)
        self._display_measurement(measurement)

        # Автосравнение с допусками
        if self.compare_check.isChecked():
            self._evaluate_tolerances(measurement)

    def _collect_measurements(self) -> Dict:
        """Собрать данные со всех панелей."""
        data = {
            'timestamp': datetime.now(),
            'set_voltage': self.voltage_spin.value(),
            'set_current': self.current_spin.value(),
            'set_frequency': self.frequency_spin.value(),
            'set_phase': self.phase_spin.value(),
            'channels': {}
        }
        for panel in self.device_panels:
            if hasattr(panel, 'get_measurement'):
                dev_id = panel.get_device_id()
                meas = panel.get_measurement()
                if meas:
                    data['channels'][dev_id] = meas
        return data

    def _add_to_history(self, measurement: Dict):
        self.measurement_history.append(measurement)

    def _display_measurement(self, measurement: Dict):
        """Добавить строку в таблицу (упрощённо берём первый канал)."""
        row = self.results_table.rowCount()
        self.results_table.insertRow(row)

        time_str = measurement['timestamp'].strftime("%H:%M:%S")
        self.results_table.setItem(row, 0, QTableWidgetItem(time_str))

        set_u = f"{measurement['set_voltage']:.3f}"
        self.results_table.setItem(row, 1, QTableWidgetItem(set_u))
        set_i = f"{measurement['set_current']:.6f}"
        self.results_table.setItem(row, 2, QTableWidgetItem(set_i))

        meas_u = "---"
        meas_i = "---"
        if measurement['channels']:
            first_dev = list(measurement['channels'].values())[0]
            meas_u = f"{first_dev.get('voltage', 0.0):.3f}"
            meas_i = f"{first_dev.get('current', 0.0):.6f}"

        self.results_table.setItem(row, 3, QTableWidgetItem(meas_u))
        self.results_table.setItem(row, 4, QTableWidgetItem(meas_i))
        self.results_table.setItem(row, 5, QTableWidgetItem("—"))
        self.results_table.setItem(row, 6, QTableWidgetItem("—"))

    def _evaluate_tolerances(self, measurement: Dict):
        """Проверить допуски и обновить последнюю строку таблицы."""
        if not measurement['channels']:
            return

        row = self.results_table.rowCount() - 1
        if row < 0:
            return

        tol_u_abs = self.tol_voltage_abs.value()
        tol_u_rel = self.tol_voltage_rel.value()
        tol_i_abs = self.tol_current_abs.value()
        tol_i_rel = self.tol_current_rel.value()

        u_spec = ToleranceSpec(absolute=tol_u_abs if tol_u_abs > 0 else None,
                               relative=tol_u_rel if tol_u_rel > 0 else None)
        i_spec = ToleranceSpec(absolute=tol_i_abs if tol_i_abs > 0 else None,
                               relative=tol_i_rel if tol_i_rel > 0 else None)

        first_dev = list(measurement['channels'].values())[0]
        u_ref = measurement['set_voltage']
        i_ref = measurement['set_current']

        u_meas = first_dev.get('voltage')
        i_meas = first_dev.get('current')

        u_pass = False
        i_pass = False

        if u_meas is not None and u_ref is not None:
            u_pass = u_spec.validate_value(u_meas, u_ref)
            self.results_table.setItem(row, 5, QTableWidgetItem("Да" if u_pass else "Нет"))
        if i_meas is not None and i_ref is not None:
            i_pass = i_spec.validate_value(i_meas, i_ref)
            self.results_table.setItem(row, 6, QTableWidgetItem("Да" if i_pass else "Нет"))

        self.log(f"Проверка допусков: U — {'пройдена' if u_pass else 'НЕ пройдена'}, "
                 f"I — {'пройдена' if i_pass else 'НЕ пройдена'}")

    def clear_history(self):
        self.measurement_history.clear()
        self.results_table.setRowCount(0)
        self.log("История очищена.")

    def save_to_csv(self):
        if not self.measurement_history:
            return

        filename = f"manual_measurements_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        path = os.path.join("history", filename)
        os.makedirs("history", exist_ok=True)

        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write("Timestamp;Set U;Set I;Set f;Set φ;Meas U;Meas I;U Pass;I Pass\n")
                for i in range(self.results_table.rowCount()):
                    row_data = []
                    for j in range(self.results_table.columnCount()):
                        item = self.results_table.item(i, j)
                        row_data.append(item.text() if item else "")
                    f.write(";".join(row_data) + "\n")
            self.log(f"Данные сохранены в {path}")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить CSV: {e}")

    def log(self, message: str):
        self.log_text.append(message)
