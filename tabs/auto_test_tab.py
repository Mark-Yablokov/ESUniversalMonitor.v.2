# tabs/auto_test_tab.py

"""
Вкладка автоматического проведения поверки по заданной методике.
"""

import json
import os
import time
from datetime import datetime
from typing import Dict, List, Optional, Any

from PyQt5.QtCore import QTimer, Qt, QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QGroupBox, QFormLayout, QTextEdit, QProgressBar,
    QTableWidget, QTableWidgetItem, QHeaderView, QFileDialog,
    QMessageBox, QSpinBox, QCheckBox
)

# Импорт структур данных из общего модуля
from core.measurement_types import TestPoint, ToleranceSpec, ParameterLink

from generators import PTSGenerator, MantigoraGenerator


class AutoTestWorker(QThread):
    """
    Поток для выполнения автоматического тестирования без блокировки GUI.
    """
    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int, int)
    measurement_signal = pyqtSignal(dict)        # результат одного измерения
    point_finished_signal = pyqtSignal(str, bool) # имя точки, успех
    finished_signal = pyqtSignal(bool, str)       # общий успех, сообщение

    def __init__(self, test_points: List[TestPoint],
                 generator, device_panels: List,
                 settings: Dict[str, Any]):
        super().__init__()
        self.test_points = test_points
        self.generator = generator
        self.device_panels = device_panels
        self.settings = settings
        self._is_running = True

    def run(self):
        total = len(self.test_points)
        success_count = 0
        results = []

        for idx, point in enumerate(self.test_points):
            if not self._is_running:
                self.log_signal.emit("Тест прерван пользователем.")
                break

            self.log_signal.emit(f"\n--- Точка {idx+1}/{total}: {point.name} ---")
            self.progress_signal.emit(idx + 1, total)

            # Установка параметров генератора
            try:
                self.generator.apply_settings(point.setpoints)
                self.log_signal.emit(f"Установлены параметры: {point.setpoints}")
            except Exception as e:
                self.log_signal.emit(f"Ошибка установки параметров: {str(e)}")
                self.point_finished_signal.emit(point.name, False)
                continue

            # Ожидание стабилизации
            wait_time = point.wait_before_measure
            while wait_time > 0 and self._is_running:
                self.log_signal.emit(f"Ожидание стабилизации: {wait_time:.1f} с")
                time.sleep(min(1.0, wait_time))
                wait_time -= 1.0

            if not self._is_running:
                break

            # Проведение измерений
            point_success = True
            for rep in range(point.repeat_count):
                if not self._is_running:
                    break
                self.log_signal.emit(f"Измерение {rep+1}/{point.repeat_count}")
                measurement = self._perform_measurement(point)
                self.measurement_signal.emit(measurement)

                # Проверка допусков
                if not self._check_tolerances(measurement, point.tolerances):
                    point_success = False
                results.append(measurement)

            if point_success:
                success_count += 1
                self.log_signal.emit(f"Точка '{point.name}': ПРОЙДЕНА")
            else:
                self.log_signal.emit(f"Точка '{point.name}': НЕ ПРОЙДЕНА (превышение допуска)")

            self.point_finished_signal.emit(point.name, point_success)

        # Завершение
        if self._is_running:
            msg = f"Тест завершен. Пройдено {success_count} из {total} точек."
            self.finished_signal.emit(True, msg)
        else:
            self.finished_signal.emit(False, "Тест прерван.")

    def _perform_measurement(self, point: TestPoint) -> Dict:
        """Собрать измерения со всех подключённых панелей устройств."""
        data = {
            'timestamp': datetime.now().isoformat(),
            'point_name': point.name,
            'setpoints': point.setpoints.copy(),
            'channels': {}
        }
        for panel in self.device_panels:
            if hasattr(panel, 'get_measurement'):
                dev_id = panel.get_device_id()
                meas = panel.get_measurement()
                if meas:
                    data['channels'][dev_id] = meas
        return data

    def _check_tolerances(self, measurement: Dict,
                          tolerances: Dict[str, ToleranceSpec]) -> bool:
        """
        Проверить, соответствуют ли измеренные значения допускам.
        Пока реализована упрощённая проверка: если есть допуск и
        измеренное значение выходит за пределы, возвращаем False.
        """
        # В полной реализации нужно сопоставлять каналы с допусками
        # Здесь заглушка — всегда возвращаем True, если не указано иное
        if not tolerances:
            return True

        # Для демонстрации проверяем только если в measurement есть ключ 'voltage'
        for channel, spec in tolerances.items():
            for dev_id, dev_meas in measurement.get('channels', {}).items():
                measured_val = dev_meas.get(channel)
                if measured_val is not None:
                    ref_val = measurement['setpoints'].get(channel)
                    if ref_val is not None:
                        if not spec.validate_value(measured_val, ref_val):
                            return False
        return True

    def stop(self):
        self._is_running = False


class AutoTestTab(QWidget):
    """Вкладка автоматического тестирования."""

    def __init__(self, device_panels: List = None, parent=None):
        super().__init__(parent)
        self.device_panels = device_panels or []
        self.test_points: List[TestPoint] = []
        self.generator = None
        self.worker: Optional[AutoTestWorker] = None
        self._setup_ui()
        self._load_methodology_list()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # Верхняя панель управления
        control_group = QGroupBox("Управление тестом")
        control_layout = QHBoxLayout(control_group)

        control_layout.addWidget(QLabel("Методика:"))
        self.method_combo = QComboBox()
        self.method_combo.setMinimumWidth(200)
        control_layout.addWidget(self.method_combo)

        self.load_btn = QPushButton("Загрузить")
        self.load_btn.clicked.connect(self.load_methodology)
        control_layout.addWidget(self.load_btn)

        control_layout.addSpacing(20)

        self.start_btn = QPushButton("Старт")
        self.start_btn.setEnabled(False)
        self.start_btn.clicked.connect(self.start_test)
        control_layout.addWidget(self.start_btn)

        self.stop_btn = QPushButton("Стоп")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_test)
        control_layout.addWidget(self.stop_btn)

        control_layout.addSpacing(20)

        self.save_report_check = QCheckBox("Сохранять отчёт")
        self.save_report_check.setChecked(True)
        control_layout.addWidget(self.save_report_check)

        layout.addWidget(control_group)

        # Таблица с тестовыми точками
        self.points_table = QTableWidget()
        self.points_table.setColumnCount(5)
        self.points_table.setHorizontalHeaderLabels(
            ["Точка", "Параметры", "Ожидание, с", "Повторов", "Статус"]
        )
        self.points_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.points_table, 2)

        # Прогресс
        progress_layout = QHBoxLayout()
        progress_layout.addWidget(QLabel("Прогресс:"))
        self.progress_bar = QProgressBar()
        progress_layout.addWidget(self.progress_bar)
        layout.addLayout(progress_layout)

        # Лог
        log_group = QGroupBox("Журнал выполнения")
        log_layout = QVBoxLayout(log_group)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        log_layout.addWidget(self.log_text)
        layout.addWidget(log_group, 1)

    def _load_methodology_list(self):
        """Загрузить список доступных методик из папки methodology."""
        method_dir = "methodology"
        if not os.path.exists(method_dir):
            os.makedirs(method_dir)
        files = [f for f in os.listdir(method_dir) if f.endswith('.json')]
        self.method_combo.clear()
        self.method_combo.addItems(files)

    def load_methodology(self):
        """Загрузить выбранную методику и отобразить тестовые точки."""
        filename = self.method_combo.currentText()
        if not filename:
            return
        path = os.path.join("methodology", filename)
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self.test_points = [TestPoint.from_dict(p) for p in data.get('points', [])]
            self._populate_table()
            self.start_btn.setEnabled(len(self.test_points) > 0)
            self.log(f"Методика '{filename}' загружена. Точек: {len(self.test_points)}")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось загрузить методику: {str(e)}")

    def _populate_table(self):
        """Заполнить таблицу тестовыми точками."""
        self.points_table.setRowCount(len(self.test_points))
        for i, point in enumerate(self.test_points):
            self.points_table.setItem(i, 0, QTableWidgetItem(point.name))
            params_str = ", ".join(f"{k}={v}" for k, v in point.setpoints.items())
            self.points_table.setItem(i, 1, QTableWidgetItem(params_str))
            self.points_table.setItem(i, 2, QTableWidgetItem(str(point.wait_before_measure)))
            self.points_table.setItem(i, 3, QTableWidgetItem(str(point.repeat_count)))
            self.points_table.setItem(i, 4, QTableWidgetItem("—"))

    def set_device_panels(self, panels: List):
        """Установить список панелей устройств."""
        self.device_panels = panels

    def set_generator(self, generator):
        """Установить активный генератор."""
        self.generator = generator

    def start_test(self):
        """Запустить тест."""
        if not self.test_points:
            QMessageBox.warning(self, "Предупреждение", "Не загружена методика.")
            return
        if not self.generator or not self.generator.is_connected():
            QMessageBox.warning(self, "Предупреждение", "Генератор не подключен.")
            return

        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.load_btn.setEnabled(False)
        self.progress_bar.setMaximum(len(self.test_points))
        self.progress_bar.setValue(0)

        # Очистить статусы в таблице
        for i in range(len(self.test_points)):
            self.points_table.setItem(i, 4, QTableWidgetItem("Ожидание"))

        self.log_text.clear()
        self.log("=== Начало автоматического теста ===")

        self.worker = AutoTestWorker(
            self.test_points,
            self.generator,
            self.device_panels,
            {}
        )
        self.worker.log_signal.connect(self.log)
        self.worker.progress_signal.connect(self.update_progress)
        self.worker.measurement_signal.connect(self.handle_measurement)
        self.worker.point_finished_signal.connect(self.update_point_status)
        self.worker.finished_signal.connect(self.test_finished)
        self.worker.start()

    def stop_test(self):
        """Остановить тест."""
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.log("Запрос остановки теста...")
            self.stop_btn.setEnabled(False)

    def test_finished(self, success: bool, message: str):
        """Обработка завершения теста."""
        self.log(message)
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.load_btn.setEnabled(True)

        if self.save_report_check.isChecked() and success:
            self._save_report()

    def update_progress(self, current: int, total: int):
        self.progress_bar.setValue(current)

    def update_point_status(self, point_name: str, passed: bool):
        """Обновить статус точки в таблице."""
        for i in range(self.points_table.rowCount()):
            if self.points_table.item(i, 0).text() == point_name:
                status = "Пройдена" if passed else "Не пройдена"
                item = QTableWidgetItem(status)
                if passed:
                    item.setForeground(Qt.green)
                else:
                    item.setForeground(Qt.red)
                self.points_table.setItem(i, 4, item)
                break

    def handle_measurement(self, data: Dict):
        """Обработать результат измерения."""
        # Можно сохранять в историю, пока просто логируем
        pass

    def log(self, message: str):
        self.log_text.append(message)

    def _save_report(self):
        """Сохранить отчёт о тесте в CSV."""
        filename = f"auto_test_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        path = os.path.join("history", filename)
        try:
            os.makedirs("history", exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                f.write("Точка;Параметры;Статус\n")
                for i in range(self.points_table.rowCount()):
                    name = self.points_table.item(i, 0).text()
                    params = self.points_table.item(i, 1).text()
                    status = self.points_table.item(i, 4).text()
                    f.write(f"{name};{params};{status}\n")
            self.log(f"Отчёт сохранён: {path}")
        except Exception as e:
            self.log(f"Ошибка сохранения отчёта: {str(e)}")