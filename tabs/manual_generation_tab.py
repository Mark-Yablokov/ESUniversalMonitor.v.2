# tabs/manual_generation_tab.py
"""
Вкладка ручного снятия контрольных точек с расчётом погрешностей.

v4.0.0 (2026-04-24):
  - Встроенная панель ожидания вместо блокирующего QMessageBox.exec_():
    пользователь может переходить по вкладкам (выставлять генератор)
    и возвращаться, нажав «Готово» — диалог не блокирует главное окно.
  - Один настраиваемый график вместо двух фиксированных:
    выбор типа отображения через комбо-бокс (Δ / δ% / γ% / Сравнение).
  - API панелей: стандартный get_device_id() / get_measurement().
  - Сохранена полная совместимость с save/load методики JSON v1.0.
"""

import csv
import json
import os
from datetime import datetime
from typing import Dict, List, Optional

import numpy as np
import pyqtgraph as pg
from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QFrame, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMessageBox, QProgressBar,
    QPushButton, QSizePolicy, QSpinBox, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

pg.setConfigOptions(antialias=True, foreground="#e6edf3", background="#0d1117")

# ── Цветовая палитра ──────────────────────────────────────────────────────────
CLR_OK   = "#3fb950"
CLR_NG   = "#f85149"
CLR_ABS  = "#74c0fc"
CLR_REL  = "#ffd43b"
CLR_RED  = "#ff922b"
CLR_ET   = "#58a6ff"   # кривая эталона в режиме «Сравнение»
CLR_MS   = "#f78166"   # кривая поверяемого в режиме «Сравнение»
CLR_TOL  = "#3fb950"

# Режимы графика
CHART_ABS  = 0
CHART_REL  = 1
CHART_RED  = 2
CHART_CMP  = 3   # Сравнение: эталон + поверяемый


# ─────────────────────────────────────────────────────────────────────────────
class MeasurementPoint:
    """Одна контрольная точка (уставка генератора + результаты)."""

    def __init__(self, setpoint: float, label: str = ""):
        self.setpoint: float = setpoint
        self.label:    str   = label or str(setpoint)

        self.samples_etalon:   List[float] = []
        self.samples_measured: List[float] = []

        self.etalon_val:   Optional[float] = None
        self.measured_val: Optional[float] = None
        self.abs_error:    Optional[float] = None
        self.rel_error:    Optional[float] = None
        self.red_error:    Optional[float] = None
        self.passed:       Optional[bool]  = None


# ─────────────────────────────────────────────────────────────────────────────
class _InstructionBanner(QFrame):
    """
    Встроенная панель ожидания — НЕ блокирует главное окно.

    Показывается когда нужно выставить генератор на нужное значение.
    Пока панель видна, пользователь может свободно переходить
    на другие вкладки (генератор PTS, HP и т.д.), настраивать там сигнал,
    затем вернуться и нажать «Готово».
    """

    ready_clicked  = pyqtSignal()
    skip_clicked   = pyqtSignal()
    stop_clicked   = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.StyledPanel)
        self.setObjectName("InstructionBanner")
        self._build()
        self.hide()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(4)

        # Заголовок
        self._lbl_title = QLabel()
        self._lbl_title.setStyleSheet(
            "font-size:13px; font-weight:bold; color:#e6edf3;"
        )
        lay.addWidget(self._lbl_title)

        # Текущие показания (обновляются каждые ~600 мс)
        readings = QHBoxLayout()
        self._lbl_et = QLabel("Эталон: —")
        self._lbl_ms = QLabel("Поверяемый: —")
        for lbl in (self._lbl_et, self._lbl_ms):
            lbl.setStyleSheet("color:#8b949e; font-family:Consolas;")
            readings.addWidget(lbl)
        readings.addStretch()
        lay.addLayout(readings)

        # Кнопки
        btn_row = QHBoxLayout()
        self._btn_ready = QPushButton("✔  Готово — начать измерение")
        self._btn_skip  = QPushButton("⏭  Пропустить точку")
        self._btn_stop  = QPushButton("✖  Прервать испытание")
        self._btn_ready.clicked.connect(self.ready_clicked)
        self._btn_skip.clicked.connect(self.skip_clicked)
        self._btn_stop.clicked.connect(self.stop_clicked)
        btn_row.addWidget(self._btn_ready)
        btn_row.addWidget(self._btn_skip)
        btn_row.addStretch()
        btn_row.addWidget(self._btn_stop)
        lay.addLayout(btn_row)

    def show_point(self, idx: int, total: int, label: str, setpoint: float):
        self._lbl_title.setText(
            f"Точка {idx + 1} / {total}:  {label}  "
            f"→  выставьте значение  {setpoint}  на генераторе, "
            f"дождитесь стабилизации и нажмите «Готово»"
        )
        self._lbl_et.setText("Эталон: —")
        self._lbl_ms.setText("Поверяемый: —")
        self.show()

    def update_readings(self, et_val: Optional[float], ms_val: Optional[float]):
        et_s = f"{et_val:.6g}" if et_val is not None else "—"
        ms_s = f"{ms_val:.6g}" if ms_val is not None else "—"
        self._lbl_et.setText(f"Эталон: {et_s}")
        self._lbl_ms.setText(f"Поверяемый: {ms_s}")


# ─────────────────────────────────────────────────────────────────────────────
class ManualGenerationTab(QWidget):
    """
    Главная вкладка ручного испытания.

    Поток управления
    ────────────────
    Старт → показать _InstructionBanner (не блокирует)
          → пользователь переходит на вкладку генератора, выставляет сигнал
          → возвращается, нажимает «Готово»
          → таймер собирает N отсчётов
          → рассчитываются погрешности
          → диалог результата (modal QMessageBox — генератор уже выставлен)
          → следующая точка или завершение
    """

    def __init__(self, device_panels: List = None, parent=None):
        super().__init__(parent)
        self.device_panels: List = device_panels or []
        self.points: List[MeasurementPoint] = []

        self._cur_idx  = -1
        self._running  = False

        self._samp_et: List[float] = []
        self._samp_ms: List[float] = []

        # Таймер сбора отсчётов
        self._timer = QTimer()
        self._timer.timeout.connect(self._on_sample)

        # Таймер предварительного просмотра (в InstructionBanner)
        self._preview_timer = QTimer()
        self._preview_timer.setInterval(600)
        self._preview_timer.timeout.connect(self._update_banner_readings)

        # Линии допуска на графике
        self._tol_lines: list = []

        self._build_ui()
        self._apply_styles()
        self.refresh_available_params()

    # =========================================================================
    # Построение интерфейса
    # =========================================================================

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(6)

        # ── Встроенная панель ожидания (не блокирует) ─────────────────────────
        self._banner = _InstructionBanner()
        self._banner.ready_clicked.connect(self._on_ready)
        self._banner.skip_clicked.connect(self._on_skip)
        self._banner.stop_clicked.connect(self._stop_sequence)
        root.addWidget(self._banner)

        root.addWidget(self._build_config_box())
        root.addLayout(self._build_ctrl_bar())
        root.addWidget(self._build_table())
        root.addWidget(self._build_progress())
        root.addWidget(self._build_chart(), stretch=1)

    # ── Конфигурация ──────────────────────────────────────────────────────────
    def _build_config_box(self) -> QGroupBox:
        gb = QGroupBox("Конфигурация измерения")
        fl = QFormLayout(gb)
        fl.setSpacing(6)
        fl.setLabelAlignment(Qt.AlignRight)

        self._meth_name = QLineEdit()
        self._meth_name.setPlaceholderText("Например: Поверка ЭНП-2 напряжение")
        fl.addRow("Название методики:", self._meth_name)

        self._et_combo = QComboBox()
        self._et_scale = _make_scale_spin()
        fl.addRow("Эталон:", _param_row(self._et_combo, self._et_scale))

        self._ms_combo = QComboBox()
        self._ms_scale = _make_scale_spin()
        fl.addRow("Поверяемый:", _param_row(self._ms_combo, self._ms_scale))

        self._nominal = QDoubleSpinBox()
        self._nominal.setRange(1e-9, 1e9)
        self._nominal.setValue(100.0)
        self._nominal.setDecimals(6)
        self._nominal.setToolTip(
            "Знаменатель для приведённой погрешности: γ = Δ / nominal × 100 %"
        )
        fl.addRow("Нормирующее значение:", self._nominal)

        self._err_combo = QComboBox()
        self._err_combo.addItems([
            "Приведённая  γ (%)",
            "Относительная  δ (%)",
            "Абсолютная  Δ",
        ])
        self._tol_spin = QDoubleSpinBox()
        self._tol_spin.setRange(0.0, 1e9)
        self._tol_spin.setValue(0.1)
        self._tol_spin.setDecimals(6)
        tol_row = QHBoxLayout()
        tol_row.addWidget(self._err_combo, stretch=2)
        tol_row.addSpacing(12)
        tol_row.addWidget(QLabel("Допуск ±:"))
        tol_row.addWidget(self._tol_spin, stretch=1)
        fl.addRow("Тип / допуск:", tol_row)

        self._n_spin = QSpinBox()
        self._n_spin.setRange(1, 10000)
        self._n_spin.setValue(10)
        self._n_spin.setToolTip("Количество отсчётов в каждой точке")

        self._interval_spin = QSpinBox()
        self._interval_spin.setRange(100, 30000)
        self._interval_spin.setValue(500)
        self._interval_spin.setSuffix(" мс")

        samp_row = QHBoxLayout()
        samp_row.addWidget(QLabel("Отсчётов:"))
        samp_row.addWidget(self._n_spin)
        samp_row.addSpacing(20)
        samp_row.addWidget(QLabel("Интервал:"))
        samp_row.addWidget(self._interval_spin)
        samp_row.addStretch()
        fl.addRow("Сбор данных:", samp_row)

        return gb

    # ── Кнопки ────────────────────────────────────────────────────────────────
    def _build_ctrl_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(6)

        self._add_btn      = QPushButton("➕ Добавить точку")
        self._remove_btn   = QPushButton("❌ Удалить точку")
        self._start_btn    = QPushButton("▶ Старт")
        self._stop_btn     = QPushButton("■ Стоп")
        self._stop_btn.setEnabled(False)
        self._export_btn   = QPushButton("💾 Экспорт CSV")
        self._clear_btn    = QPushButton("🗑 Очистить результаты")
        self._save_btn     = QPushButton("📋 Сохранить методику")
        self._load_btn     = QPushButton("📂 Загрузить методику")

        self._add_btn.clicked.connect(self._add_point)
        self._remove_btn.clicked.connect(self._remove_point)
        self._start_btn.clicked.connect(self._start_sequence)
        self._stop_btn.clicked.connect(self._stop_sequence)
        self._export_btn.clicked.connect(self._export_csv)
        self._clear_btn.clicked.connect(self._clear_results)
        self._save_btn.clicked.connect(self._save_methodology)
        self._load_btn.clicked.connect(self._load_methodology)

        for w in (self._add_btn, self._remove_btn,
                  _vline(),
                  self._start_btn, self._stop_btn,
                  _vline(),
                  self._clear_btn,
                  _vline(),
                  self._export_btn,
                  _vline(),
                  self._save_btn, self._load_btn):
            bar.addWidget(w)
        bar.addStretch()
        return bar

    # ── Таблица точек ─────────────────────────────────────────────────────────
    def _build_table(self) -> QTableWidget:
        self._table = QTableWidget()
        self._table.setColumnCount(9)
        self._table.setHorizontalHeaderLabels([
            "Точка", "Эталон", "Поверяемый",
            "Δ (абс.)", "δ (%)", "γ (%)",
            "Допуск ±", "Тип", "Статус",
        ])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setMaximumHeight(200)
        return self._table

    # ── Прогресс-бар ──────────────────────────────────────────────────────────
    def _build_progress(self) -> QProgressBar:
        self._progress = QProgressBar()
        self._progress.setVisible(False)
        self._progress.setTextVisible(True)
        self._progress.setFormat("Сбор: %v / %m отсчётов")
        return self._progress

    # ── Один настраиваемый график ─────────────────────────────────────────────
    def _build_chart(self) -> QWidget:
        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(4)

        # Панель выбора режима
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("График:"))
        self._chart_combo = QComboBox()
        self._chart_combo.addItems([
            "Δ — абсолютная погрешность",
            "δ% — относительная погрешность",
            "γ% — приведённая погрешность",
            "Сравнение: эталон + поверяемый",
        ])
        self._chart_combo.setFixedWidth(280)
        self._chart_combo.currentIndexChanged.connect(self._redraw_chart)
        mode_row.addWidget(self._chart_combo)
        mode_row.addStretch()
        vbox.addLayout(mode_row)

        # Единственный PlotWidget
        self._plot = pg.PlotWidget()
        self._plot.setBackground("#0d1117")
        self._plot.showGrid(x=True, y=True, alpha=0.18)
        self._plot.setLabel("bottom", "Заданное значение", color="#8b949e", size="9pt")
        self._plot.setMinimumHeight(200)
        self._plot.addLegend(
            offset=(10, 5), labelTextColor="#e6edf3",
            pen=pg.mkPen("#30363d"), brush=pg.mkBrush(22, 27, 34, 200),
        )

        # Кривые (показываются/скрываются в зависимости от режима)
        self._curve_abs = self._plot.plot(
            [], [], pen=pg.mkPen(CLR_ABS, width=2),
            symbol="o", symbolSize=7, symbolBrush=CLR_ABS, name="Δ",
        )
        self._curve_rel = self._plot.plot(
            [], [], pen=pg.mkPen(CLR_REL, width=2),
            symbol="o", symbolSize=7, symbolBrush=CLR_REL, name="δ (%)",
        )
        self._curve_red = self._plot.plot(
            [], [], pen=pg.mkPen(CLR_RED, width=2),
            symbol="s", symbolSize=7, symbolBrush=CLR_RED, name="γ (%)",
        )
        self._curve_et = self._plot.plot(
            [], [], pen=pg.mkPen(CLR_ET, width=2),
            symbol="o", symbolSize=7, symbolBrush=CLR_ET, name="Эталон",
        )
        self._curve_ms = self._plot.plot(
            [], [], pen=pg.mkPen(CLR_MS, width=2),
            symbol="s", symbolSize=7, symbolBrush=CLR_MS, name="Поверяемый",
        )

        vbox.addWidget(self._plot, stretch=1)
        return container

    # =========================================================================
    # Работа с параметрами устройств
    # =========================================================================

    def set_device_panels(self, panels: List):
        self.device_panels = panels
        self.refresh_available_params()

    def refresh_available_params(self):
        """Обновить списки параметров в комбо-боксах из подключённых панелей."""
        params = self._available_params()
        cur_et = self._et_combo.currentText()
        cur_ms = self._ms_combo.currentText()

        self._et_combo.clear()
        self._et_combo.addItems(params)
        self._ms_combo.clear()
        self._ms_combo.addItems(params)

        if cur_et in params:
            self._et_combo.setCurrentText(cur_et)
        elif len(params) >= 1:
            self._et_combo.setCurrentIndex(0)

        if cur_ms in params:
            self._ms_combo.setCurrentText(cur_ms)
        elif len(params) >= 2:
            self._ms_combo.setCurrentIndex(1)
        elif params:
            self._ms_combo.setCurrentIndex(0)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_available_params()

    def _available_params(self) -> List[str]:
        """
        Список 'device_name:param_key' со всех панелей.

        Modbus / Rigol / PTS используют BaseDevicePanel:
          panel.parameters  — dict {param_name: {...}} заполняется при настройке
                              регистров/режима, ДО подключения.
          panel.device_name — строковое имя устройства.

        MantigoraPanel не имеет panel.parameters, поэтому для неё fallback
        на panel.get_measurement() (live-чтение с железа).
        """
        result = []
        for panel in self.device_panels:
            try:
                dev_name = getattr(panel, 'device_name', None) or panel.get_device_id()
                params   = getattr(panel, 'parameters', {})
                if params:
                    # Modbus, Rigol, PTS — параметры из конфигурации панели
                    for key in params:
                        result.append(f"{dev_name}:{key}")
                else:
                    # Mantigora и другие без panel.parameters
                    meas = panel.get_measurement()
                    if isinstance(meas, dict):
                        for key, val in meas.items():
                            if not isinstance(val, bool):
                                result.append(f"{dev_name}:{key}")
            except Exception:
                pass
        return result

    def _read_scaled(self, full_key: str, scale: float) -> Optional[float]:
        """
        Читает текущее значение параметра по ключу 'device_name:param' и
        умножает на масштаб.

        Путь 1 — panel.last_values (Modbus, Rigol, PTS):
          кэш последнего опроса потока, обновляется без блокировки GUI.
        Путь 2 — panel.get_measurement() (Mantigora):
          live-чтение с железа; используется как fallback.
        """
        if ":" not in full_key:
            return None
        dev_name, param = full_key.split(":", 1)
        for panel in self.device_panels:
            try:
                name = getattr(panel, 'device_name', None) or panel.get_device_id()
                if name != dev_name:
                    continue
                # Путь 1: last_values (polling-based панели)
                last = getattr(panel, 'last_values', {})
                if param in last and last[param] is not None:
                    return float(last[param]) * scale
                # Путь 2: get_measurement() (Mantigora и др.)
                meas = panel.get_measurement()
                if isinstance(meas, dict):
                    val = meas.get(param)
                    if val is not None and not isinstance(val, bool):
                        return float(val) * scale
            except Exception:
                pass
        return None

    # =========================================================================
    # Управление точками
    # =========================================================================

    def _add_point(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Новая точка")
        dlg.setMinimumWidth(320)
        vbox = QVBoxLayout(dlg)

        fl = QFormLayout()
        sp_val = QDoubleSpinBox()
        sp_val.setRange(-1e9, 1e9)
        sp_val.setDecimals(6)
        lbl_edit = QLineEdit()
        lbl_edit.setPlaceholderText("Оставьте пустым — будет число")
        fl.addRow("Заданное значение:", sp_val)
        fl.addRow("Метка:", lbl_edit)
        vbox.addLayout(fl)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        vbox.addWidget(bb)

        if dlg.exec_():
            label = lbl_edit.text().strip() or str(sp_val.value())
            self.points.append(MeasurementPoint(sp_val.value(), label))
            self._refresh_table()

    def _remove_point(self):
        row = self._table.currentRow()
        if row >= 0:
            del self.points[row]
            self._refresh_table()

    def _refresh_table(self):
        self._table.setRowCount(len(self.points))
        tol   = self._tol_spin.value()
        etype = self._etype_label()

        for i, pt in enumerate(self.points):

            def _item(text: str) -> QTableWidgetItem:
                it = QTableWidgetItem(text)
                it.setTextAlignment(Qt.AlignCenter)
                return it

            self._table.setItem(i, 0, _item(pt.label))

            if pt.etalon_val is not None:
                self._table.setItem(i, 1, _item(f"{pt.etalon_val:.6g}"))
                self._table.setItem(i, 2, _item(f"{pt.measured_val:.6g}"))
                self._table.setItem(i, 3, _item(f"{pt.abs_error:.6g}"))
                rel = (f"{pt.rel_error:.4f}"
                       if pt.rel_error is not None and not np.isnan(pt.rel_error)
                       else "—")
                red = (f"{pt.red_error:.4f}"
                       if pt.red_error is not None and not np.isnan(pt.red_error)
                       else "—")
                self._table.setItem(i, 4, _item(rel))
                self._table.setItem(i, 5, _item(red))
            else:
                for col in range(1, 6):
                    self._table.setItem(i, col, _item("—"))

            self._table.setItem(i, 6, _item(f"±{tol:.6g}"))
            self._table.setItem(i, 7, _item(etype))

            # Статус
            if pt.passed is None:
                st = _item("⏳ Ожидание")
                st.setForeground(QColor("#8b949e"))
            elif pt.passed:
                st = _item("✅  OK")
                st.setForeground(QColor(CLR_OK))
            else:
                st = _item("❌  NG")
                st.setForeground(QColor(CLR_NG))
            self._table.setItem(i, 8, st)

    # =========================================================================
    # Последовательность измерений
    # =========================================================================

    def _start_sequence(self):
        if not self.points:
            QMessageBox.warning(self, "Старт", "Добавьте хотя бы одну точку.")
            return
        et_key = self._et_combo.currentText()
        ms_key = self._ms_combo.currentText()
        if not et_key or not ms_key:
            QMessageBox.warning(self, "Старт", "Выберите параметры эталона и поверяемого.")
            return
        if et_key == ms_key:
            QMessageBox.warning(self, "Старт", "Эталон и поверяемый не должны совпадать.")
            return

        self._running  = True
        self._cur_idx  = 0
        self._set_controls_running(True)
        self._proceed_to_point()

    def _stop_sequence(self):
        self._timer.stop()
        self._preview_timer.stop()
        self._running = False
        self._cur_idx = -1
        self._progress.setVisible(False)
        self._banner.hide()
        self._set_controls_running(False)

    def _set_controls_running(self, running: bool):
        self._start_btn.setEnabled(not running)
        self._stop_btn.setEnabled(running)
        self._add_btn.setEnabled(not running)
        self._remove_btn.setEnabled(not running)
        self._clear_btn.setEnabled(not running)
        self._save_btn.setEnabled(not running)
        self._load_btn.setEnabled(not running)

    # ── Шаг 1: показать встроенную панель (НЕ блокирует окно) ─────────────────
    def _proceed_to_point(self):
        if not self._running:
            return
        if self._cur_idx >= len(self.points):
            self._finish_sequence(all_done=True)
            return

        pt = self.points[self._cur_idx]
        self._banner.show_point(
            self._cur_idx, len(self.points), pt.label, pt.setpoint
        )
        # Запустить предварительный просмотр значений в панели
        self._preview_timer.start()

    def _update_banner_readings(self):
        """Обновляет текущие показания в InstructionBanner каждые 600 мс."""
        et_val = self._read_scaled(
            self._et_combo.currentText(), self._et_scale.value()
        )
        ms_val = self._read_scaled(
            self._ms_combo.currentText(), self._ms_scale.value()
        )
        self._banner.update_readings(et_val, ms_val)

    # ── Шаг 2: пользователь нажал «Готово» → начать сбор ─────────────────────
    def _on_ready(self):
        self._preview_timer.stop()
        self._banner.hide()

        self._samp_et.clear()
        self._samp_ms.clear()
        n = self._n_spin.value()
        self._progress.setMaximum(n)
        self._progress.setValue(0)
        self._progress.setVisible(True)
        self._timer.start(self._interval_spin.value())

    # ── Шаг 2б: пропустить точку ──────────────────────────────────────────────
    def _on_skip(self):
        self._preview_timer.stop()
        self._banner.hide()
        self._cur_idx += 1
        self._proceed_to_point()

    # ── Шаг 3: таймер собирает отсчёты ────────────────────────────────────────
    def _on_sample(self):
        et_val = self._read_scaled(
            self._et_combo.currentText(), self._et_scale.value()
        )
        ms_val = self._read_scaled(
            self._ms_combo.currentText(), self._ms_scale.value()
        )
        if et_val is not None and ms_val is not None:
            self._samp_et.append(et_val)
            self._samp_ms.append(ms_val)
        self._progress.setValue(len(self._samp_et))

        if len(self._samp_et) >= self._n_spin.value():
            self._timer.stop()
            self._progress.setVisible(False)
            self._process_samples()

    # ── Шаг 4: расчёт погрешностей ────────────────────────────────────────────
    def _process_samples(self):
        pt      = self.points[self._cur_idx]
        nominal = self._nominal.value()
        tol     = self._tol_spin.value()
        etype   = self._err_combo.currentIndex()

        et_arr = np.array(self._samp_et)
        ms_arr = np.array(self._samp_ms)
        abs_arr = ms_arr - et_arr

        with np.errstate(invalid="ignore", divide="ignore"):
            rel_arr = np.where(et_arr != 0, abs_arr / np.abs(et_arr) * 100.0, np.nan)
            red_arr = np.where(
                nominal != 0, abs_arr / abs(nominal) * 100.0, np.nan
            )

        ctrl_arr  = [red_arr, rel_arr, abs_arr][etype]
        worst_idx = int(np.nanargmax(np.abs(ctrl_arr)))

        pt.samples_etalon   = list(et_arr)
        pt.samples_measured = list(ms_arr)
        pt.etalon_val       = float(et_arr[worst_idx])
        pt.measured_val     = float(ms_arr[worst_idx])
        pt.abs_error        = float(abs_arr[worst_idx])
        pt.rel_error        = float(rel_arr[worst_idx])
        pt.red_error        = float(red_arr[worst_idx])
        ctrl_val            = float(ctrl_arr[worst_idx])
        pt.passed           = bool(abs(ctrl_val) <= tol)

        self._refresh_table()
        self._redraw_chart()
        self._show_result_dialog(pt, ctrl_val)

    # ── Шаг 5: диалог результата (modal — генератор уже выставлен) ────────────
    def _show_result_dialog(self, pt: MeasurementPoint, ctrl_val: float):
        tol     = self._tol_spin.value()
        short   = self._etype_short()
        is_last = (self._cur_idx + 1 >= len(self.points))

        rel_txt = (f"{pt.rel_error:.4f}"
                   if pt.rel_error is not None and not np.isnan(pt.rel_error)
                   else "—")
        red_txt = (f"{pt.red_error:.4f}"
                   if pt.red_error is not None and not np.isnan(pt.red_error)
                   else "—")

        body = (
            f"<b>Точка:</b> {pt.label}<br><br>"
            f"<table cellspacing='6'>"
            f"<tr><td>Эталон:</td><td><b>{pt.etalon_val:.6g}</b></td></tr>"
            f"<tr><td>Поверяемый:</td><td><b>{pt.measured_val:.6g}</b></td></tr>"
            f"<tr><td>Δ (абс.):</td><td>{pt.abs_error:.6g}</td></tr>"
            f"<tr><td>δ (отн., %):</td><td>{rel_txt}</td></tr>"
            f"<tr><td>γ (привед., %):</td><td>{red_txt}</td></tr>"
            f"</table><br>"
            f"Контрольная <b>{short}</b> = <b>{ctrl_val:.4f}</b>"
            f"&nbsp;|&nbsp; Допуск ± {tol:.6g}<br><br>"
        )
        if pt.passed:
            body += f"<font color='{CLR_OK}'><b>✅  В ДОПУСКЕ</b></font>"
        else:
            body += f"<font color='{CLR_NG}'><b>❌  ПРЕВЫШЕНИЕ ДОПУСКА</b></font>"

        dlg = QMessageBox(self)
        dlg.setWindowTitle("Результат измерения")
        dlg.setText(body)

        if pt.passed:
            if is_last:
                next_btn = dlg.addButton("Завершить ✓", QMessageBox.AcceptRole)
            else:
                next_btn = dlg.addButton("Следующая точка →", QMessageBox.AcceptRole)
                dlg.addButton("Завершить", QMessageBox.RejectRole)
            dlg.exec_()
            if dlg.clickedButton() == next_btn and self._running:
                self._cur_idx += 1
                self._proceed_to_point()
            else:
                self._finish_sequence(all_done=is_last)
        else:
            retry_btn = dlg.addButton("🔄 Повторить точку",  QMessageBox.AcceptRole)
            cont_btn  = dlg.addButton("Продолжить →",        QMessageBox.ActionRole)
            dlg.addButton("Завершить",                        QMessageBox.RejectRole)
            dlg.exec_()
            clicked = dlg.clickedButton()
            if clicked == retry_btn and self._running:
                pt.etalon_val = None
                pt.passed     = None
                self._refresh_table()
                self._proceed_to_point()
            elif clicked == cont_btn and self._running:
                self._cur_idx += 1
                self._proceed_to_point()
            else:
                self._finish_sequence(all_done=False)

    def _finish_sequence(self, all_done: bool = False):
        self._timer.stop()
        self._preview_timer.stop()
        self._running = False
        self._cur_idx = -1
        self._progress.setVisible(False)
        self._banner.hide()
        self._set_controls_running(False)

        if all_done and self.points:
            ok    = sum(1 for pt in self.points if pt.passed)
            total = sum(1 for pt in self.points if pt.passed is not None)
            QMessageBox.information(
                self, "Измерения завершены",
                f"Обработано точек: {total}\n"
                f"В допуске:        {ok}\n"
                f"Вне допуска:      {total - ok}",
            )

    # =========================================================================
    # График
    # =========================================================================

    def _redraw_chart(self):
        """Перерисовать график по текущему выбору комбо-бокса."""
        mode = self._chart_combo.currentIndex()
        done = [pt for pt in self.points if pt.etalon_val is not None]

        # Скрыть все кривые
        for c in (self._curve_abs, self._curve_rel,
                  self._curve_red, self._curve_et, self._curve_ms):
            c.setData([], [])

        # Убрать старые линии допуска
        for item in self._tol_lines:
            self._plot.removeItem(item)
        self._tol_lines.clear()

        if not done:
            return

        xs = [pt.setpoint for pt in done]
        tol = self._tol_spin.value()

        if mode == CHART_ABS:
            self._plot.setLabel("left", "Δ (абс.)", color="#8b949e", size="9pt")
            self._curve_abs.setData(xs, [pt.abs_error for pt in done])

        elif mode == CHART_REL:
            self._plot.setLabel("left", "δ (%)", color="#8b949e", size="9pt")
            self._curve_rel.setData(xs, [pt.rel_error for pt in done])
            self._add_tol_lines(tol)

        elif mode == CHART_RED:
            self._plot.setLabel("left", "γ (%)", color="#8b949e", size="9pt")
            self._curve_red.setData(xs, [pt.red_error for pt in done])
            self._add_tol_lines(tol)

        elif mode == CHART_CMP:
            self._plot.setLabel("left", "Значение", color="#8b949e", size="9pt")
            self._curve_et.setData(xs, [pt.etalon_val   for pt in done])
            self._curve_ms.setData(xs, [pt.measured_val for pt in done])

    def _add_tol_lines(self, tol: float):
        for sign in (1, -1):
            line = pg.InfiniteLine(
                sign * tol, angle=0,
                pen=pg.mkPen(CLR_TOL, width=1, style=Qt.DashLine),
                label=f"±{tol:.4g}",
                labelOpts={"color": CLR_TOL, "position": 0.05},
            )
            self._plot.addItem(line)
            self._tol_lines.append(line)

    # =========================================================================
    # Экспорт CSV
    # =========================================================================

    def _export_csv(self):
        done = [pt for pt in self.points if pt.etalon_val is not None]
        if not done:
            QMessageBox.warning(self, "Экспорт", "Нет данных для экспорта.")
            return

        fname, _ = QFileDialog.getSaveFileName(
            self, "Сохранить CSV",
            f"measurement_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            "CSV Files (*.csv)",
        )
        if not fname:
            return

        tol       = self._tol_spin.value()
        etype_str = self._etype_label()

        with open(fname, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["Дата",            datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
            w.writerow(["Эталон",          self._et_combo.currentText(),
                        "Масштаб",         self._et_scale.value()])
            w.writerow(["Поверяемый",      self._ms_combo.currentText(),
                        "Масштаб",         self._ms_scale.value()])
            w.writerow(["Нормирующее",     self._nominal.value()])
            w.writerow(["Тип погрешности", etype_str, "Допуск ±", tol])
            w.writerow([])
            w.writerow(["Точка", "Эталон", "Поверяемый",
                        "Δ абс.", "δ отн. (%)", "γ привед. (%)",
                        f"Допуск ±{tol}", "Статус"])
            for pt in done:
                rel = (f"{pt.rel_error:.6f}"
                       if not np.isnan(pt.rel_error) else "")
                red = (f"{pt.red_error:.6f}"
                       if not np.isnan(pt.red_error) else "")
                w.writerow([
                    pt.label, pt.etalon_val, pt.measured_val,
                    pt.abs_error, rel, red, tol,
                    "OK" if pt.passed else "NG",
                ])

        QMessageBox.information(self, "Экспорт", f"Данные сохранены:\n{fname}")

    # =========================================================================
    # Очистка результатов
    # =========================================================================

    def _clear_results(self):
        if self._running:
            QMessageBox.warning(self, "Очистка", "Сначала остановите испытание.")
            return
        if not self.points:
            return
        if QMessageBox.question(
            self, "Очистить результаты",
            "Сбросить все результаты измерений?\n"
            "Точки будут сохранены, данные очищены.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) != QMessageBox.Yes:
            return

        for pt in self.points:
            pt.samples_etalon = []
            pt.samples_measured = []
            pt.etalon_val = pt.measured_val = None
            pt.abs_error = pt.rel_error = pt.red_error = None
            pt.passed = None

        for c in (self._curve_abs, self._curve_rel,
                  self._curve_red, self._curve_et, self._curve_ms):
            c.setData([], [])
        for item in self._tol_lines:
            self._plot.removeItem(item)
        self._tol_lines.clear()
        self._refresh_table()

    # =========================================================================
    # Сохранение / загрузка методики
    # =========================================================================

    def _save_methodology(self):
        fname, _ = QFileDialog.getSaveFileName(
            self, "Сохранить методику",
            self._meth_name.text().strip() or "methodology",
            "Методики (*.json)",
        )
        if not fname:
            return

        data = {
            "version":      "1.0",
            "name":         self._meth_name.text().strip(),
            "saved_at":     datetime.now().isoformat(timespec="seconds"),
            "etalon_key":   self._et_combo.currentText(),
            "etalon_scale": self._et_scale.value(),
            "meas_key":     self._ms_combo.currentText(),
            "meas_scale":   self._ms_scale.value(),
            "nominal":      self._nominal.value(),
            "error_type":   self._err_combo.currentIndex(),
            "tolerance":    self._tol_spin.value(),
            "n_samples":    self._n_spin.value(),
            "interval_ms":  self._interval_spin.value(),
            "chart_mode":   self._chart_combo.currentIndex(),
            "points": [
                {"setpoint": pt.setpoint, "label": pt.label}
                for pt in self.points
            ],
        }
        try:
            with open(fname, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            QMessageBox.information(self, "Методика", f"Сохранено:\n{fname}")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить:\n{e}")

    def _load_methodology(self):
        if self._running:
            QMessageBox.warning(self, "Загрузка", "Сначала остановите испытание.")
            return

        fname, _ = QFileDialog.getOpenFileName(
            self, "Загрузить методику", "", "Методики (*.json)"
        )
        if not fname:
            return
        try:
            with open(fname, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось прочитать файл:\n{e}")
            return

        self._meth_name.setText(data.get("name", ""))
        available = self._available_params()

        et_key = data.get("etalon_key", "")
        if et_key in available:
            self._et_combo.setCurrentText(et_key)
        else:
            self._et_combo.setCurrentIndex(-1)
        self._et_scale.setValue(data.get("etalon_scale", 1.0))

        ms_key = data.get("meas_key", "")
        if ms_key in available:
            self._ms_combo.setCurrentText(ms_key)
        else:
            self._ms_combo.setCurrentIndex(-1)
        self._ms_scale.setValue(data.get("meas_scale", 1.0))

        self._nominal.setValue(data.get("nominal", 100.0))
        err_type = data.get("error_type", 0)
        if 0 <= err_type < self._err_combo.count():
            self._err_combo.setCurrentIndex(err_type)
        self._tol_spin.setValue(data.get("tolerance", 0.1))
        self._n_spin.setValue(data.get("n_samples", 10))
        self._interval_spin.setValue(data.get("interval_ms", 500))
        chart_mode = data.get("chart_mode", 0)
        if 0 <= chart_mode < self._chart_combo.count():
            self._chart_combo.setCurrentIndex(chart_mode)

        self.points.clear()
        for pd in data.get("points", []):
            self.points.append(MeasurementPoint(
                setpoint=pd.get("setpoint", 0.0),
                label=pd.get("label", ""),
            ))

        self._refresh_table()
        for c in (self._curve_abs, self._curve_rel,
                  self._curve_red, self._curve_et, self._curve_ms):
            c.setData([], [])
        for item in self._tol_lines:
            self._plot.removeItem(item)
        self._tol_lines.clear()

        missing = []
        if et_key and et_key not in available:
            missing.append(f"Эталон: {et_key}")
        if ms_key and ms_key not in available:
            missing.append(f"Поверяемый: {ms_key}")

        info = (
            f"Методика «{data.get('name', os.path.basename(fname))}» загружена.\n"
            f"Точек: {len(self.points)}"
        )
        if missing:
            info += (
                "\n\n⚠ Устройства недоступны (добавьте и выберите вручную):\n"
                + "\n".join(f"  • {m}" for m in missing)
            )
        QMessageBox.information(self, "Методика загружена", info)

    # =========================================================================
    # Вспомогательные методы
    # =========================================================================

    def _etype_label(self) -> str:
        return ["γ (%)", "δ (%)", "Δ"][self._err_combo.currentIndex()]

    def _etype_short(self) -> str:
        return ["γ", "δ", "Δ"][self._err_combo.currentIndex()]

    # =========================================================================
    # Стили
    # =========================================================================

    def _apply_styles(self):
        self.setStyleSheet("""
            QWidget { background:#0d1117; color:#e6edf3; font-family:Consolas; }

            QGroupBox {
                border:1px solid #30363d; border-radius:4px;
                margin-top:12px; padding-top:8px;
                font-weight:bold; color:#8b949e;
            }
            QGroupBox::title {
                subcontrol-origin:margin; left:10px; padding:0 4px;
            }

            QPushButton {
                background:#21262d; border:none; border-radius:4px;
                padding:5px 12px; color:#e6edf3; font-weight:bold;
            }
            QPushButton:hover    { background:#00d4ff; color:#0d1117; }
            QPushButton:disabled { background:#161b22; color:#484f58; }

            QTableWidget {
                background:#0d1117; color:#e6edf3;
                gridline-color:#30363d;
                selection-background-color:#1f6feb;
            }
            QHeaderView::section {
                background:#161b22; color:#8b949e;
                border:none; padding:4px;
            }

            QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {
                background:#161b22; color:#e6edf3;
                border:1px solid #30363d; padding:3px; border-radius:3px;
            }
            QComboBox::drop-down { border:none; }
            QComboBox QAbstractItemView { background:#161b22; color:#e6edf3; }

            QProgressBar {
                background:#161b22; border:1px solid #30363d;
                border-radius:3px; color:#e6edf3; text-align:center;
            }
            QProgressBar::chunk { background:#1f6feb; border-radius:3px; }

            /* Панель ожидания — оранжевая рамка */
            QFrame#InstructionBanner {
                border: 2px solid #e36209;
                border-radius: 6px;
                background: #1a1200;
            }

            QDialog { background:#161b22; }
        """)


# ─────────────────────────────────────────────────────────────────────────────
# Вспомогательные функции
# ─────────────────────────────────────────────────────────────────────────────

def _make_scale_spin(default: float = 1.0) -> QDoubleSpinBox:
    sp = QDoubleSpinBox()
    sp.setRange(1e-9, 1e9)
    sp.setValue(default)
    sp.setDecimals(6)
    sp.setFixedWidth(110)
    sp.setToolTip("Масштабный коэффициент: значение × масштаб")
    return sp


def _param_row(combo: QComboBox, scale_spin: QDoubleSpinBox) -> QHBoxLayout:
    row = QHBoxLayout()
    row.addWidget(combo, stretch=1)
    row.addWidget(QLabel("× масштаб:"))
    row.addWidget(scale_spin)
    return row


def _vline() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.VLine)
    f.setStyleSheet("color:#30363d;")
    f.setFixedHeight(22)
    return f
