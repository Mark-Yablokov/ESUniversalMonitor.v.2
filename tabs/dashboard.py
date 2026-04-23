"""
Автор: m.yablokov
tabs/dashboard.py
Версия 2.2.0  (23.04.2026)

Изменения v2.2.0:
  - CONFIG_FILE перенесён в config/config_dashboard/ (новая структура)
  - Все QLabel явно получают color: #e6edf3 — исправлен чёрный текст
  - os.makedirs для папки config_dashboard при первом сохранении
"""

import os
import csv
import json
import time
from collections import deque
from datetime import datetime
from typing import Dict, List, Optional, Any

import numpy as np
import pyqtgraph as pg
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea, QGroupBox, QLabel,
    QPushButton, QComboBox, QCheckBox, QSpinBox, QDoubleSpinBox,
    QFormLayout, QDialog, QDialogButtonBox, QListWidget, QListWidgetItem,
    QMessageBox, QFileDialog, QLineEdit, QColorDialog, QInputDialog,
    QFrame, QSizePolicy, QSplitter, QTabWidget
)
from PyQt5.QtCore import QTimer, pyqtSignal, pyqtSlot, Qt, QThread
from PyQt5.QtGui import QColor, QFont

pg.setConfigOptions(antialias=True, foreground='#e6edf3', background='#0d1117')

# ── Пути конфигурации ─────────────────────────────────────────────────────────
_CONFIG_DIR  = os.path.join("config", "config_dashboard")
CONFIG_FILE  = os.path.join(_CONFIG_DIR, "dashboard_config.json")

_PALETTE = [
    "#00d4ff", "#ff6b6b", "#51cf66", "#ffd43b",
    "#cc5de8", "#ff922b", "#74c0fc", "#f06595",
]

# ── Общий стиль для диалогов ──────────────────────────────────────────────────
_DIALOG_STYLE = """
    QDialog, QWidget  { background-color: #0d1117; color: #e6edf3; }
    QLabel            { color: #e6edf3; background-color: transparent; }
    QGroupBox         { background-color: #161b22; border: 1px solid #30363d;
                        border-radius: 5px; margin-top: 10px; font-weight: bold; color: #e6edf3; }
    QGroupBox::title  { subcontrol-origin: margin; left: 10px; padding: 0 5px;
                        color: #00d4ff; }
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QListWidget {
        background-color: #0d1117; color: #e6edf3;
        border: 1px solid #30363d; padding: 4px; border-radius: 3px;
    }
    QTabWidget::pane  { background-color: #0d1117; border: none; }
    QTabBar::tab      { background: #21262d; color: #8b949e; padding: 6px 14px; }
    QTabBar::tab:selected { background: #0d1117; color: #00d4ff;
                            border-bottom: 2px solid #00d4ff; }
    QPushButton { background-color: #30363d; color: #e6edf3; border: none;
                  padding: 6px 14px; border-radius: 4px; font-weight: bold; }
    QPushButton:hover { background-color: #00d4ff; color: #0d1117; }
    QCheckBox   { color: #e6edf3; }
"""


# ──────────────────────────────────────────────────────────────
#  Диалог настройки стилей параметров (цвет + масштаб)
# ──────────────────────────────────────────────────────────────
class ParamStyleDialog(QDialog):
    def __init__(self, params: List[str], plot_config: Dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Цвета и масштабы")
        self.setMinimumWidth(420)
        self.params  = params
        self.colors  = plot_config.get("param_colors", {}).copy()
        self.scales  = plot_config.get("param_scales", {}).copy()
        self._build()
        self.setStyleSheet(_DIALOG_STYLE)

    def _build(self):
        layout = QVBoxLayout(self)
        self.color_btns: Dict[str, QPushButton] = {}
        self.scale_spins: Dict[str, QDoubleSpinBox] = {}

        for i, param in enumerate(self.params):
            color = self.colors.get(param, _PALETTE[i % len(_PALETTE)])
            row = QHBoxLayout()
            lbl = QLabel(param.split(":")[-1])
            lbl.setMinimumWidth(120)

            btn = QPushButton("●")
            btn.setFixedWidth(36)
            btn.setStyleSheet(f"color: {color}; font-size: 18px; border: none; background: transparent;")
            btn.clicked.connect(lambda _, p=param: self._pick(p))
            self.color_btns[param] = btn

            spin = QDoubleSpinBox()
            spin.setRange(0.0001, 1e6)
            spin.setDecimals(6)
            spin.setValue(self.scales.get(param, 1.0))
            self.scale_spins[param] = spin

            row.addWidget(lbl)
            row.addWidget(btn)
            row.addWidget(QLabel("× масштаб:"))
            row.addWidget(spin)
            layout.addLayout(row)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        layout.addWidget(bb)

    def _pick(self, param: str):
        c = QColorDialog.getColor(QColor(self.colors.get(param, "#00d4ff")), self)
        if c.isValid():
            self.colors[param] = c.name()
            self.color_btns[param].setStyleSheet(
                f"color: {c.name()}; font-size: 18px; border: none; background: transparent;")

    def get_styles(self) -> Dict:
        return {
            "colors": {p: self.colors.get(p, _PALETTE[i % len(_PALETTE)])
                       for i, p in enumerate(self.params)},
            "scales": {p: self.scale_spins[p].value() for p in self.params},
        }


# ──────────────────────────────────────────────────────────────
#  Диалог конфигурации одного графика
# ──────────────────────────────────────────────────────────────
class PlotConfigDialog(QDialog):
    def __init__(self, available_params: List[str],
                 plot_config: Optional[Dict] = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Настройка графика")
        self.setMinimumWidth(620)
        self.available_params = available_params
        self.cfg = plot_config if plot_config else {
            "name": "",
            "parameters": [],
            "param_colors": {},
            "param_scales": {},
            "etalon_param": None,
            "error_type": "relative",
            "nominal": 100.0,
            "tolerance": 1.0,
            "show_tolerance": True,
            "window_sec": 300,
        }
        self._build()
        self.setStyleSheet(_DIALOG_STYLE)
        self._sync_nominal()

    def _build(self):
        layout = QVBoxLayout(self)
        tabs = QTabWidget()

        tab_params = QWidget()
        fp = QFormLayout(tab_params)
        fp.setSpacing(8)

        self.name_edit = QLineEdit(self.cfg.get("name", ""))
        fp.addRow("Название:", self.name_edit)

        self.param_list = QListWidget()
        self.param_list.setSelectionMode(QListWidget.MultiSelection)
        self.param_list.setMinimumHeight(150)
        for p in self.available_params:
            item = QListWidgetItem(p)
            if p in self.cfg.get("parameters", []):
                item.setSelected(True)
            self.param_list.addItem(item)
        fp.addRow("Параметры:", self.param_list)

        style_btn = QPushButton("Цвета и масштабы…")
        style_btn.clicked.connect(self._open_styles)
        fp.addRow("", style_btn)

        self.window_spin = QSpinBox()
        self.window_spin.setRange(10, 86400)
        self.window_spin.setValue(self.cfg.get("window_sec", 300))
        self.window_spin.setSuffix(" с")
        fp.addRow("Окно отображения:", self.window_spin)

        tabs.addTab(tab_params, "Параметры")

        tab_err = QWidget()
        fe = QFormLayout(tab_err)
        fe.setSpacing(8)

        self.etalon_combo = QComboBox()
        self.etalon_combo.addItem("(нет)", None)
        for p in self.available_params:
            self.etalon_combo.addItem(p.split(":")[-1], p)
        idx = self.etalon_combo.findData(self.cfg.get("etalon_param"))
        if idx >= 0:
            self.etalon_combo.setCurrentIndex(idx)
        fe.addRow("Эталонный параметр:", self.etalon_combo)

        self.error_type_combo = QComboBox()
        self.error_type_combo.addItems(["Абсолютная (Δ)", "Относительная (δ, %)", "Приведённая (γ, %)"])
        _emap = {"absolute": 0, "relative": 1, "reduced": 2}
        self.error_type_combo.setCurrentIndex(
            _emap.get(self.cfg.get("error_type", "relative"), 1))
        self.error_type_combo.currentIndexChanged.connect(self._sync_nominal)
        fe.addRow("Тип погрешности:", self.error_type_combo)

        self.nominal_spin = QDoubleSpinBox()
        self.nominal_spin.setRange(1e-6, 1e9)
        self.nominal_spin.setValue(self.cfg.get("nominal", 100.0))
        self.nominal_spin.setDecimals(6)
        fe.addRow("Номинал (для γ):", self.nominal_spin)

        self.tolerance_spin = QDoubleSpinBox()
        self.tolerance_spin.setRange(0.0, 1e6)
        self.tolerance_spin.setValue(self.cfg.get("tolerance", 1.0))
        self.tolerance_spin.setDecimals(6)
        fe.addRow("Допуск ±:", self.tolerance_spin)

        self.show_tol_check = QCheckBox("Показывать коридор допуска")
        self.show_tol_check.setChecked(self.cfg.get("show_tolerance", True))
        fe.addRow("", self.show_tol_check)

        tabs.addTab(tab_err, "Погрешность")

        layout.addWidget(tabs)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        layout.addWidget(bb)

    def _open_styles(self):
        selected = [it.text() for it in self.param_list.selectedItems()]
        if not selected:
            QMessageBox.warning(self, "Предупреждение", "Выберите параметры.")
            return
        d = ParamStyleDialog(selected, self.cfg, self)
        if d.exec_():
            s = d.get_styles()
            self.cfg["param_colors"] = s["colors"]
            self.cfg["param_scales"] = s["scales"]

    def _sync_nominal(self):
        self.nominal_spin.setEnabled(self.error_type_combo.currentIndex() == 2)

    def get_config(self) -> Dict[str, Any]:
        selected = [it.text() for it in self.param_list.selectedItems()]
        _emap = {0: "absolute", 1: "relative", 2: "reduced"}
        return {
            "name":          self.name_edit.text().strip(),
            "parameters":    selected,
            "param_colors":  self.cfg.get("param_colors", {}),
            "param_scales":  self.cfg.get("param_scales", {}),
            "etalon_param":  self.etalon_combo.currentData(),
            "error_type":    _emap[self.error_type_combo.currentIndex()],
            "nominal":       self.nominal_spin.value(),
            "tolerance":     self.tolerance_spin.value(),
            "show_tolerance": self.show_tol_check.isChecked(),
            "window_sec":    self.window_spin.value(),
        }


# ──────────────────────────────────────────────────────────────
#  PlotPane — стабильный виджет одного графика
# ──────────────────────────────────────────────────────────────
class PlotPane(QWidget):
    MAX_POINTS = 8000

    def __init__(self, config: Dict, parent=None):
        super().__init__(parent)
        self.config = config
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        self._data:  Dict[str, deque] = {}
        self._error: Dict[str, deque] = {}
        self._last:  Dict[str, float] = {}

        self._main_curves:  Dict[str, pg.PlotDataItem] = {}
        self._error_curves: Dict[str, pg.PlotDataItem] = {}
        self._etalon_curve: Optional[pg.PlotDataItem]  = None
        self._tol_region:   Optional[pg.LinearRegionItem] = None

        self._val_labels: Dict[str, QLabel] = {}

        self._init_buffers()
        self._build_ui()

    def _init_buffers(self):
        etalon = self.config.get("etalon_param")
        all_keys = set(self.config.get("parameters", []))
        if etalon:
            all_keys.add(etalon)
        for k in all_keys:
            self._data[k] = deque(maxlen=self.MAX_POINTS)
        for p in self.config.get("parameters", []):
            if p != etalon and etalon:
                self._error[p] = deque(maxlen=self.MAX_POINTS)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 8)
        layout.setSpacing(3)

        header = QWidget()
        header.setFixedHeight(34)
        header.setStyleSheet(
            "background-color:#1c2128; border-radius:4px;"
            "border-left:3px solid #00d4ff;"
        )
        hl = QHBoxLayout(header)
        hl.setContentsMargins(10, 0, 10, 0)

        title_lbl = QLabel(self.config.get("name", "График"))
        title_lbl.setStyleSheet(
            "color:#00d4ff; font-weight:bold; font-size:13px; background:transparent;")
        hl.addWidget(title_lbl)
        hl.addStretch()

        params = self.config.get("parameters", [])
        for i, param in enumerate(params):
            color = self._color(param, i)
            lbl = QLabel(f"{param.split(':')[-1]}: —")
            lbl.setStyleSheet(
                f"color:{color}; font-size:12px; font-family:Consolas; "
                f"background:transparent; padding:0 8px;"
            )
            hl.addWidget(lbl)
            self._val_labels[param] = lbl

        layout.addWidget(header)

        date_axis = pg.DateAxisItem(orientation="bottom")
        date_axis.setStyle(tickFont=QFont("Consolas", 8))

        self._main_pw = pg.PlotWidget(axisItems={"bottom": date_axis})
        self._main_pw.setBackground("#0d1117")
        self._main_pw.showGrid(x=True, y=True, alpha=0.18)
        self._main_pw.setMinimumHeight(200)
        self._main_pw.getAxis("left").setStyle(tickFont=QFont("Consolas", 8))
        legend = self._main_pw.addLegend(
            offset=(10, 5), labelTextColor="#e6edf3",
            pen=pg.mkPen("#30363d"), brush=pg.mkBrush(22, 27, 34, 200)
        )

        etalon = self.config.get("etalon_param")

        if etalon:
            self._etalon_curve = self._main_pw.plot(
                [], [],
                pen=pg.mkPen(color="#8b949e", width=1, style=Qt.DashLine),
                name=f"{etalon.split(':')[-1]} (эталон)"
            )

        for i, param in enumerate(params):
            color = self._color(param, i)
            curve = self._main_pw.plot(
                [], [],
                pen=pg.mkPen(color=color, width=2),
                name=param.split(":")[-1]
            )
            self._main_curves[param] = curve

        layout.addWidget(self._main_pw, stretch=3)

        if etalon and params:
            err_date_axis = pg.DateAxisItem(orientation="bottom")
            err_date_axis.setStyle(tickFont=QFont("Consolas", 8))

            self._error_pw = pg.PlotWidget(axisItems={"bottom": err_date_axis})
            self._error_pw.setBackground("#0d1117")
            self._error_pw.showGrid(x=True, y=True, alpha=0.18)
            self._error_pw.setMinimumHeight(110)
            self._error_pw.setMaximumHeight(160)
            self._error_pw.getAxis("left").setStyle(tickFont=QFont("Consolas", 8))
            self._error_pw.addLegend(
                offset=(10, 5), labelTextColor="#e6edf3",
                pen=pg.mkPen("#30363d"), brush=pg.mkBrush(22, 27, 34, 200)
            )

            _ylabels = {"absolute": "Δ (ед.)", "relative": "δ (%)", "reduced": "γ (%)"}
            err_type = self.config.get("error_type", "relative")
            self._error_pw.setLabel("left", _ylabels[err_type],
                                    color="#8b949e", size="9pt")

            self._error_pw.addItem(
                pg.InfiniteLine(0, angle=0,
                                pen=pg.mkPen("#30363d", width=1, style=Qt.DashLine))
            )

            tol = self.config.get("tolerance", 0.0)
            if tol > 0 and self.config.get("show_tolerance", True):
                self._tol_region = pg.LinearRegionItem(
                    values=[-tol, tol],
                    orientation="horizontal",
                    brush=pg.mkBrush(63, 185, 80, 35),
                    movable=False
                )
                self._tol_region.setZValue(-10)
                self._error_pw.addItem(self._tol_region)

                for sign in (1, -1):
                    self._error_pw.addItem(
                        pg.InfiniteLine(
                            sign * tol, angle=0,
                            pen=pg.mkPen("#3fb950", width=1, style=Qt.DotLine)
                        )
                    )

            for i, param in enumerate(params):
                if param != etalon:
                    color = self._color(param, i)
                    c = self._error_pw.plot(
                        [], [],
                        pen=pg.mkPen(color=color, width=1.5),
                        name=f"Δ {param.split(':')[-1]}"
                    )
                    self._error_curves[param] = c

            layout.addWidget(self._error_pw, stretch=1)
        else:
            self._error_pw = None

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color:#21262d;")
        layout.addWidget(sep)

    def _color(self, param: str, index: int = 0) -> str:
        custom = self.config.get("param_colors", {})
        return custom.get(param, _PALETTE[index % len(_PALETTE)])

    def receive(self, key: str, ts: float, value: float):
        if key not in self._data:
            return

        scale = self.config.get("param_scales", {}).get(key, 1.0)
        scaled = value * (scale if scale else 1.0)
        self._data[key].append((ts, scaled))
        self._last[key] = scaled

        if key in self._val_labels:
            self._val_labels[key].setText(
                f"{key.split(':')[-1]}: {scaled:.5g}"
            )

        etalon = self.config.get("etalon_param")
        if etalon and key != etalon and key in self._error:
            et_buf = self._data.get(etalon)
            if et_buf:
                et_val = et_buf[-1][1]
                err = self._calc_error(scaled, et_val)
                self._error[key].append((ts, err))

        self._refresh()

    def _calc_error(self, measured: float, etalon: float) -> float:
        etype = self.config.get("error_type", "relative")
        if etype == "absolute":
            return measured - etalon
        elif etype == "relative":
            return (measured - etalon) / abs(etalon) * 100.0 if etalon != 0 else 0.0
        else:
            nom = self.config.get("nominal", 1.0)
            return (measured - etalon) / abs(nom) * 100.0 if nom != 0 else 0.0

    def _refresh(self):
        window_sec = self.config.get("window_sec", 300)
        all_last_ts = [buf[-1][0] for buf in self._data.values() if buf]
        if not all_last_ts:
            return
        x_max = max(all_last_ts)
        x_min = x_max - window_sec

        def _filter(buf: deque):
            pts = [(t, v) for t, v in buf if t >= x_min]
            if pts:
                xs, ys = zip(*pts)
                return list(xs), list(ys)
            return [], []

        etalon = self.config.get("etalon_param")
        if self._etalon_curve and etalon and etalon in self._data:
            xs, ys = _filter(self._data[etalon])
            self._etalon_curve.setData(xs, ys)

        for param, curve in self._main_curves.items():
            if param in self._data:
                xs, ys = _filter(self._data[param])
                curve.setData(xs, ys)

        for param, curve in self._error_curves.items():
            if param in self._error:
                xs, ys = _filter(self._error[param])
                curve.setData(xs, ys)

    def clear_data(self):
        for buf in self._data.values():
            buf.clear()
        for buf in self._error.values():
            buf.clear()
        self._last.clear()
        for lbl in self._val_labels.values():
            lbl.setText(f"{lbl.text().split(':')[0]}: —")
        self._refresh()


# ──────────────────────────────────────────────────────────────
#  CSV-ридер (поток для исторических данных)
# ──────────────────────────────────────────────────────────────
class DataReaderThread(QThread):
    new_rows = pyqtSignal(list)

    def __init__(self, history_file: str, interval: float = 2.0):
        super().__init__()
        self.history_file = history_file
        self.interval     = interval
        self._running     = False
        self._pos         = 0

    def run(self):
        self._running = True
        while self._running:
            rows = self._read_new()
            if rows:
                self.new_rows.emit(rows)
            self.msleep(int(self.interval * 1000))

    def _read_new(self) -> list:
        rows = []
        if not os.path.exists(self.history_file):
            return rows
        try:
            with open(self.history_file, "r", encoding="utf-8") as f:
                f.seek(self._pos)
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("Timestamp"):
                        continue
                    parts = line.split(";")
                    if len(parts) >= 4:
                        try:
                            ts = datetime.strptime(parts[0], "%Y-%m-%d %H:%M:%S").timestamp()
                            key = f"{parts[1]}:{parts[2]}"
                            val = float(parts[3])
                            rows.append((ts, key, val))
                        except Exception:
                            pass
                self._pos = f.tell()
        except Exception as e:
            print(f"[DataReaderThread] {e}")
        return rows

    def stop(self):
        self._running = False
        self.quit()
        self.wait(3000)

    def reset(self):
        self._pos = 0


# ──────────────────────────────────────────────────────────────
#  Dashboard — главная панель графиков
# ──────────────────────────────────────────────────────────────
class Dashboard(QWidget):
    def __init__(self, device_panels: List = None, parent=None):
        super().__init__(parent)
        self.device_panels: List = device_panels or []
        self.history_file:  Optional[str] = None

        self._panes:  List[PlotPane] = []
        self._plots:  List[Dict]     = []
        self._reader: Optional[DataReaderThread] = None

        self._recording = False
        self._report_file = None
        self._report_writer = None
        self._report_params = []

        self._build_ui()
        self._apply_styles()
        self._load_config()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(6)

        bar = QHBoxLayout()
        bar.setSpacing(6)

        def _btn(text, slot, enabled=True) -> QPushButton:
            b = QPushButton(text)
            b.clicked.connect(slot)
            b.setEnabled(enabled)
            return b

        self._start_btn = _btn("▶ История", self._start_reader)
        self._stop_btn  = _btn("■ Стоп",    self._stop_reader, False)
        self._clear_btn = _btn("🗑 Очистить", self._clear_all)

        self._interval_spin = QDoubleSpinBox()
        self._interval_spin.setRange(0.5, 60.0)
        self._interval_spin.setValue(2.0)
        self._interval_spin.setSuffix(" с")
        self._interval_spin.setFixedWidth(80)

        self._add_btn    = _btn("➕ Добавить",   self._add_plot)
        self._edit_btn   = _btn("✎ Настроить",   self._edit_plot)
        self._remove_btn = _btn("❌ Удалить",    self._remove_plot)

        self._report_btn = QPushButton("📝 Запись отчёта")
        self._report_btn.setCheckable(True)
        self._report_btn.toggled.connect(self._toggle_report_recording)

        interval_lbl = QLabel("Интервал CSV:")
        interval_lbl.setStyleSheet("color: #e6edf3;")

        for w in (self._start_btn, self._stop_btn, self._clear_btn,
                  interval_lbl, self._interval_spin,
                  _make_separator(), self._add_btn,
                  self._edit_btn, self._remove_btn,
                  _make_separator(), self._report_btn):
            bar.addWidget(w)
        bar.addStretch()

        root.addLayout(bar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea { border: none; background: #0d1117; }")

        self._container = QWidget()
        self._container.setStyleSheet("background: #0d1117;")
        self._vbox = QVBoxLayout(self._container)
        self._vbox.setContentsMargins(0, 0, 0, 0)
        self._vbox.setSpacing(6)
        self._vbox.addStretch()

        scroll.setWidget(self._container)
        root.addWidget(scroll, stretch=1)

    @pyqtSlot(str, str, float, str)
    def receive_data(self, device: str, param: str, value: float, unit: str):
        pass  # данные идут только через DataReaderThread

    def _toggle_report_recording(self, checked: bool):
        if checked:
            self._start_report_recording()
        else:
            self._stop_report_recording()

    def _start_report_recording(self):
        params = set()
        for plot in self._plots:
            params.update(plot.get("parameters", []))
        if not params:
            QMessageBox.warning(self, "Запись", "Нет параметров для записи.")
            self._report_btn.setChecked(False)
            return
        self._report_params = sorted(params)
        filename = f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        try:
            self._report_file = open(filename, 'w', newline='', encoding='utf-8')
            self._report_writer = csv.writer(self._report_file, delimiter=';')
            self._report_writer.writerow(["Timestamp"] + self._report_params)
            self._recording = True
            self._log(f"Запись отчёта начата: {filename}")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось создать файл отчёта:\n{e}")
            self._report_btn.setChecked(False)

    def _stop_report_recording(self):
        if self._report_file:
            self._report_file.close()
            self._report_file = None
            self._report_writer = None
        self._recording = False
        self._log("Запись отчёта остановлена")

    def _log(self, msg: str):
        print(f"[Dashboard] {msg}")

    def set_history_file(self, filename: str):
        self.history_file = filename

    def _start_reader(self):
        if not self.history_file:
            QMessageBox.warning(self, "История", "Файл истории не задан.")
            return
        if self._reader and self._reader.isRunning():
            return
        self._reader = DataReaderThread(self.history_file,
                                        self._interval_spin.value())
        self._reader.new_rows.connect(self._on_csv_rows)
        self._reader.start()
        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)

    def _stop_reader(self):
        if self._reader:
            self._reader.stop()
            self._reader = None
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)

    @pyqtSlot(list)
    def _on_csv_rows(self, rows: list):
        for ts, key, value in rows:
            for pane in self._panes:
                pane.receive(key, ts, value)

        if self._recording and rows:
            self._write_report_from_panes(rows[-1][0])

    def _write_report_from_panes(self, ts: float):
        scaled_last: Dict[str, float] = {}
        for pane in self._panes:
            scaled_last.update(pane._last)

        if not all(p in scaled_last for p in self._report_params):
            return

        ts_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
        row = [ts_str] + [str(scaled_last.get(p, "")) for p in self._report_params]
        if self._report_writer:
            self._report_writer.writerow(row)

    def _clear_all(self):
        fname = self.history_file or ""
        msg = (
            "Будут очищены графики и файл истории.\n"
            "Данные из файла удалятся безвозвратно.\n\n"
            f"Файл: {fname or '(не задан)'}\n\n"
            "Продолжить?"
        )
        reply = QMessageBox.question(
            self, "Очистить всё", msg,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        was_running = self._reader is not None and self._reader.isRunning()
        if was_running:
            self._stop_reader()

        for pane in self._panes:
            pane.clear_data()

        if self.history_file and os.path.exists(self.history_file):
            try:
                open(self.history_file, "w", encoding="utf-8").close()
                self._log(f"Файл истории очищен: {self.history_file}")
            except Exception as e:
                QMessageBox.warning(self, "Ошибка", f"Не удалось очистить файл истории:\n{e}")

        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)

    def _available_params(self) -> List[str]:
        params = []
        for panel in self.device_panels:
            for name in panel.parameters.keys():
                params.append(f"{panel.device_name}:{name}")
        return params

    def _add_plot(self):
        params = self._available_params()
        if not params:
            QMessageBox.warning(self, "Добавление",
                "Нет доступных параметров.\n"
                "Добавьте устройства и настройте их параметры.")
            return
        d = PlotConfigDialog(params, parent=self)
        if d.exec_():
            cfg = d.get_config()
            if not cfg["name"]:
                QMessageBox.warning(self, "Ошибка", "Укажите название графика.")
                return
            if not cfg["parameters"]:
                QMessageBox.warning(self, "Ошибка", "Выберите хотя бы один параметр.")
                return
            self._plots.append(cfg)
            self._append_pane(cfg)
            self._save_config()

    def _edit_plot(self):
        if not self._plots:
            return
        names = [c.get("name", f"График {i+1}") for i, c in enumerate(self._plots)]
        name, ok = QInputDialog.getItem(self, "Редактировать", "Выберите график:", names, 0, False)
        if not ok:
            return
        idx = names.index(name)
        params = self._available_params()
        d = PlotConfigDialog(params, self._plots[idx], self)
        if d.exec_():
            new_cfg = d.get_config()
            self._plots[idx] = new_cfg
            old_pane = self._panes[idx]
            new_pane = PlotPane(new_cfg)
            self._vbox.removeWidget(old_pane)
            old_pane.deleteLater()
            self._panes[idx] = new_pane
            self._vbox.insertWidget(idx, new_pane)
            self._save_config()

    def _remove_plot(self):
        if not self._plots:
            return
        names = [c.get("name", f"График {i+1}") for i, c in enumerate(self._plots)]
        name, ok = QInputDialog.getItem(self, "Удалить", "Выберите график:", names, 0, False)
        if not ok:
            return
        idx = names.index(name)
        pane = self._panes.pop(idx)
        self._vbox.removeWidget(pane)
        pane.deleteLater()
        self._plots.pop(idx)
        self._save_config()

    def _append_pane(self, cfg: Dict):
        pane = PlotPane(cfg)
        self._panes.append(pane)
        insert_pos = self._vbox.count() - 1
        self._vbox.insertWidget(insert_pos, pane)

    def _save_config(self):
        try:
            os.makedirs(_CONFIG_DIR, exist_ok=True)
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump({"plots": self._plots}, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[Dashboard] Ошибка сохранения конфига: {e}")

    def _load_config(self):
        if not os.path.exists(CONFIG_FILE):
            return
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for cfg in data.get("plots", []):
                self._plots.append(cfg)
                self._append_pane(cfg)
        except Exception as e:
            print(f"[Dashboard] Ошибка загрузки конфига: {e}")

    def _apply_styles(self):
        self.setStyleSheet("""
            QWidget { background:#0d1117; color:#e6edf3; font-family:Consolas; }
            QLabel  { color:#e6edf3; background-color: transparent; }
            QPushButton {
                background:#21262d; border:none; border-radius:4px;
                padding:5px 11px; color:#e6edf3; font-weight:bold;
            }
            QPushButton:hover   { background:#00d4ff; color:#0d1117; }
            QPushButton:checked { background:#3fb950; color:#0d1117; }
            QPushButton:disabled{ background:#161b22; color:#484f58; }
            QDoubleSpinBox {
                background:#0d1117; border:1px solid #30363d;
                color:#e6edf3; border-radius:3px; padding:3px;
            }
            QScrollBar:vertical {
                background:#0d1117; width:8px; border-radius:4px;
            }
            QScrollBar::handle:vertical { background:#30363d; border-radius:4px; }
            QScrollBar::handle:vertical:hover { background:#00d4ff; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
        """)

    def closeEvent(self, event):
        self._stop_reader()
        self._stop_report_recording()
        event.accept()


def _make_separator() -> QFrame:
    sep = QFrame()
    sep.setFrameShape(QFrame.VLine)
    sep.setStyleSheet("color:#30363d;")
    sep.setFixedHeight(22)
    return sep
