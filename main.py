"""
main.py
Главное окно приложения UniversalMonitor.

Изменения (21.04.2026):
  - Добавлена вкладка «Авто-испытание» (AutoTestTab).
  - Добавлена поддержка PTSPanel (тип "PTS").
  - Исправлено: несоответствие имён типов в диалоге добавления устройства.

Исправления после реструктуризации (23.04.2026):
  - Исправлены пути импорта: base_device_panel → panels.base_device_panel
  - Исправлены пути импорта: dashboard → tabs.dashboard
  - Исправлены пути импорта: measurement_tab → tabs.manual_generation_tab
  - Исправлены пути импорта: auto_test_tab → tabs.auto_test_tab
  - CONFIG_DIR изменён на config/config_devices (согласно новой структуре)

Исправления (23.04.2026 — bugfix):
  - _load_devices_from_config: ключ "Mantigora" расширен до ("Mantigora", "Mantigora HT"),
    чтобы конфиги, сохранённые с type="Mantigora HT", тоже загружались корректно.
"""

import sys
import json
import csv
import os
import threading
from datetime import datetime
from typing import Dict, List, Optional

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTabWidget, QTabBar, QPushButton, QMessageBox, QFileDialog,
    QInputDialog, QLabel, QStatusBar
)
from PyQt5.QtCore import Qt, QTimer, pyqtSlot

# ── Панели устройств ──────────────────────────────────────────────────────────
from panels.base_device_panel import BaseDevicePanel
from panels.rigol_panel import RigolPanel
from panels.modbus_panel import ModbusPanel
from panels.mantigora_panel import MantigoraPanel
from panels.pts_panel import PTSPanel

# ── Вкладки ───────────────────────────────────────────────────────────────────
from tabs.dashboard import Dashboard
from tabs.manual_generation_tab import ManualGenerationTab
from tabs.auto_test_tab import AutoTestTab

# ── Пути конфигурации ─────────────────────────────────────────────────────────
CONFIG_DIR  = os.path.join("config", "config_devices")
HISTORY_DIR = "history"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("UniversalMonitor")
        self.setMinimumSize(1200, 800)

        self.device_panels: List[BaseDevicePanel] = []

        self.history_file: Optional[str] = None
        self.csv_file   = None
        self.csv_writer = None
        self.csv_writer_lock = threading.Lock()
        self._init_history_file()
        self._ensure_config_dir()

        self._init_ui()
        self._apply_styles()
        self._load_devices_from_config()

        # Обновляем ссылки на устройства в панелях
        self.dashboard.device_panels = self.device_panels
        self.dashboard.set_history_file(self.history_file)
        self.measurement_tab.device_panels = self.device_panels
        self.auto_test_tab.device_panels   = self.device_panels

    def _ensure_config_dir(self):
        """Создаёт директорию config/config_devices если не существует."""
        os.makedirs(CONFIG_DIR, exist_ok=True)

    def _init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # Верхняя панель
        top_bar = QWidget()
        top_bar.setFixedHeight(40)
        top_bar.setStyleSheet("background-color: #161b22;")
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(10, 5, 10, 5)

        self.title_label = QLabel("⬡  UNIVERSAL MONITOR")
        self.title_label.setStyleSheet(
            "color: #00d4ff; font-size: 14px; font-weight: bold; background: transparent;"
        )
        top_layout.addWidget(self.title_label)
        top_layout.addStretch()

        self.history_btn = QPushButton("📁 История")
        self.history_btn.setStyleSheet("color: #e6edf3;")
        self.history_btn.clicked.connect(self._select_history_file)
        top_layout.addWidget(self.history_btn)

        save_btn = QPushButton("💾 Сохранить конфигурацию")
        save_btn.setStyleSheet("color: #e6edf3;")
        save_btn.clicked.connect(self._save_all_configs)
        top_layout.addWidget(save_btn)

        main_layout.addWidget(top_bar)

        # Вкладки
        self.tab_widget = QTabWidget()
        self.tab_widget.setTabsClosable(False)
        self.tab_widget.setMovable(True)

        self.dashboard = Dashboard(self.device_panels)
        self.tab_widget.addTab(self.dashboard, "Dashboard")

        self.measurement_tab = ManualGenerationTab(self.device_panels)
        self.tab_widget.addTab(self.measurement_tab, "Испытания")

        self.auto_test_tab = AutoTestTab(self.device_panels)
        self.tab_widget.addTab(self.auto_test_tab, "Авто-испытание")

        self._add_plus_tab()

        main_layout.addWidget(self.tab_widget)
        self.tab_widget.currentChanged.connect(self._on_tab_changed)

        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Готов")

    def _add_plus_tab(self):
        plus_widget = QWidget()
        self.tab_widget.addTab(plus_widget, "+")
        tab_bar = self.tab_widget.tabBar()
        idx = self.tab_widget.count() - 1
        tab_bar.setTabButton(idx, QTabBar.LeftSide, None)
        tab_bar.setTabButton(idx, QTabBar.RightSide, None)

    def _on_tab_changed(self, index: int):
        if index == self.tab_widget.count() - 1:
            self.tab_widget.setCurrentIndex(0)
            self._show_add_device_dialog()

    def _show_add_device_dialog(self):
        # Имена в списке ДОЛЖНЫ совпадать со строками в if/elif ниже
        available_types = ["Rigol DM3068", "Modbus", "PTS", "Mantigora HT"]

        device_type, ok = QInputDialog.getItem(
            self, "Добавить устройство", "Тип устройства:",
            available_types, 0, False
        )
        if not ok:
            return

        name, ok = QInputDialog.getText(
            self, "Имя устройства", "Введите уникальное имя:"
        )
        if not ok or not name.strip():
            return
        name = name.strip()

        for panel in self.device_panels:
            if panel.device_name == name:
                QMessageBox.warning(self, "Ошибка",
                                    "Устройство с таким именем уже существует.")
                return

        panel = None
        if device_type == "Rigol DM3068":
            panel = RigolPanel(name)
        elif device_type == "Modbus":
            panel = ModbusPanel(name)
        elif device_type == "PTS":
            panel = PTSPanel(name)
        elif device_type == "Mantigora HT":
            panel = MantigoraPanel(name)
        else:
            QMessageBox.critical(self, "Ошибка", f"Неизвестный тип: {device_type}")
            return

        self._register_panel(panel)
        idx = self.tab_widget.count() - 1
        self.tab_widget.insertTab(idx, panel, name)
        self.tab_widget.setCurrentIndex(idx)
        self._save_device_config(panel)

        # Обновить ссылки в зависимых вкладках
        self.dashboard.device_panels     = self.device_panels
        self.measurement_tab.device_panels = self.device_panels
        self.auto_test_tab.device_panels   = self.device_panels

        self.statusBar().showMessage(f"Устройство '{name}' добавлено", 3000)

    def _register_panel(self, panel: BaseDevicePanel):
        """Подключить сигналы панели и добавить в список."""
        panel.data_updated.connect(self._on_device_data_updated)
        self.device_panels.append(panel)

    def _save_device_config(self, panel: BaseDevicePanel):
        config   = panel.get_config()
        filename = os.path.join(CONFIG_DIR, f"{panel.device_name}.json")
        try:
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
        except Exception as e:
            QMessageBox.warning(self, "Ошибка",
                f"Не удалось сохранить конфигурацию {panel.device_name}:\n{e}")

    def _save_all_configs(self):
        for panel in self.device_panels:
            self._save_device_config(panel)
        self.statusBar().showMessage("Конфигурации сохранены", 3000)

    def _load_devices_from_config(self):
        if not os.path.exists(CONFIG_DIR):
            return
        for filename in sorted(os.listdir(CONFIG_DIR)):
            if not filename.endswith(".json"):
                continue
            filepath = os.path.join(CONFIG_DIR, filename)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    config = json.load(f)
            except Exception as e:
                print(f"[main] Ошибка загрузки {filename}: {e}")
                continue

            dev_type = config.get("type")
            name     = config.get("name")
            panel    = None

            if dev_type == "Rigol":
                panel = RigolPanel(name)
                panel.apply_config(config)
            elif dev_type == "Modbus":
                panel = ModbusPanel(name)
                panel.apply_config(config)
            elif dev_type == "PTS":
                panel = PTSPanel(name)
                panel.apply_config(config)
            elif dev_type in ("Mantigora", "Mantigora HT"):
                # Исправление: принимаем оба варианта ключа —
                # "Mantigora" (старый) и "Mantigora HT" (новый, из MantigoraPanel.get_config)
                panel = MantigoraPanel(name)
                panel.apply_config(config)
            else:
                continue

            self._register_panel(panel)
            idx = self.tab_widget.count() - 1
            self.tab_widget.insertTab(idx, panel, name)

        self.statusBar().showMessage(f"Загружено устройств: {len(self.device_panels)}")

    @pyqtSlot(str, str, float, str)
    def _on_device_data_updated(self, device_name: str, param_name: str,
                                value: float, unit: str):
        # 1. Запись в CSV-историю
        if self.csv_writer:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with self.csv_writer_lock:
                self.csv_writer.writerow([timestamp, device_name, param_name, value, unit])
                self.csv_file.flush()

        # 2. Прямая передача в Dashboard (live-графики)
        self.dashboard.receive_data(device_name, param_name, value, unit)

    def _init_history_file(self):
        os.makedirs(HISTORY_DIR, exist_ok=True)
        date_str = datetime.now().strftime("%Y%m%d")
        self.history_file = os.path.join(HISTORY_DIR, f"history_{date_str}.csv")
        self._open_history_file()

    def _open_history_file(self):
        if self.csv_file:
            self.csv_file.close()
        file_exists = os.path.isfile(self.history_file)
        self.csv_file   = open(self.history_file, "a", newline="", encoding="utf-8")
        self.csv_writer = csv.writer(self.csv_file, delimiter=";")
        if not file_exists:
            self.csv_writer.writerow(["Timestamp", "Device", "Parameter", "Value", "Unit"])

    def _select_history_file(self):
        filename, _ = QFileDialog.getSaveFileName(
            self, "Выберите файл истории", HISTORY_DIR, "CSV Files (*.csv)"
        )
        if filename:
            self.history_file = filename
            self._open_history_file()
            self.dashboard.set_history_file(filename)
            self.statusBar().showMessage(f"Файл истории: {filename}")

    def _apply_styles(self):
        self.setStyleSheet("""
            QMainWindow { background-color: #0d1117; }
            QWidget { background-color: #0d1117; color: #e6edf3; }
            QTabWidget::pane { border: none; background-color: #0d1117; }
            QTabBar::tab {
                background-color: #161b22; color: #e6edf3; padding: 8px 16px;
                margin-right: 2px; border-top-left-radius: 4px; border-top-right-radius: 4px;
            }
            QTabBar::tab:selected {
                background-color: #0d1117; color: #00d4ff;
                border-bottom: 2px solid #00d4ff;
            }
            QTabBar::tab:hover:!selected { background-color: #21262d; }
            QTabBar::tab:last {
                background-color: #21262d; color: #00d4ff;
                font-weight: bold; font-size: 14px;
            }
            QStatusBar { background-color: #161b22; color: #e6edf3; }
            QPushButton {
                background-color: #30363d; border: none; border-radius: 4px;
                padding: 6px 12px; color: #e6edf3; font-weight: bold;
            }
            QPushButton:hover   { background-color: #00d4ff; color: #0d1117; }
            QPushButton:disabled{ background-color: #21262d; color: #8b949e; }
            QLabel { color: #e6edf3; background-color: transparent; }
        """)

    def closeEvent(self, event):
        for panel in self.device_panels:
            try:
                panel.stop_polling()
                panel.disconnect_device()
            except Exception:
                pass
        if self.csv_file:
            self.csv_file.close()
        self._save_all_configs()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
