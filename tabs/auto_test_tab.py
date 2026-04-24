"""
auto_test_tab.py  v2.1.0  (2026-04-24)

Изменения v2.1.0:
  - GENERATOR_CONFIGS — реестр генераторов с описанием полей и колонок уставок.
    Для добавления нового генератора достаточно добавить запись в GENERATOR_CONFIGS.
  - Динамическая вкладка «Генератор» в редакторе методики:
    при смене типа появляются нужные поля (скорость по умолчанию, kV/Вт для Mantigora).
  - Динамические колонки таблицы точек: PTS — Ua/Ub/Uc/Ia/.../f, Mantigora — U/I.
    При смене типа метки и задержки сохраняются, колонки уставок перестраиваются.
  - _GeneratorPanelProxy — fallback если generators/__init__.py не содержит
    create_generator(). Ищет нужную панель среди уже подключённых device_panels.
  - Всё остальное из v2.0.0 сохранено без изменений.
"""

import csv
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

import pyqtgraph as pg
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QFont
from PyQt5.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QFrame, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMessageBox, QProgressBar,
    QPushButton, QScrollArea, QSpinBox, QSplitter, QStackedWidget,
    QTableWidget, QTableWidgetItem,
    QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)

pg.setConfigOptions(antialias=True, foreground="#e6edf3", background="#0d1117")

# ── Палитра ───────────────────────────────────────────────────────────────────
CLR_OK     = "#3fb950"
CLR_NG     = "#f85149"
CLR_WARN   = "#ffd43b"
CLR_BG     = "#0d1117"
CLR_PANEL  = "#161b22"
CLR_BORDER = "#30363d"
CLR_ACCENT = "#00d4ff"

ERR_COLORS = {"abs": "#74c0fc", "rel": "#ffd43b", "red": "#ff922b"}

ERR_TYPES = {
    "abs": "Абсолютная  Δ",
    "rel": "Относительная  δ (%)",
    "red": "Приведённая  γ (%)",
}
ERR_TYPE_KEYS = list(ERR_TYPES.keys())
ERR_SHORT = {"abs": "Δ", "rel": "δ (%)", "red": "γ (%)"}

_PARAM_PALETTE = [
    "#74c0fc", "#a9e34b", "#ffd43b", "#f87171", "#c084fc",
    "#fb923c", "#34d399", "#60a5fa", "#f472b6", "#2dd4bf",
]

_ERR_LINE_STYLE = {
    "abs": Qt.SolidLine,
    "rel": Qt.DashLine,
    "red": Qt.DotLine,
}

# ── Фиксированные индексы колонок ─────────────────────────────────────────────
PT_LABEL_COL    = 0
PT_SETTLING_COL = 1
PT_CH_START     = 2

# ─────────────────────────────────────────────────────────────────────────────
# Реестр генераторов
# ─────────────────────────────────────────────────────────────────────────────
#
# Чтобы добавить новый генератор:
#   1. Добавь запись в GENERATOR_CONFIGS.
#   2. В MethodologyDialog._build_gen_specific_page() добавь страницу для
#      специфичных полей (или оставь пустую страницу если доп. полей нет).
#   3. В _GeneratorPanelProxy.connect() добавь ветку для нового типа.
#   4. В _GeneratorPanelProxy.set_point() добавь ветку для нового типа.
#
GENERATOR_CONFIGS = {
    "PTS": {
        "display":          "PTS (AC трёхфазный калибратор)",
        "default_baudrate": "19200",
        "setpoint_cols": [
            ("Ua",    "Ua (В)"),
            ("Ub",    "Ub (В)"),
            ("Uc",    "Uc (В)"),
            ("Ia",    "Ia (А)"),
            ("Ib",    "Ib (А)"),
            ("Ic",    "Ic (А)"),
            ("phi_a", "φa (°)"),
            ("phi_b", "φb (°)"),
            ("phi_c", "φc (°)"),
            ("f",     "f (Гц)"),
        ],
    },
    "Mantigora": {
        "display":          "Mantigora HT/HP (DC высоковольтный)",
        "default_baudrate": "38400",
        "setpoint_cols": [
            ("voltage",    "U (В)"),
            ("current_ma", "I (мА)"),
        ],
    },
    # Шаблон для будущего генератора:
    # "МойГенератор": {
    #     "display":          "Мой генератор (описание)",
    #     "default_baudrate": "9600",
    #     "setpoint_cols": [("param1", "Параметр 1"), ...],
    # },
}

GENERATOR_TYPES = list(GENERATOR_CONFIGS.keys())


# ── Фабрика генераторов ───────────────────────────────────────────────────────

def create_generator(config: dict):
    """
    Создать объект генератора по конфигурации из методики.

    Сначала пробуем импортировать из generators/__init__.py.
    Если там нет create_generator — используем _GeneratorPanelProxy,
    который ищет нужную панель среди уже подключённых device_panels.
    """
    try:
        from generators import create_generator as _mod_create
        g = _mod_create(config)
        # Добавить shim set_point / output_off если отсутствуют
        if not hasattr(g, 'set_point'):
            g.set_point = lambda sp: g.apply_settings(sp)
        if not hasattr(g, 'output_off'):
            g.output_off = lambda: g.enable_output(False)
        return g
    except (ImportError, AttributeError, KeyError):
        return _GeneratorPanelProxy(config)


# ─────────────────────────────────────────────────────────────────────────────
# Прокси-генератор (fallback)
# ─────────────────────────────────────────────────────────────────────────────

class _GeneratorPanelProxy:
    """
    Ищет подходящую панель в device_panels и делегирует ей команды.
    Работает если панель уже подключена пользователем в UI.
    Не требует изменений в модуле generators/.
    """

    def __init__(self, config: dict):
        self._config      = config
        self._panel       = None
        self.is_connected = False

    # ── Подключение ──────────────────────────────────────────────────────────
    def connect(self, device_panels: list) -> bool:
        gen_type = self._config.get('type', '')
        for panel in device_panels:
            cls  = type(panel).__name__
            name = getattr(panel, 'device_name', '')
            conn = getattr(panel, 'is_connected', False)

            matched = False
            if gen_type == 'PTS' and conn:
                matched = 'PTS' in cls or 'PTS' in name or 'pts' in name.lower()
            elif gen_type == 'Mantigora' and conn:
                matched = ('Mantigora' in cls or 'Mantigora' in name
                           or 'mantigora' in name.lower())

            # Добавляй ветки для новых типов здесь:
            # elif gen_type == 'МойГенератор' and conn:
            #     matched = 'МойКласс' in cls

            if matched:
                self._panel = panel
                self.is_connected = True
                return True

        return False

    # ── Уставки ──────────────────────────────────────────────────────────────
    def set_point(self, setpoints: dict):
        if not self._panel:
            raise RuntimeError("Генератор не подключён (панель не найдена)")

        gen_type = self._config.get('type', '')
        drv = getattr(self._panel, 'driver', None)

        if gen_type == 'PTS':
            # Пробуем apply_settings() на панели или генераторе
            if hasattr(self._panel, 'apply_settings'):
                self._panel.apply_settings(setpoints)
            elif drv and hasattr(drv, 'apply_settings'):
                drv.apply_settings(setpoints)
            elif hasattr(self._panel, 'generator'):
                self._panel.generator.apply_settings(setpoints)

        elif gen_type == 'Mantigora':
            # MantigoraDriver: set_voltage / set_current_limit / start
            # Минимальное рабочее напряжение = 10 В.
            # 0 В → выключить выход (stop), не передавать на устройство.
            # 0 < U < 10 → также stop (устройство не принимает такие значения).
            if drv:
                voltage    = float(setpoints.get('voltage', 0.0))
                current_ma = float(setpoints.get('current_ma', 0.0))
                if voltage == 0.0 or voltage < 10.0:
                    drv.stop()
                else:
                    drv.set_voltage(voltage)
                    drv.set_current_limit(current_ma)
                    drv.start()

        # Добавляй ветки для новых типов здесь

    # ── Выключение выхода ────────────────────────────────────────────────────
    def output_off(self):
        if not self._panel:
            return
        gen_type = self._config.get('type', '')
        drv = getattr(self._panel, 'driver', None)

        if gen_type == 'Mantigora' and drv:
            drv.stop()
        elif gen_type == 'PTS':
            if hasattr(self._panel, 'generator'):
                try:
                    self._panel.generator.enable_output(False)
                except Exception:
                    pass
            elif drv and hasattr(drv, 'enable_output'):
                drv.enable_output(False)


# ─────────────────────────────────────────────────────────────────────────────
# Структуры данных (без изменений из v2.0.0)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ToleranceSpec:
    error_type: str   = "rel"
    tolerance:  float = 0.5
    nominal:    float = 100.0

    @property
    def label(self) -> str:
        return f"{ERR_SHORT.get(self.error_type, self.error_type)} ≤ ±{self.tolerance}"

    def to_dict(self) -> dict:
        return {"error_type": self.error_type,
                "tolerance": self.tolerance,
                "nominal": self.nominal}

    @classmethod
    def from_dict(cls, d: dict) -> "ToleranceSpec":
        return cls(
            error_type=d.get("error_type", "rel"),
            tolerance=float(d.get("tolerance", 0.5)),
            nominal=float(d.get("nominal", 100.0)),
        )


@dataclass
class ParameterLink:
    name:            str   = ""
    etalon_device:   str   = ""
    etalon_param:    str   = ""
    etalon_scale:    float = 1.0
    measured_device: str   = ""
    measured_param:  str   = ""
    measured_scale:  float = 1.0
    tolerances:      List[ToleranceSpec] = field(
        default_factory=lambda: [ToleranceSpec()])

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "etalon_device": self.etalon_device,
            "etalon_param": self.etalon_param,
            "etalon_scale": self.etalon_scale,
            "measured_device": self.measured_device,
            "measured_param": self.measured_param,
            "measured_scale": self.measured_scale,
            "tolerances": [t.to_dict() for t in self.tolerances],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ParameterLink":
        obj = cls(
            name=d.get("name", ""),
            etalon_device=d.get("etalon_device", ""),
            etalon_param=d.get("etalon_param", ""),
            etalon_scale=float(d.get("etalon_scale", 1.0)),
            measured_device=d.get("measured_device", ""),
            measured_param=d.get("measured_param", ""),
            measured_scale=float(d.get("measured_scale", 1.0)),
        )
        if "tolerances" in d:
            obj.tolerances = [ToleranceSpec.from_dict(t) for t in d["tolerances"]]
        elif "error_type" in d:
            obj.tolerances = [ToleranceSpec(
                d.get("error_type", "rel"),
                float(d.get("tolerance", 0.5)),
                float(d.get("nominal", 100.0)),
            )]
        return obj


@dataclass
class TestPoint:
    label:         str              = ""
    setpoints:     Dict[str, float] = field(default_factory=dict)
    settling_time: Optional[float]  = None

    etalon_vals:   Dict[str, float]            = field(default_factory=dict)
    measured_vals: Dict[str, float]            = field(default_factory=dict)
    errors:        Dict[str, Dict[str, float]] = field(default_factory=dict)
    passed:        Dict[str, bool]             = field(default_factory=dict)
    done:          bool                        = False

    def clear_results(self):
        self.etalon_vals.clear()
        self.measured_vals.clear()
        self.errors.clear()
        self.passed.clear()
        self.done = False

    def overall_passed(self) -> Optional[bool]:
        if not self.done or not self.passed:
            return None
        return all(self.passed.values())

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "setpoints": self.setpoints,
            "settling_time": self.settling_time,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TestPoint":
        return cls(
            label=d.get("label", ""),
            setpoints=d.get("setpoints", {}),
            settling_time=d.get("settling_time"),
        )


# ─────────────────────────────────────────────────────────────────────────────
# Поток автоматического прогона (без изменений из v2.0.0)
# ─────────────────────────────────────────────────────────────────────────────

class AutoRunThread(QThread):
    point_started = pyqtSignal(int)
    sample_tick   = pyqtSignal(int, int)
    point_done    = pyqtSignal(int, object)
    run_finished  = pyqtSignal(bool, str)
    log_msg       = pyqtSignal(str)

    def __init__(self, generator, device_panels: list,
                 links: List[ParameterLink], points: List[TestPoint],
                 n_samples: int, sample_interval_ms: int,
                 default_settling: float):
        super().__init__()
        self._gen      = generator
        self._panels   = device_panels
        self._links    = links
        self._points   = points
        self._n        = n_samples
        self._interval = sample_interval_ms / 1000.0
        self._settling = default_settling
        self._stop     = False

    def stop(self):
        self._stop = True

    def _sleep(self, seconds: float):
        t0 = time.time()
        while time.time() - t0 < seconds:
            if self._stop:
                return
            time.sleep(0.05)

    def _collect_fresh(self) -> dict:
        needed = set()
        for lnk in self._links:
            needed.add(lnk.etalon_device)
            needed.add(lnk.measured_device)

        result = {}
        for panel in self._panels:
            if getattr(panel, 'device_name', '') not in needed:
                continue
            try:
                vals = panel.read_device_values()
            except Exception:
                vals = {}
            if not vals:
                vals = dict(getattr(panel, 'last_values', {}))
            for param, val in vals.items():
                result[(panel.device_name, param)] = val
        return result

    def run(self):
        try:
            if not self._gen.is_connected:
                self.log_msg.emit("Подключение к генератору...")
                self._gen.connect(self._panels)
                self.log_msg.emit("✔ Генератор подключён")

            for idx, point in enumerate(self._points):
                if self._stop:
                    break

                point.clear_results()
                self.point_started.emit(idx)
                self.log_msg.emit(
                    f"\n▶ [{idx+1}/{len(self._points)}]  {point.label}")

                self.log_msg.emit(f"   Уставки: {point.setpoints}")
                self._gen.set_point(point.setpoints)

                settling = (point.settling_time
                            if point.settling_time is not None
                            else self._settling)
                self.log_msg.emit(f"   Ожидание {settling} с...")
                self._sleep(settling)
                if self._stop:
                    break

                buf: Dict[str, Dict[int, List]] = {
                    lnk.name: {i: [] for i in range(len(lnk.tolerances))}
                    for lnk in self._links
                }

                for s in range(self._n):
                    if self._stop:
                        break
                    fresh = self._collect_fresh()
                    for lnk in self._links:
                        v_et = fresh.get((lnk.etalon_device,   lnk.etalon_param))
                        v_ms = fresh.get((lnk.measured_device, lnk.measured_param))
                        if v_et is not None and v_ms is not None:
                            et_s = v_et * lnk.etalon_scale
                            ms_s = v_ms * lnk.measured_scale
                            for ti in range(len(lnk.tolerances)):
                                buf[lnk.name][ti].append((et_s, ms_s))
                    self.sample_tick.emit(s + 1, self._n)
                    self._sleep(self._interval)

                if self._stop:
                    break

                for lnk in self._links:
                    if not any(buf[lnk.name][ti]
                               for ti in range(len(lnk.tolerances))):
                        self.log_msg.emit(f"   ⚠ Нет данных для '{lnk.name}'")
                        continue

                    param_errors: Dict[str, float] = {}
                    param_passed = True
                    worst_et = None
                    worst_ms = None

                    for ti, tspec in enumerate(lnk.tolerances):
                        pairs = buf[lnk.name][ti]
                        if not pairs:
                            continue

                        worst_err = None
                        for et_v, ms_v in pairs:
                            delta = ms_v - et_v
                            if tspec.error_type == "abs":
                                err = delta
                            elif tspec.error_type == "rel":
                                err = (delta / et_v * 100.0) if et_v else 0.0
                            else:
                                err = (delta / tspec.nominal * 100.0) \
                                      if tspec.nominal else 0.0

                            if worst_err is None or abs(err) > abs(worst_err):
                                worst_err = err
                                worst_et  = et_v
                                worst_ms  = ms_v

                        if worst_err is not None:
                            param_errors[tspec.error_type] = worst_err
                            if abs(worst_err) > tspec.tolerance:
                                param_passed = False

                    point.etalon_vals[lnk.name]   = worst_et
                    point.measured_vals[lnk.name] = worst_ms
                    point.errors[lnk.name]        = param_errors
                    point.passed[lnk.name]        = param_passed

                point.done = True
                ok = point.overall_passed()
                errs_str = "  ".join(
                    f"{k}=" + "/".join(f"{v:+.3f}" for v in e.values())
                    for k, e in point.errors.items()
                )
                self.log_msg.emit(
                    f"   {'✅ OK' if ok else '❌ NG'}  {errs_str}")
                self.point_done.emit(idx, point)

            try:
                self._gen.output_off()
                self.log_msg.emit("\nГенератор выключен.")
            except Exception as e:
                self.log_msg.emit(f"\n⚠ Не удалось выключить генератор: {e}")

            if self._stop:
                self.run_finished.emit(False, "Остановлено пользователем")
            else:
                self.run_finished.emit(True, "Прогон завершён успешно")

        except Exception as e:
            self.run_finished.emit(False, f"Ошибка: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Таблица допусков (без изменений из v2.0.0)
# ─────────────────────────────────────────────────────────────────────────────

class ToleranceTableWidget(QWidget):

    def __init__(self, tolerances: List[ToleranceSpec] = None, parent=None):
        super().__init__(parent)
        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(4)

        bar = QHBoxLayout()
        add_btn = QPushButton("➕ Добавить допуск")
        del_btn = QPushButton("❌ Удалить")
        add_btn.clicked.connect(lambda: self._add_row())
        del_btn.clicked.connect(self._del_row)
        bar.addWidget(add_btn)
        bar.addWidget(del_btn)
        bar.addStretch()
        vbox.addLayout(bar)

        self._tbl = QTableWidget(0, 3)
        self._tbl.setHorizontalHeaderLabels(
            ["Тип погрешности", "Допуск ±", "Нормирующее значение"])
        hh = self._tbl.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._tbl.setSelectionBehavior(QTableWidget.SelectRows)
        self._tbl.setMaximumHeight(160)
        vbox.addWidget(self._tbl)

        for t in (tolerances or [ToleranceSpec()]):
            self._add_row(t)

    def _add_row(self, tspec: ToleranceSpec = None):
        if not isinstance(tspec, ToleranceSpec):
            tspec = ToleranceSpec()
        r = self._tbl.rowCount()
        self._tbl.insertRow(r)

        combo = QComboBox()
        combo.setStyleSheet("""
            QComboBox {
                background: #161b22; color: #e6edf3;
                border: 1px solid #30363d; border-radius: 3px; padding: 2px 4px;
            }
            QComboBox::drop-down { width: 22px; border-left: 1px solid #30363d; }
            QComboBox::down-arrow {
                width: 0; height: 0;
                border-left: 5px solid transparent;
                border-right: 5px solid transparent;
                border-top: 6px solid #e6edf3;
            }
            QComboBox QAbstractItemView {
                background: #161b22; color: #e6edf3;
                border: 1px solid #30363d;
                selection-background-color: #00d4ff;
                selection-color: #0d1117;
            }
        """)
        for k, v in ERR_TYPES.items():
            combo.addItem(v, k)
        if tspec.error_type in ERR_TYPE_KEYS:
            combo.setCurrentIndex(ERR_TYPE_KEYS.index(tspec.error_type))
        self._tbl.setCellWidget(r, 0, combo)

        tol_spin = QDoubleSpinBox()
        tol_spin.setRange(0.0, 1e9)
        tol_spin.setDecimals(4)
        tol_spin.setValue(tspec.tolerance)
        self._tbl.setCellWidget(r, 1, tol_spin)

        nom_spin = QDoubleSpinBox()
        nom_spin.setRange(1e-9, 1e9)
        nom_spin.setDecimals(4)
        nom_spin.setValue(tspec.nominal)
        self._tbl.setCellWidget(r, 2, nom_spin)

    def _del_row(self):
        r = self._tbl.currentRow()
        if r >= 0 and self._tbl.rowCount() > 1:
            self._tbl.removeRow(r)

    def get_tolerances(self) -> List[ToleranceSpec]:
        result = []
        for r in range(self._tbl.rowCount()):
            combo = self._tbl.cellWidget(r, 0)
            tol   = self._tbl.cellWidget(r, 1)
            nom   = self._tbl.cellWidget(r, 2)
            if combo and tol and nom:
                result.append(ToleranceSpec(
                    error_type=combo.currentData(),
                    tolerance=tol.value(),
                    nominal=nom.value(),
                ))
        return result or [ToleranceSpec()]


# ─────────────────────────────────────────────────────────────────────────────
# Диалог редактирования пары параметров (без изменений из v2.0.0)
# ─────────────────────────────────────────────────────────────────────────────

class ParameterLinkDialog(QDialog):

    def __init__(self, device_panels: list,
                 link: Optional[ParameterLink] = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Параметр")
        self.setMinimumWidth(560)
        self._panels = device_panels
        lnk = link or ParameterLink()

        fl = QFormLayout(self)
        fl.setSpacing(10)
        fl.setContentsMargins(20, 20, 20, 20)

        self._name = QLineEdit(lnk.name)
        self._name.setPlaceholderText("Например: Ua")
        fl.addRow("Имя параметра:", self._name)
        fl.addRow(_sep())

        self._et_dev = QComboBox()
        self._et_par = QComboBox()
        self._et_scale = _dbl(lnk.etalon_scale, -1e9, 1e9, 6)
        self._et_dev.addItems([p.device_name for p in device_panels])
        if lnk.etalon_device:
            self._et_dev.setCurrentText(lnk.etalon_device)
        self._refresh_params(self._et_dev, self._et_par, lnk.etalon_param)
        self._et_dev.currentIndexChanged.connect(
            lambda: self._refresh_params(self._et_dev, self._et_par))
        fl.addRow("Эталон — устройство:", self._et_dev)
        fl.addRow("Эталон — параметр:", self._et_par)
        fl.addRow("Масштаб эталона ×:", self._et_scale)
        fl.addRow(_sep())

        self._ms_dev = QComboBox()
        self._ms_par = QComboBox()
        self._ms_scale = _dbl(lnk.measured_scale, -1e9, 1e9, 6)
        self._ms_dev.addItems([p.device_name for p in device_panels])
        if lnk.measured_device:
            self._ms_dev.setCurrentText(lnk.measured_device)
        self._refresh_params(self._ms_dev, self._ms_par, lnk.measured_param)
        self._ms_dev.currentIndexChanged.connect(
            lambda: self._refresh_params(self._ms_dev, self._ms_par))
        fl.addRow("Поверяемый — устройство:", self._ms_dev)
        fl.addRow("Поверяемый — параметр:", self._ms_par)
        fl.addRow("Масштаб поверяемого ×:", self._ms_scale)
        fl.addRow(_sep())

        fl.addRow(QLabel("Допуски (NG если хотя бы один нарушен):"))
        self._tol_tbl = ToleranceTableWidget(lnk.tolerances)
        fl.addRow(self._tol_tbl)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        fl.addRow(btns)

        _apply_dialog_style(self)

    def _refresh_params(self, dev_cb: QComboBox, par_cb: QComboBox,
                        select: str = None):
        dev_name = dev_cb.currentText()
        par_cb.clear()
        for p in self._panels:
            if p.device_name == dev_name:
                par_cb.addItems(list(p.parameters.keys()))
                break
        if select:
            par_cb.setCurrentText(select)

    def _on_accept(self):
        if not self._name.text().strip():
            QMessageBox.warning(self, "Ошибка", "Укажите имя параметра")
            return
        self.accept()

    def get_link(self) -> ParameterLink:
        return ParameterLink(
            name=self._name.text().strip(),
            etalon_device=self._et_dev.currentText(),
            etalon_param=self._et_par.currentText(),
            etalon_scale=self._et_scale.value(),
            measured_device=self._ms_dev.currentText(),
            measured_param=self._ms_par.currentText(),
            measured_scale=self._ms_scale.value(),
            tolerances=self._tol_tbl.get_tolerances(),
        )


# ─────────────────────────────────────────────────────────────────────────────
# График погрешностей (без изменений из v2.0.0)
# ─────────────────────────────────────────────────────────────────────────────

class ErrorGraph(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        self._links:  List[ParameterLink] = []
        self._points: List[TestPoint]     = []
        self._param_color: Dict[str, str] = {}
        self._cb_items: list = []

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(6)

        left = QWidget()
        left.setFixedWidth(210)
        left.setStyleSheet(
            f"background:{CLR_PANEL}; border:1px solid {CLR_BORDER};"
            "border-radius:4px;")
        lv = QVBoxLayout(left)
        lv.setContentsMargins(8, 8, 8, 8)
        lv.setSpacing(4)

        title = QLabel("Параметры на графике:")
        title.setStyleSheet("font-weight:bold; font-size:11px; border:none; background:transparent;")
        lv.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("background:transparent; border:none;")
        self._cb_widget = QWidget()
        self._cb_widget.setStyleSheet("background:transparent;")
        self._cb_layout = QVBoxLayout(self._cb_widget)
        self._cb_layout.setContentsMargins(0, 0, 0, 0)
        self._cb_layout.setSpacing(3)
        self._cb_layout.addStretch()
        scroll.setWidget(self._cb_widget)
        lv.addWidget(scroll, stretch=1)

        btn_row = QHBoxLayout()
        all_btn  = QPushButton("Все")
        none_btn = QPushButton("Нет")
        all_btn.setFixedHeight(22)
        none_btn.setFixedHeight(22)
        s = (f"background:{CLR_BORDER}; color:#e6edf3; border:none;"
             "border-radius:3px; font-size:11px;")
        all_btn.setStyleSheet(s)
        none_btn.setStyleSheet(s)
        all_btn.clicked.connect(lambda: self._toggle_all(True))
        none_btn.clicked.connect(lambda: self._toggle_all(False))
        btn_row.addWidget(all_btn)
        btn_row.addWidget(none_btn)
        lv.addLayout(btn_row)

        layout.addWidget(left)

        self._pw = pg.PlotWidget()
        self._pw.setBackground(CLR_BG)
        self._pw.showGrid(x=True, y=True, alpha=0.15)
        self._pw.setLabel("bottom", "Контрольная точка",
                          color="#8b949e", size="9pt")
        self._pw.setLabel("left", "Погрешность",
                          color="#8b949e", size="9pt")
        self._pw.addLegend(offset=(10, 5),
                           labelTextColor="#e6edf3",
                           pen=pg.mkPen(CLR_BORDER),
                           brush=pg.mkBrush(22, 27, 34, 200))
        layout.addWidget(self._pw, stretch=1)

    def setup(self, links: List[ParameterLink], points: List[TestPoint]):
        self._links  = links
        self._points = points
        self._assign_colors()
        self._rebuild_checkboxes()
        self._redraw()

    def update_point(self, idx: int):
        self._redraw()

    def clear(self):
        self._pw.clear()

    def _assign_colors(self):
        self._param_color.clear()
        unique = []
        for lnk in self._links:
            if lnk.name not in unique:
                unique.append(lnk.name)
        for i, name in enumerate(unique):
            self._param_color[name] = _PARAM_PALETTE[i % len(_PARAM_PALETTE)]

    def _rebuild_checkboxes(self):
        while self._cb_layout.count() > 1:
            item = self._cb_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._cb_items.clear()

        for lnk in self._links:
            clr = self._param_color.get(lnk.name, "#e6edf3")
            for tspec in lnk.tolerances:
                err_short = ERR_SHORT.get(tspec.error_type, tspec.error_type)
                label = f"{lnk.name}  {err_short}"
                cb = QCheckBox(label)
                cb.setChecked(True)
                cb.setStyleSheet(f"""
                    QCheckBox {{ color:{clr}; font-size:12px; background:transparent; border:none; }}
                    QCheckBox::indicator {{
                        width:14px; height:14px;
                        border:2px solid {clr}; border-radius:3px; background:transparent;
                    }}
                    QCheckBox::indicator:checked {{ background:{clr}; }}
                """)
                cb.stateChanged.connect(self._redraw)
                self._cb_layout.insertWidget(self._cb_layout.count() - 1, cb)
                self._cb_items.append((cb, lnk.name, tspec.error_type, tspec.tolerance))

    def _toggle_all(self, state: bool):
        for cb, *_ in self._cb_items:
            cb.blockSignals(True)
            cb.setChecked(state)
            cb.blockSignals(False)
        self._redraw()

    def _redraw(self):
        self._pw.clear()
        if not self._points:
            return

        x_labels = [(i, pt.label or str(i+1)) for i, pt in enumerate(self._points)]
        self._pw.getAxis("bottom").setTicks([x_labels])

        for cb, param_name, err_type, tol_val in self._cb_items:
            if not cb.isChecked():
                continue
            clr   = self._param_color.get(param_name, CLR_ACCENT)
            style = _ERR_LINE_STYLE.get(err_type, Qt.SolidLine)
            short = ERR_SHORT.get(err_type, err_type)

            pen_tol = pg.mkPen(clr, width=1, style=Qt.DashLine)
            for sign in (+1, -1):
                self._pw.addItem(pg.InfiniteLine(pos=sign * tol_val, angle=0, pen=pen_tol))

            xs_all, ys_all = [], []
            xs_ng, ys_ng   = [], []
            for i, pt in enumerate(self._points):
                if not pt.done:
                    continue
                val = pt.errors.get(param_name, {}).get(err_type)
                if val is None:
                    continue
                xs_all.append(i)
                ys_all.append(val)
                if not pt.passed.get(param_name, True):
                    xs_ng.append(i)
                    ys_ng.append(val)

            if not xs_all:
                continue

            self._pw.plot(
                xs_all, ys_all,
                pen=pg.mkPen(clr, width=2, style=style),
                symbol="o", symbolSize=8,
                symbolBrush=clr, symbolPen=None,
                name=f"{param_name} {short}",
            )
            if xs_ng:
                self._pw.plot(
                    xs_ng, ys_ng,
                    pen=None,
                    symbol="t", symbolSize=12,
                    symbolBrush=CLR_NG,
                    symbolPen=pg.mkPen(CLR_NG, width=1.5),
                    name=None,
                )


# ─────────────────────────────────────────────────────────────────────────────
# Диалог редактора методики (обновлён для поддержки нескольких генераторов)
# ─────────────────────────────────────────────────────────────────────────────

class MethodologyDialog(QDialog):

    def __init__(self, device_panels: list,
                 methodology: Optional[dict] = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Редактор методики")
        self.setMinimumSize(1000, 700)
        self.setModal(True)
        self._panels = device_panels
        self._links: List[ParameterLink] = []

        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.setContentsMargins(12, 12, 12, 12)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Название:"))
        self._meth_name = QLineEdit()
        self._meth_name.setPlaceholderText("Поверка ЭНП-2 — трёхфазная")
        name_row.addWidget(self._meth_name, stretch=1)
        root.addLayout(name_row)

        self._tabs = QTabWidget()
        # Порядок важен: сначала генератор (создаёт self._gen_type),
        # потом точки (читают текущий тип через self._gen_type)
        self._tabs.addTab(self._build_generator_tab(), "⚙ Генератор")
        self._tabs.addTab(self._build_params_tab(),    "📊 Параметры")
        self._tabs.addTab(self._build_points_tab(),    "📍 Точки")
        root.addWidget(self._tabs, stretch=1)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

        _apply_dialog_style(self)

        if methodology:
            self._load(methodology)

    # =========================================================================
    # Вкладка «Генератор» — динамическая
    # =========================================================================

    def _build_generator_tab(self) -> QWidget:
        w = QWidget()
        fl = QFormLayout(w)
        fl.setSpacing(12)
        fl.setContentsMargins(20, 20, 20, 20)

        # ── Тип генератора ────────────────────────────────────────────────────
        self._gen_type = QComboBox()
        for key, cfg in GENERATOR_CONFIGS.items():
            self._gen_type.addItem(cfg.get('display', key), key)
        self._gen_type.currentIndexChanged.connect(self._on_gen_type_changed)
        fl.addRow("Тип генератора:", self._gen_type)

        # ── Общие поля ────────────────────────────────────────────────────────
        self._gen_port = QLineEdit("COM1")
        fl.addRow("COM-порт:", self._gen_port)

        self._gen_baud = QComboBox()
        self._gen_baud.addItems(["9600", "19200", "38400", "57600", "115200"])
        self._gen_baud.setCurrentText("19200")
        fl.addRow("Скорость (бод):", self._gen_baud)

        # ── Специфичные поля (QStackedWidget, страница per тип) ──────────────
        self._gen_stack = QStackedWidget()

        # Страница 0: PTS (нет доп. полей)
        pts_page = QWidget()
        pts_fl = QFormLayout(pts_page)
        pts_fl.setContentsMargins(0, 4, 0, 0)
        pts_fl.addRow(QLabel("Трёхфазный AC калибратор — дополнительные настройки не требуются."))
        self._gen_stack.addWidget(pts_page)   # idx = 0

        # Страница 1: Mantigora
        man_page = QWidget()
        man_fl = QFormLayout(man_page)
        man_fl.setContentsMargins(0, 4, 0, 0)
        self._gen_kv = QComboBox()
        self._gen_kv.addItems(["2 кВ", "6 кВ", "10 кВ", "20 кВ", "30 кВ"])
        man_fl.addRow("Макс. напряжение:", self._gen_kv)
        self._gen_pw = QComboBox()
        self._gen_pw.addItems(["6 Вт", "15 Вт", "60 Вт"])
        man_fl.addRow("Мощность:", self._gen_pw)
        self._gen_series = QComboBox()
        self._gen_series.addItems(["HT", "HP"])
        man_fl.addRow("Серия:", self._gen_series)
        self._gen_stack.addWidget(man_page)   # idx = 1

        # При добавлении нового генератора: создай новую страницу и addWidget()
        # Индекс страницы должен совпадать с позицией в GENERATOR_TYPES.

        fl.addRow(self._gen_stack)
        fl.addRow(_sep())

        # ── Параметры прогона ─────────────────────────────────────────────────
        fl.addRow(QLabel("Параметры прогона"))

        self._settling = _dbl(5.0, 0.0, 300.0, 1)
        self._settling.setSuffix(" с")
        fl.addRow("Задержка установления:", self._settling)

        self._n_samples = QSpinBox()
        self._n_samples.setRange(1, 10000)
        self._n_samples.setValue(10)
        fl.addRow("Количество отсчётов:", self._n_samples)

        self._sample_interval = QSpinBox()
        self._sample_interval.setRange(100, 30000)
        self._sample_interval.setValue(500)
        self._sample_interval.setSuffix(" мс")
        fl.addRow("Интервал между отсчётами:", self._sample_interval)

        return w

    def _on_gen_type_changed(self, idx: int):
        """Сменить страницу специфичных полей и обновить таблицу точек."""
        self._gen_stack.setCurrentIndex(idx)

        # Обновить скорость по умолчанию
        key = self._gen_type.currentData()
        cfg = GENERATOR_CONFIGS.get(key, {})
        self._gen_baud.setCurrentText(cfg.get('default_baudrate', '19200'))

        # Пересобрать колонки таблицы точек (метки/задержки сохраняются)
        sp_cols = cfg.get('setpoint_cols', [])
        self._rebuild_points_columns(sp_cols)

    # =========================================================================
    # Вкладка «Параметры» (без изменений из v2.0.0)
    # =========================================================================

    def _build_params_tab(self) -> QWidget:
        w = QWidget()
        vbox = QVBoxLayout(w)
        vbox.setContentsMargins(8, 8, 8, 8)
        vbox.setSpacing(6)

        bar = QHBoxLayout()
        add_btn  = QPushButton("➕ Добавить")
        edit_btn = QPushButton("✏ Изменить")
        del_btn  = QPushButton("❌ Удалить")
        add_btn.clicked.connect(self._add_link)
        edit_btn.clicked.connect(self._edit_link)
        del_btn.clicked.connect(self._delete_link)
        for b in (add_btn, edit_btn, del_btn):
            bar.addWidget(b)
        bar.addStretch()
        hint = QLabel("Двойной клик — редактировать")
        hint.setStyleSheet("color:#8b949e;font-size:11px;")
        bar.addWidget(hint)
        vbox.addLayout(bar)

        self._links_tbl = QTableWidget(0, 6)
        self._links_tbl.setHorizontalHeaderLabels([
            "Имя", "Эталон (уст. / пар.)", "Поверяемый (уст. / пар.)",
            "Допуск 1", "Допуск 2", "Допуск 3",
        ])
        hh = self._links_tbl.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        for c in (3, 4, 5):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self._links_tbl.setSelectionBehavior(QTableWidget.SelectRows)
        self._links_tbl.setEditTriggers(QTableWidget.NoEditTriggers)
        self._links_tbl.doubleClicked.connect(self._edit_link)
        vbox.addWidget(self._links_tbl)

        return w

    # =========================================================================
    # Вкладка «Точки» — с динамическими колонками
    # =========================================================================

    def _build_points_tab(self) -> QWidget:
        w = QWidget()
        vbox = QVBoxLayout(w)
        vbox.setContentsMargins(8, 8, 8, 8)
        vbox.setSpacing(6)

        bar = QHBoxLayout()
        add_btn = QPushButton("➕ Строка")
        del_btn = QPushButton("❌ Удалить")
        dup_btn = QPushButton("📋 Дублировать")
        add_btn.clicked.connect(self._add_point_row)
        del_btn.clicked.connect(self._del_point_row)
        dup_btn.clicked.connect(self._dup_point_row)
        for b in (add_btn, del_btn, dup_btn):
            bar.addWidget(b)
        bar.addStretch()
        info = QLabel("Пустая ячейка = канал не меняется")
        info.setStyleSheet("color:#8b949e;font-size:11px;")
        bar.addWidget(info)
        vbox.addLayout(bar)

        # Таблица создаётся пустой, колонки выставит _rebuild_points_columns
        self._pts_tbl = QTableWidget(0, PT_CH_START)
        self._pts_tbl.setHorizontalHeaderLabels(["Метка", "Задержка (с)"])
        hh = self._pts_tbl.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(PT_LABEL_COL, QHeaderView.Stretch)
        vbox.addWidget(self._pts_tbl)

        # Инициализировать колонки под текущий тип генератора
        key = self._gen_type.currentData()
        cfg = GENERATOR_CONFIGS.get(key, {})
        self._rebuild_points_columns(cfg.get('setpoint_cols', []))

        return w

    @property
    def _active_sp_cols(self) -> list:
        """Текущие колонки уставок согласно выбранному типу генератора."""
        key = self._gen_type.currentData()
        cfg = GENERATOR_CONFIGS.get(key, {})
        return cfg.get('setpoint_cols', [])

    def _rebuild_points_columns(self, sp_cols: list):
        """
        Перестроить заголовки таблицы точек.
        Данные в колонках «Метка» и «Задержка» сохраняются,
        колонки уставок сбрасываются (они несовместимы между типами).
        """
        # Сохранить метку и задержку существующих строк
        saved = []
        for r in range(self._pts_tbl.rowCount()):
            label    = self._cell(self._pts_tbl, r, PT_LABEL_COL)
            settling = self._cell(self._pts_tbl, r, PT_SETTLING_COL)
            saved.append((label, settling))

        total_cols = PT_CH_START + len(sp_cols)
        self._pts_tbl.setColumnCount(total_cols)

        headers = ["Метка", "Задержка (с)"] + [c[1] for c in sp_cols]
        self._pts_tbl.setHorizontalHeaderLabels(headers)

        hh = self._pts_tbl.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(PT_LABEL_COL, QHeaderView.Stretch)

        # Восстановить метки и задержки, обнулить уставки
        self._pts_tbl.setRowCount(len(saved))
        for r, (label, settling) in enumerate(saved):
            self._pts_tbl.setItem(r, PT_LABEL_COL,    QTableWidgetItem(label))
            self._pts_tbl.setItem(r, PT_SETTLING_COL, QTableWidgetItem(settling))
            for c in range(PT_CH_START, total_cols):
                self._pts_tbl.setItem(r, c, QTableWidgetItem(""))

    # ── Параметры: операции ───────────────────────────────────────────────────

    def _add_link(self):
        dlg = ParameterLinkDialog(self._panels, parent=self)
        if dlg.exec() == QDialog.Accepted:
            self._links.append(dlg.get_link())
            self._refresh_links_tbl()

    def _edit_link(self):
        row = self._links_tbl.currentRow()
        if 0 <= row < len(self._links):
            dlg = ParameterLinkDialog(self._panels, self._links[row], self)
            if dlg.exec() == QDialog.Accepted:
                self._links[row] = dlg.get_link()
                self._refresh_links_tbl()

    def _delete_link(self):
        row = self._links_tbl.currentRow()
        if 0 <= row < len(self._links):
            self._links.pop(row)
            self._refresh_links_tbl()

    def _refresh_links_tbl(self):
        t = self._links_tbl
        t.setRowCount(len(self._links))
        for r, lnk in enumerate(self._links):
            t.setItem(r, 0, QTableWidgetItem(lnk.name))
            t.setItem(r, 1, QTableWidgetItem(
                f"{lnk.etalon_device} / {lnk.etalon_param}"))
            t.setItem(r, 2, QTableWidgetItem(
                f"{lnk.measured_device} / {lnk.measured_param}"))
            for ci, tspec in enumerate(lnk.tolerances[:3]):
                t.setItem(r, 3 + ci, QTableWidgetItem(tspec.label))
            for ci in range(len(lnk.tolerances), 3):
                t.setItem(r, 3 + ci, QTableWidgetItem(""))

    # ── Точки: операции ───────────────────────────────────────────────────────

    def _add_point_row(self):
        r = self._pts_tbl.rowCount()
        self._pts_tbl.insertRow(r)
        for c in range(self._pts_tbl.columnCount()):
            self._pts_tbl.setItem(r, c, QTableWidgetItem(""))

    def _del_point_row(self):
        row = self._pts_tbl.currentRow()
        if row >= 0:
            self._pts_tbl.removeRow(row)

    def _dup_point_row(self):
        row = self._pts_tbl.currentRow()
        if row < 0:
            return
        nr = row + 1
        self._pts_tbl.insertRow(nr)
        for c in range(self._pts_tbl.columnCount()):
            src = self._pts_tbl.item(row, c)
            self._pts_tbl.setItem(nr, c, QTableWidgetItem(src.text() if src else ""))

    def _cell(self, table, r, c) -> str:
        item = table.item(r, c)
        return item.text().strip() if item else ""

    def _points_from_table(self) -> List[TestPoint]:
        sp_cols = self._active_sp_cols
        points = []
        for r in range(self._pts_tbl.rowCount()):
            label      = self._cell(self._pts_tbl, r, PT_LABEL_COL)
            settling_s = self._cell(self._pts_tbl, r, PT_SETTLING_COL)
            settling   = float(settling_s) if settling_s else None
            setpoints: Dict[str, float] = {}
            for c_idx, (key, _) in enumerate(sp_cols):
                val_s = self._cell(self._pts_tbl, r, PT_CH_START + c_idx)
                if val_s:
                    try:
                        setpoints[key] = float(val_s.replace(",", "."))
                    except ValueError:
                        pass
            if label or setpoints:
                points.append(TestPoint(
                    label=label or str(r + 1),
                    setpoints=setpoints,
                    settling_time=settling,
                ))
        return points

    def _points_to_table(self, points: List[TestPoint]):
        sp_cols = self._active_sp_cols
        self._rebuild_points_columns(sp_cols)  # настраивает колонки
        self._pts_tbl.setRowCount(0)
        for pt in points:
            r = self._pts_tbl.rowCount()
            self._pts_tbl.insertRow(r)
            self._pts_tbl.setItem(r, PT_LABEL_COL, QTableWidgetItem(pt.label))
            self._pts_tbl.setItem(r, PT_SETTLING_COL,
                QTableWidgetItem("" if pt.settling_time is None
                                 else str(pt.settling_time)))
            for c_idx, (key, _) in enumerate(sp_cols):
                val = pt.setpoints.get(key)
                self._pts_tbl.setItem(r, PT_CH_START + c_idx,
                    QTableWidgetItem("" if val is None else str(val)))

    # ── Load / Save ───────────────────────────────────────────────────────────

    def _load(self, m: dict):
        self._meth_name.setText(m.get("name", ""))
        gen = m.get("generator", {})

        # Выбрать тип генератора (по ключу, хранится в 'type')
        gen_type = gen.get("type", "")
        for i in range(self._gen_type.count()):
            if self._gen_type.itemData(i) == gen_type:
                self._gen_type.setCurrentIndex(i)
                self._gen_stack.setCurrentIndex(i)
                break

        self._gen_port.setText(gen.get("port", "COM1"))
        self._gen_baud.setCurrentText(str(gen.get("baudrate", 19200)))

        # Специфичные поля Mantigora
        if gen_type == "Mantigora":
            kv = gen.get("voltage_kv", 2)
            self._gen_kv.setCurrentText(f"{kv} кВ")
            pw = gen.get("power_w", 6)
            self._gen_pw.setCurrentText(f"{pw} Вт")
            self._gen_series.setCurrentText(gen.get("series", "HT"))

        # Специфичные поля новых генераторов добавлять здесь

        self._settling.setValue(m.get("settling_time", 5.0))
        self._n_samples.setValue(m.get("n_samples", 10))
        self._sample_interval.setValue(m.get("sample_interval_ms", 500))

        self._links = [ParameterLink.from_dict(d)
                       for d in m.get("parameter_links", [])]
        self._refresh_links_tbl()
        self._points_to_table([TestPoint.from_dict(d)
                                for d in m.get("test_points", [])])

    def _on_accept(self):
        if not self._links:
            QMessageBox.warning(self, "Ошибка", "Добавьте хотя бы один параметр")
            return
        if not self._points_from_table():
            QMessageBox.warning(self, "Ошибка", "Добавьте хотя бы одну точку")
            return
        self.accept()

    def get_methodology(self) -> dict:
        gen_type = self._gen_type.currentData()

        gen_config: dict = {
            "type":     gen_type,
            "port":     self._gen_port.text().strip(),
            "baudrate": int(self._gen_baud.currentText()),
        }

        # Добавить специфичные параметры каждого типа
        if gen_type == "Mantigora":
            kv_str = self._gen_kv.currentText()     # "2 кВ"
            pw_str = self._gen_pw.currentText()     # "6 Вт"
            gen_config["voltage_kv"] = int(kv_str.split()[0])
            gen_config["power_w"]    = int(pw_str.split()[0])
            gen_config["series"]     = self._gen_series.currentText()

        # Новые генераторы: добавить ветку elif здесь

        return {
            "name":    self._meth_name.text().strip(),
            "version": "2.1",
            "created": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "generator":          gen_config,
            "settling_time":      self._settling.value(),
            "n_samples":          self._n_samples.value(),
            "sample_interval_ms": self._sample_interval.value(),
            "parameter_links":    [lnk.to_dict() for lnk in self._links],
            "test_points":        [pt.to_dict()  for pt  in self._points_from_table()],
        }


# ─────────────────────────────────────────────────────────────────────────────
# Основная вкладка «Авто-испытание» (без изменений из v2.0.0)
# ─────────────────────────────────────────────────────────────────────────────

class AutoTestTab(QWidget):

    def __init__(self, device_panels: list, parent=None):
        super().__init__(parent)
        self.device_panels = device_panels
        self._methodology: dict = {}
        self._links:  List[ParameterLink] = []
        self._points: List[TestPoint]     = []
        self._generator = None
        self._thread: Optional[AutoRunThread] = None

        self._build_ui()
        self._apply_styles()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        root.addLayout(self._build_toolbar())

        self._status_lbl = QLabel("Методика не загружена")
        self._status_lbl.setStyleSheet("color:#8b949e;font-size:11px;")
        root.addWidget(self._status_lbl)

        self._progress = QProgressBar()
        self._progress.setVisible(False)
        self._progress.setTextVisible(True)
        root.addWidget(self._progress)

        v_split = QSplitter(Qt.Vertical)

        self._results_tbl = QTableWidget()
        self._results_tbl.setEditTriggers(QTableWidget.NoEditTriggers)
        self._results_tbl.setSelectionBehavior(QTableWidget.SelectRows)
        v_split.addWidget(self._results_tbl)

        bottom = QWidget()
        h_lay  = QHBoxLayout(bottom)
        h_lay.setContentsMargins(0, 0, 0, 0)
        h_lay.setSpacing(6)

        self._graph = ErrorGraph()
        h_lay.addWidget(self._graph, stretch=3)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setFont(QFont("Courier New", 9))
        self._log.setMaximumWidth(360)
        h_lay.addWidget(self._log, stretch=1)

        v_split.addWidget(bottom)
        v_split.setSizes([300, 320])

        root.addWidget(v_split, stretch=1)

    def _build_toolbar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(6)

        self._btn_cfg   = QPushButton("⚙ Настроить методику")
        self._btn_start = QPushButton("▶ Запустить")
        self._btn_stop  = QPushButton("■ Стоп")
        self._btn_clear = QPushButton("🗑 Очистить")
        self._btn_csv   = QPushButton("💾 CSV")
        self._btn_save  = QPushButton("📋 Сохранить методику")
        self._btn_load  = QPushButton("📂 Загрузить методику")

        self._btn_start.setEnabled(False)
        self._btn_stop.setEnabled(False)

        self._btn_cfg.clicked.connect(self._open_editor)
        self._btn_start.clicked.connect(self._start_run)
        self._btn_stop.clicked.connect(self._stop_run)
        self._btn_clear.clicked.connect(self._clear_results)
        self._btn_csv.clicked.connect(self._export_csv)
        self._btn_save.clicked.connect(self._save_methodology)
        self._btn_load.clicked.connect(self._load_methodology)

        for w in (self._btn_cfg, _vline(),
                  self._btn_start, self._btn_stop, _vline(),
                  self._btn_clear, self._btn_csv, _vline(),
                  self._btn_save, self._btn_load):
            bar.addWidget(w)
        bar.addStretch()
        return bar

    def _open_editor(self):
        dlg = MethodologyDialog(
            self.device_panels,
            self._methodology if self._methodology else None,
            parent=self,
        )
        if dlg.exec() == QDialog.Accepted:
            self._apply_methodology(dlg.get_methodology())

    def _apply_methodology(self, m: dict):
        self._methodology = m
        self._links  = [ParameterLink.from_dict(d)
                        for d in m.get("parameter_links", [])]
        self._points = [TestPoint.from_dict(d)
                        for d in m.get("test_points", [])]

        try:
            self._generator = create_generator(m.get("generator", {}))
        except Exception as e:
            QMessageBox.critical(self, "Ошибка генератора", str(e))
            self._generator = None

        self._rebuild_results_table()
        self._graph.setup(self._links, self._points)

        name        = m.get("name", "Без названия")
        gen_type    = m.get("generator", {}).get("type", "?")
        tol_summary = []
        for lnk in self._links:
            tols = " | ".join(t.label for t in lnk.tolerances)
            tol_summary.append(f"{lnk.name}: {tols}")

        self._status_lbl.setText(
            f"Методика: {name}  |  Генератор: {gen_type}  |  "
            f"{len(self._links)} параметров  |  {len(self._points)} точек")

        self._btn_start.setEnabled(
            bool(self._links and self._points and self._generator))
        self._log.append(
            f"[{datetime.now():%H:%M:%S}] ✔ Методика: {name}  [{gen_type}]\n"
            + "\n".join(f"  {s}" for s in tol_summary))

    def _rebuild_results_table(self):
        t = self._results_tbl
        cols = ["№", "Точка"]
        for lnk in self._links:
            tols_str = " / ".join(ERR_SHORT.get(ts.error_type, ts.error_type)
                                  for ts in lnk.tolerances)
            cols.append(f"{lnk.name}\n{tols_str}")
        cols.append("Итог")

        t.setColumnCount(len(cols))
        t.setHorizontalHeaderLabels(cols)
        hh = t.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        t.verticalHeader().setDefaultSectionSize(38)

        t.setRowCount(len(self._points))
        for r, pt in enumerate(self._points):
            t.setItem(r, 0, _item(str(r + 1)))
            t.setItem(r, 1, _item(pt.label))
            for c in range(len(self._links) + 1):
                t.setItem(r, 2 + c, _item("⏳"))

    def _update_results_row(self, idx: int, point: TestPoint):
        t = self._results_tbl
        for c_idx, lnk in enumerate(self._links):
            errs   = point.errors.get(lnk.name, {})
            passed = point.passed.get(lnk.name, None)

            if not errs:
                text = "—"
                bg   = QColor(CLR_BORDER)
            else:
                parts = []
                for tspec in lnk.tolerances:
                    v = errs.get(tspec.error_type)
                    if v is not None:
                        parts.append(f"{v:+.3f}")
                text = "  /  ".join(parts)
                text += f"  {'✅' if passed else '❌'}"
                bg = QColor(CLR_OK if passed else CLR_NG)
                bg.setAlpha(55)

            it = _item(text)
            it.setBackground(QBrush(bg))
            t.setItem(idx, 2 + c_idx, it)

        ok   = point.overall_passed()
        text = ("✅ OK" if ok else "❌ NG") if ok is not None else "—"
        bg   = QColor(CLR_OK if ok else CLR_NG)
        bg.setAlpha(70)
        it   = _item(text)
        it.setBackground(QBrush(bg))
        t.setItem(idx, 2 + len(self._links), it)

    def _highlight_running(self, idx: int):
        bg = QColor(CLR_WARN)
        bg.setAlpha(35)
        for c in range(self._results_tbl.columnCount()):
            it = self._results_tbl.item(idx, c)
            if it:
                it.setBackground(QBrush(bg))

    def _start_run(self):
        if not self._generator:
            QMessageBox.warning(self, "Ошибка", "Генератор не настроен")
            return
        m = self._methodology
        self._thread = AutoRunThread(
            generator=self._generator,
            device_panels=self.device_panels,
            links=self._links,
            points=self._points,
            n_samples=m.get("n_samples", 10),
            sample_interval_ms=m.get("sample_interval_ms", 500),
            default_settling=m.get("settling_time", 5.0),
        )
        self._thread.point_started.connect(self._on_point_started)
        self._thread.sample_tick.connect(self._on_sample_tick)
        self._thread.point_done.connect(self._on_point_done)
        self._thread.run_finished.connect(self._on_run_finished)
        self._thread.log_msg.connect(self._log.append)

        self._progress.setMaximum(len(self._points))
        self._progress.setValue(0)
        self._progress.setFormat("Точка %v / %m")
        self._progress.setVisible(True)

        self._btn_start.setEnabled(False)
        self._btn_stop.setEnabled(True)
        self._btn_cfg.setEnabled(False)
        self._btn_load.setEnabled(False)
        self._thread.start()

    def _stop_run(self):
        if self._thread:
            self._thread.stop()
        self._btn_stop.setEnabled(False)

    def _on_point_started(self, idx: int):
        self._progress.setValue(idx)
        self._highlight_running(idx)

    def _on_sample_tick(self, cur: int, total: int):
        self._progress.setFormat(
            f"Точка {self._progress.value()+1}/{len(self._points)}"
            f" — отсчёт {cur}/{total}")

    def _on_point_done(self, idx: int, point: object):
        self._update_results_row(idx, point)
        self._graph.update_point(idx)
        self._progress.setValue(idx + 1)

    def _on_run_finished(self, success: bool, msg: str):
        self._progress.setVisible(False)
        self._btn_start.setEnabled(True)
        self._btn_stop.setEnabled(False)
        self._btn_cfg.setEnabled(True)
        self._btn_load.setEnabled(True)
        self._log.append(f"\n{'✅' if success else '❌'} {msg}")
        QMessageBox.information(self, "Прогон", msg)

    def _clear_results(self):
        for pt in self._points:
            pt.clear_results()
        self._rebuild_results_table()
        self._graph.setup(self._links, self._points)

    def _export_csv(self):
        if not any(pt.done for pt in self._points):
            QMessageBox.information(self, "Экспорт", "Нет данных для экспорта")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Экспорт результатов",
            f"results_{datetime.now():%Y%m%d_%H%M%S}.csv", "CSV (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f, delimiter=";")
                header = ["№", "Точка"]
                for lnk in self._links:
                    header.append(f"{lnk.name}_эталон")
                    header.append(f"{lnk.name}_поверяемый")
                    for tspec in lnk.tolerances:
                        header.append(f"{lnk.name}_{ERR_SHORT.get(tspec.error_type,'?')}")
                    header.append(f"{lnk.name}_OK")
                header.append("Итог")
                w.writerow(header)

                for i, pt in enumerate(self._points):
                    if not pt.done:
                        continue
                    row: list = [i + 1, pt.label]
                    for lnk in self._links:
                        row.append(pt.etalon_vals.get(lnk.name, ""))
                        row.append(pt.measured_vals.get(lnk.name, ""))
                        errs = pt.errors.get(lnk.name, {})
                        for tspec in lnk.tolerances:
                            row.append(errs.get(tspec.error_type, ""))
                        row.append("OK" if pt.passed.get(lnk.name) else "NG")
                    row.append("OK" if pt.overall_passed() else "NG")
                    w.writerow(row)

            self._log.append(f"[{datetime.now():%H:%M:%S}] Экспорт: {path}")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка экспорта", str(e))

    def _save_methodology(self):
        if not self._methodology:
            QMessageBox.warning(self, "Ошибка", "Методика не настроена")
            return
        os.makedirs("methodology", exist_ok=True)
        path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить методику", "methodology/", "JSON (*.json)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._methodology, f, indent=2, ensure_ascii=False)
            self._log.append(f"[{datetime.now():%H:%M:%S}] Методика сохранена: {path}")

    def _load_methodology(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Загрузить методику", "methodology/", "JSON (*.json)")
        if path:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self._apply_methodology(json.load(f))
            except Exception as e:
                QMessageBox.critical(self, "Ошибка загрузки", str(e))

    def _apply_styles(self):
        self.setStyleSheet(f"""
            QWidget {{ background:{CLR_BG}; color:#e6edf3; }}
            QTableWidget {{
                background:{CLR_PANEL}; color:#e6edf3;
                gridline-color:{CLR_BORDER}; border:none;
            }}
            QHeaderView::section {{
                background:#21262d; color:#e6edf3; padding:4px;
                border:none;
                border-right:1px solid {CLR_BORDER};
                border-bottom:1px solid {CLR_BORDER};
            }}
            QPushButton {{
                background:{CLR_BORDER}; border:none; border-radius:4px;
                padding:6px 12px; color:#e6edf3; font-weight:bold;
            }}
            QPushButton:hover    {{ background:{CLR_ACCENT}; color:{CLR_BG}; }}
            QPushButton:disabled {{ background:#21262d; color:#8b949e; }}
            QTextEdit {{
                background:{CLR_PANEL}; color:#e6edf3;
                border:1px solid {CLR_BORDER}; border-radius:4px;
            }}
            QProgressBar {{
                background:#21262d; border:none; border-radius:4px;
                color:#e6edf3; text-align:center; max-height:18px;
            }}
            QProgressBar::chunk {{ background:{CLR_ACCENT}; border-radius:4px; }}
            QComboBox {{
                background:{CLR_PANEL}; color:#e6edf3;
                border:1px solid {CLR_BORDER}; border-radius:4px; padding:4px;
            }}
            QComboBox::drop-down {{ width:22px; border-left:1px solid {CLR_BORDER}; }}
            QComboBox::down-arrow {{
                width:0; height:0;
                border-left:5px solid transparent;
                border-right:5px solid transparent;
                border-top:6px solid #e6edf3;
            }}
            QComboBox QAbstractItemView {{
                background:{CLR_PANEL}; color:#e6edf3;
                border:1px solid {CLR_BORDER};
                selection-background-color:{CLR_ACCENT};
                selection-color:{CLR_BG};
            }}
            QLabel {{ background:transparent; }}
        """)

    def showEvent(self, event):
        super().showEvent(event)


# ─────────────────────────────────────────────────────────────────────────────
# Вспомогательные функции (без изменений из v2.0.0)
# ─────────────────────────────────────────────────────────────────────────────

def _dbl(val: float, min_: float = 0.0, max_: float = 1e9,
         decs: int = 3) -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setRange(min_, max_)
    s.setValue(val)
    s.setDecimals(decs)
    return s


def _sep() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.HLine)
    f.setStyleSheet(f"color:{CLR_BORDER};")
    return f


def _vline() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.VLine)
    f.setStyleSheet(f"color:{CLR_BORDER};")
    return f


def _item(text: str) -> QTableWidgetItem:
    it = QTableWidgetItem(text)
    it.setTextAlignment(Qt.AlignCenter)
    return it


def _apply_dialog_style(dlg: QDialog):
    dlg.setStyleSheet(f"""
        QDialog, QWidget {{ background:{CLR_BG}; color:#e6edf3; }}
        QTabWidget::pane {{ border:none; background:{CLR_BG}; }}
        QTabBar::tab {{
            background:{CLR_PANEL}; color:#e6edf3;
            padding:6px 14px; margin-right:2px;
            border-top-left-radius:4px; border-top-right-radius:4px;
        }}
        QTabBar::tab:selected {{ color:{CLR_ACCENT}; border-bottom:2px solid {CLR_ACCENT}; }}
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
            background:{CLR_PANEL}; color:#e6edf3;
            border:1px solid {CLR_BORDER}; border-radius:4px; padding:4px;
            min-width:100px;
        }}
        QComboBox::drop-down {{
            subcontrol-origin: padding;
            subcontrol-position: top right;
            width: 22px;
            border-left: 1px solid {CLR_BORDER};
            border-radius: 0 4px 4px 0;
        }}
        QComboBox::down-arrow {{
            width: 0; height: 0;
            border-left:  5px solid transparent;
            border-right: 5px solid transparent;
            border-top:   6px solid #e6edf3;
        }}
        QComboBox QAbstractItemView {{
            background:{CLR_PANEL}; color:#e6edf3;
            border:1px solid {CLR_BORDER};
            selection-background-color:{CLR_ACCENT};
            selection-color:#0d1117;
            outline: none;
        }}
        QTableWidget {{
            background:{CLR_PANEL}; color:#e6edf3;
            gridline-color:{CLR_BORDER}; border:none;
        }}
        QHeaderView::section {{
            background:#21262d; color:#e6edf3; padding:4px;
            border:none; border-right:1px solid {CLR_BORDER};
        }}
        QPushButton {{
            background:{CLR_BORDER}; border:none; border-radius:4px;
            padding:5px 12px; color:#e6edf3; font-weight:bold;
        }}
        QPushButton:hover {{ background:{CLR_ACCENT}; color:{CLR_BG}; }}
        QLabel {{ color:#e6edf3; background:transparent; }}
        QFrame[frameShape="4"] {{ color:{CLR_BORDER}; }}
    """)
