"""
panels/pts_panel.py  v3.1.2  (2026-04-24)

Панель генератора/эталона MTE/EMH PTSx.xC.

Исправления v3.1.2:
  - Корректный фоновый опрос через сигнал value_updated.
  - Таблица автоматически обновляется каждую секунду.
"""

import time
from typing import Optional

from PyQt5.QtCore import Qt, pyqtSignal, QTimer
from PyQt5.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox,
    QFrame, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from panels.base_device_panel import BaseDevicePanel
from drivers.pts_driver import PTSx_Client


# ─────────────────────────────────────────────────────────────────────────────
_PARAM_DEFS = [
    ("Ua",    "В",   "Напряжение фаза A"),
    ("Ub",    "В",   "Напряжение фаза B"),
    ("Uc",    "В",   "Напряжение фаза C"),
    ("Ia",    "А",   "Ток фаза A"),
    ("Ib",    "А",   "Ток фаза B"),
    ("Ic",    "А",   "Ток фаза C"),
    ("Pa",    "Вт",  "Активная мощность A"),
    ("Pb",    "Вт",  "Активная мощность B"),
    ("Pc",    "Вт",  "Активная мощность C"),
    ("Qa",    "вар", "Реактивная мощность A"),
    ("Qb",    "вар", "Реактивная мощность B"),
    ("Qc",    "вар", "Реактивная мощность C"),
    ("phi_a", "°",   "Угол ток/напряжение A"),
    ("phi_b", "°",   "Угол ток/напряжение B"),
    ("phi_c", "°",   "Угол ток/напряжение C"),
    ("f",     "Гц",  "Частота"),
    ("P_sum", "Вт",  "Активная мощность суммарная"),
    ("Q_sum", "вар", "Реактивная мощность суммарная"),
]


class PTSPanel(BaseDevicePanel):
    value_updated = pyqtSignal(str, float)

    def __init__(self, device_name: str, parent=None):
        super().__init__("PTS", device_name, parent)
        self._client: Optional[PTSx_Client] = None

        for name, unit, label in _PARAM_DEFS:
            self.parameters[name] = {"unit": unit, "label": label}
            self.last_values[name] = None

        self.poll_interval = 1.0
        self.poll_interval_spin.setValue(1.0)

        self.value_updated.connect(self._update_gui_and_emit)

        self._build_specific_ui()
        self._rebuild_values_table()

    @property
    def pts_client(self) -> Optional[PTSx_Client]:
        return self._client

    # ─────────────────────────────────────────────────────────────────────
    def _build_specific_ui(self):
        self._port_edit = QLineEdit("COM1")
        self._port_edit.setPlaceholderText("COM3")
        self.add_setting_row("COM-порт:", self._port_edit)

        self._baud_combo = QComboBox()
        self._baud_combo.addItems(["9600", "19200", "38400", "57600", "115200"])
        self._baud_combo.setCurrentText("19200")
        self.add_setting_row("Скорость:", self._baud_combo)

        self._timeout_edit = QLineEdit("2.0")
        self.add_setting_row("Таймаут (с):", self._timeout_edit)

        src_box = self._build_source_box()
        self.add_setting_widget(src_box)

        # Тестовая кнопка (можно удалить после проверки)
        self._test_btn = QPushButton("Тест чтения")
        self._test_btn.clicked.connect(self._test_read)
        self.add_setting_widget(self._test_btn)

    def _build_source_box(self) -> QGroupBox:
        gb = QGroupBox("Источник — управление")
        vb = QVBoxLayout(gb)
        vb.setSpacing(4)
        vb.setContentsMargins(6, 8, 6, 6)

        self._sym_chk = QCheckBox("Симметричный (все фазы одинаковые)")
        self._sym_chk.setChecked(True)
        self._sym_chk.toggled.connect(self._on_sym_toggled)
        vb.addWidget(self._sym_chk)

        vb.addWidget(_sep())

        self._sp_ua = _sp(0.0, 0.0, 1000.0, 1, "В")
        self._sp_ub = _sp(0.0, 0.0, 1000.0, 1, "В")
        self._sp_uc = _sp(0.0, 0.0, 1000.0, 1, "В")
        self._row_u = _sp_row("U (В):", self._sp_ua, self._sp_ub, self._sp_uc)
        vb.addLayout(self._row_u)

        self._sp_ia = _sp(0.0, 0.0, 120.0, 4, "А")
        self._sp_ib = _sp(0.0, 0.0, 120.0, 4, "А")
        self._sp_ic = _sp(0.0, 0.0, 120.0, 4, "А")
        self._row_i = _sp_row("I (А):", self._sp_ia, self._sp_ib, self._sp_ic)
        vb.addLayout(self._row_i)

        self._sp_pa = _sp(0.0, -360.0, 360.0, 2, "°")
        self._sp_pb = _sp(0.0, -360.0, 360.0, 2, "°")
        self._sp_pc = _sp(0.0, -360.0, 360.0, 2, "°")
        self._row_p = _sp_row("φ (°):", self._sp_pa, self._sp_pb, self._sp_pc)
        vb.addLayout(self._row_p)

        self._sp_f = _sp(50.0, 0.0, 400.0, 2, "Гц")
        f_row = QHBoxLayout()
        f_row.addWidget(QLabel("f (Гц):"))
        f_row.addWidget(self._sp_f)
        vb.addLayout(f_row)

        for sp_a, sp_b, sp_c in (
            (self._sp_ua, self._sp_ub, self._sp_uc),
            (self._sp_ia, self._sp_ib, self._sp_ic),
            (self._sp_pa, self._sp_pb, self._sp_pc),
        ):
            sp_a.valueChanged.connect(
                lambda v, b=sp_b, c=sp_c: self._sync_bc(v, b, c))

        self._on_sym_toggled(True)

        vb.addWidget(_sep())

        self._btn_start = QPushButton("▶  Старт (SET)")
        self._btn_stop  = QPushButton("■  Стоп (плавно)")
        self._btn_emg   = QPushButton("⚠  АВАРИЙНО")
        self._btn_emg.setStyleSheet(
            "background:#7d1f1f; color:#ffd43b; font-weight:bold;")
        for btn in (self._btn_start, self._btn_stop, self._btn_emg):
            btn.setEnabled(False)
        self._btn_start.clicked.connect(self._on_start)
        self._btn_stop.clicked.connect(self._on_stop)
        self._btn_emg.clicked.connect(self._on_emergency)

        btn_row = QHBoxLayout()
        btn_row.addWidget(self._btn_start)
        btn_row.addWidget(self._btn_stop)
        btn_row.addWidget(self._btn_emg)
        vb.addLayout(btn_row)

        self._src_status_lbl = QLabel("○  Не подключено")
        self._src_status_lbl.setStyleSheet("color:#8b949e; font-size:9pt;")
        vb.addWidget(self._src_status_lbl)

        return gb

    # ─────────────────────────────────────────────────────────────────────
    def connect_device(self) -> bool:
        port = self._port_edit.text().strip()
        baud = int(self._baud_combo.currentText())
        try:
            timeout = float(self._timeout_edit.text().strip())
        except ValueError:
            timeout = 2.0
        try:
            self._client = PTSx_Client(
                port, baud, line_timeout=0.5, cmd_timeout=timeout)
            self._client.mode1()
            ref = self._client.reference
            ref.enable_all_results()
            ref.set_timebase(1)
            ref.set_auto_range()

            for btn in (self._btn_start, self._btn_stop, self._btn_emg):
                btn.setEnabled(True)
            self._src_status_lbl.setText("○  Подключено, выход выключен")
            self._src_status_lbl.setStyleSheet("color:#3fb950; font-size:9pt;")
            self.log_event(f"PTS подключён: {port} @ {baud} бод")

            self.start_polling()
            return True

        except Exception as e:
            self._client = None
            self.log_event(f"Ошибка подключения PTS: {e}")
            QMessageBox.critical(self, "PTS", f"Не удалось подключиться:\n{e}")
            return False

    def disconnect_device(self):
        if self.is_polling:
            self.stop_polling()
        if self._client:
            try:
                self._client.source.off(mode=1)
            except Exception:
                pass
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
        for btn in (self._btn_start, self._btn_stop, self._btn_emg):
            btn.setEnabled(False)
        self._src_status_lbl.setText("○  Отключено")
        self._src_status_lbl.setStyleSheet("color:#8b949e; font-size:9pt;")
        self.log_event("PTS отключён")

    # ─────────────────────────────────────────────────────────────────────
    def _poll_loop(self):
        while self.is_polling:
            try:
                data = self.read_device_values()
                for name, value in data.items():
                    self.value_updated.emit(name, value)
                if data:
                    QTimer.singleShot(0, lambda d=data: self.log_measurement(d))
            except Exception as e:
                QTimer.singleShot(0, lambda err=str(e): self.log_event(f"Опрос PTS: {err}"))
            time.sleep(self.poll_interval)

    # ─────────────────────────────────────────────────────────────────────
    def read_device_values(self) -> dict:
        if not self._client:
            return {}
        ref = self._client.reference
        values: dict = {}

        def safe(fn, keys):
            try:
                result = fn()
                if not result:
                    return
                for k, v in zip(keys, result):
                    if v is not None:
                        values[k] = float(v)
            except Exception as e:
                self.log_event(f"PTS read [{fn.__name__}]: {e}")

        safe(ref.read_voltages,              ["Ua", "Ub", "Uc"])
        safe(ref.read_currents,              ["Ia", "Ib", "Ic"])
        safe(ref.read_active_power_phases,   ["Pa", "Pb", "Pc"])
        safe(ref.read_reactive_power_phases, ["Qa", "Qb", "Qc"])
        safe(ref.read_phase_angles,          ["phi_a", "phi_b", "phi_c"])

        try:
            f = ref.read_frequency()
            if f is not None:
                values["f"] = float(f)
        except Exception as e:
            self.log_event(f"PTS read [frequency]: {e}")

        try:
            p = ref.read_active_power_sum()
            if p is not None:
                values["P_sum"] = float(p)
        except Exception as e:
            self.log_event(f"PTS read [P_sum]: {e}")

        try:
            q = ref.read_reactive_power_sum()
            if q is not None:
                values["Q_sum"] = float(q)
        except Exception as e:
            self.log_event(f"PTS read [Q_sum]: {e}")

        return values

    # ─────────────────────────────────────────────────────────────────────
    def _on_start(self):
        if not self._client:
            return
        try:
            src = self._client.source
            src.set_voltage(
                self._sp_ua.value(), self._sp_ub.value(), self._sp_uc.value())
            src.set_current(
                self._sp_ia.value(), self._sp_ib.value(), self._sp_ic.value())
            src.set_angle(
                self._sp_pa.value(), self._sp_pb.value(), self._sp_pc.value())
            src.set_frequency(self._sp_f.value())
            src.on()
            self._src_status_lbl.setText("●  Выход включён")
            self._src_status_lbl.setStyleSheet("color:#3fb950; font-size:9pt;")
            self.log_event(
                f"SET: Ua={self._sp_ua.value():.1f} В  "
                f"Ia={self._sp_ia.value():.4f} А  "
                f"φa={self._sp_pa.value():.2f}°  "
                f"f={self._sp_f.value():.2f} Гц")
        except Exception as e:
            QMessageBox.critical(self, "PTS — Старт", str(e))
            self.log_event(f"Ошибка SET: {e}")

    def _on_stop(self):
        if not self._client:
            return
        try:
            self._client.source.off(mode=0)
            self._src_status_lbl.setText("○  Выход выключен (плавно)")
            self._src_status_lbl.setStyleSheet("color:#ffd43b; font-size:9pt;")
            self.log_event("OFF плавно")
        except Exception as e:
            QMessageBox.critical(self, "PTS — Стоп", str(e))

    def _on_emergency(self):
        if not self._client:
            return
        try:
            self._client.source.off(mode=1)
        except Exception:
            try:
                self._client.source.off(mode=1)
            except Exception:
                pass
        self._src_status_lbl.setText("⚠  АВАРИЙНОЕ ОТКЛЮЧЕНИЕ")
        self._src_status_lbl.setStyleSheet(
            "color:#f85149; font-weight:bold; font-size:9pt;")
        self.log_event("OFF1 — АВАРИЙНОЕ ОТКЛЮЧЕНИЕ")

    # ─────────────────────────────────────────────────────────────────────
    def apply_settings(self, setpoints: dict):
        if not self._client:
            raise RuntimeError("PTSPanel: не подключён")
        src = self._client.source
        ua = setpoints.get("Ua"); ub = setpoints.get("Ub"); uc = setpoints.get("Uc")
        ia = setpoints.get("Ia"); ib = setpoints.get("Ib"); ic = setpoints.get("Ic")
        pa = setpoints.get("phi_a"); pb = setpoints.get("phi_b"); pc = setpoints.get("phi_c")
        f  = setpoints.get("f")
        if any(v is not None for v in (ua, ub, uc)):
            ua = ua if ua is not None else 0.0
            ub = ub if ub is not None else ua
            uc = uc if uc is not None else ua
            src.set_voltage(ua, ub, uc)
        if any(v is not None for v in (ia, ib, ic)):
            ia = ia if ia is not None else 0.0
            ib = ib if ib is not None else ia
            ic = ic if ic is not None else ia
            src.set_current(ia, ib, ic)
        if any(v is not None for v in (pa, pb, pc)):
            pa = pa if pa is not None else 0.0
            pb = pb if pb is not None else pa
            pc = pc if pc is not None else pa
            src.set_angle(pa, pb, pc)
        if f is not None:
            src.set_frequency(f)
        src.on()
        self._src_status_lbl.setText("●  Выход включён (авто-тест)")
        self._src_status_lbl.setStyleSheet("color:#3fb950; font-size:9pt;")
        self.log_event(f"apply_settings: {setpoints}")

    def output_off(self):
        if self._client:
            try:
                self._client.source.off(mode=1)
                self._src_status_lbl.setText("○  Выход выключен (авто-тест)")
                self._src_status_lbl.setStyleSheet("color:#8b949e; font-size:9pt;")
                self.log_event("output_off: OFF1")
            except Exception:
                pass

    # ─────────────────────────────────────────────────────────────────────
    def _on_sym_toggled(self, checked: bool):
        for row in (self._row_u, self._row_i, self._row_p):
            for idx in (2, 3):
                item = row.itemAt(idx)
                if item and item.widget():
                    item.widget().setVisible(not checked)
        if checked:
            self._sync_bc(self._sp_ua.value(), self._sp_ub, self._sp_uc)
            self._sync_bc(self._sp_ia.value(), self._sp_ib, self._sp_ic)
            self._sync_bc(self._sp_pa.value(), self._sp_pb, self._sp_pc)

    def _sync_bc(self, val: float, b: QDoubleSpinBox, c: QDoubleSpinBox):
        if self._sym_chk.isChecked():
            b.blockSignals(True); c.blockSignals(True)
            b.setValue(val);      c.setValue(val)
            b.blockSignals(False); c.blockSignals(False)

    def _test_read(self):
        data = self.read_device_values()
        self.log_event(f"Тест чтения: {data}")

    # ─────────────────────────────────────────────────────────────────────
    def get_config(self) -> dict:
        cfg = super().get_config()
        cfg.update({
            "port":      self._port_edit.text().strip(),
            "baudrate":  int(self._baud_combo.currentText()),
            "timeout":   self._timeout_edit.text().strip(),
            "symmetric": self._sym_chk.isChecked(),
            "setpoints": {
                "Ua": self._sp_ua.value(), "Ub": self._sp_ub.value(),
                "Uc": self._sp_uc.value(),
                "Ia": self._sp_ia.value(), "Ib": self._sp_ib.value(),
                "Ic": self._sp_ic.value(),
                "phi_a": self._sp_pa.value(), "phi_b": self._sp_pb.value(),
                "phi_c": self._sp_pc.value(), "f": self._sp_f.value(),
            },
        })
        return cfg

    def apply_config(self, config: dict):
        super().apply_config(config)
        if not hasattr(self, "_port_edit"):
            return
        self._port_edit.setText(config.get("port", "COM1"))
        self._baud_combo.setCurrentText(str(config.get("baudrate", 19200)))
        self._timeout_edit.setText(str(config.get("timeout", "2.0")))
        self._sym_chk.setChecked(config.get("symmetric", True))
        sp = config.get("setpoints", {})
        for key, widget in [
            ("Ua", self._sp_ua), ("Ub", self._sp_ub), ("Uc", self._sp_uc),
            ("Ia", self._sp_ia), ("Ib", self._sp_ib), ("Ic", self._sp_ic),
            ("phi_a", self._sp_pa), ("phi_b", self._sp_pb), ("phi_c", self._sp_pc),
            ("f", self._sp_f),
        ]:
            if key in sp:
                widget.setValue(float(sp[key]))

    def closeEvent(self, event):
        self.stop_polling()
        self.disconnect_device()
        super().closeEvent(event)


# ─────────────────────────────────────────────────────────────────────────────
def _sp(default: float, min_: float, max_: float,
        decs: int = 2, suffix: str = "") -> QDoubleSpinBox:
    w = QDoubleSpinBox()
    w.setRange(min_, max_)
    w.setValue(default)
    w.setDecimals(decs)
    w.setSuffix(f" {suffix}" if suffix else "")
    return w


def _sp_row(label: str, sp_a: QDoubleSpinBox,
            sp_b: QDoubleSpinBox, sp_c: QDoubleSpinBox) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setSpacing(4)
    lbl = QLabel(label)
    lbl.setFixedWidth(42)
    lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    row.addWidget(lbl)
    row.addWidget(sp_a)
    row.addWidget(sp_b)
    row.addWidget(sp_c)
    return row


def _sep() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.HLine)
    f.setStyleSheet("color:#30363d;")
    return f