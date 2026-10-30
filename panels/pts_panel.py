"""
Автор: m.yablokov
panels/pts_panel.py  v4.0.0

Панель генератора/эталона MTE/EMH PTSx.xC.

Что нового в v4.0.0:
  БЕЗОПАСНОСТЬ (до 300 В / 120 А)
  - Старт/Стоп выполняются в фоновом потоке — окно не "замерзает" на время SET
    (раньше до 30 с), поэтому кнопка АВАРИЙНО всегда доступна.
  - АВАРИЙНО прерывает ожидание SET и шлёт OFF1 (до 3 попыток).
  - Лимиты U / I / S на фазу (вкладка "Источник"), проверяются в драйвере.
  - Подтверждение мощных уставок (U ≥ 250 В или I ≥ 20 А).
  - Статус источника SE/STATI/STATU и флаги перегрузки эталона ROLI/ROLU
    (вкладка "Статус", обновляется в фоновом опросе).
  - Статусная строка обновляется через сигнал — без обращения к виджетам
    из чужих потоков (apply_settings вызывается из потока автотеста).

  УПРАВЛЕНИЕ (вся документация)
  - Вкладка "Нагрузка": U, I, φ (W), f, абсолютные фазы U (PH).
  - Вкладка "Источник": рампы RAMPI/RAMPU, реле токовых выходов SKLI,
    3/4-проводный режим TRI, диапазоны BI/BU, лимиты.
  - Вкладка "Гармоники": OWU/OWI (гармоники 0…31, амплитуда %, фаза °).
  - Вкладка "Эталон": AUT/MAN, диапазоны, входы DB/DBU, режим P4/P3/N4/…,
    YI/YU, IAL/UAL, время опроса T; кнопки SG и VER в лог.
  - Вкладка "Статус": состояние источника и эталона.

Все добавленные опции по умолчанию стоят в положении "Не менять" —
поведение при обычной работе такое же, как в v3.1.2.
"""

import threading
import time
from typing import Optional

from PyQt5.QtCore import Qt, pyqtSignal, QTimer
from PyQt5.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFrame, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox, QTabWidget,
    QVBoxLayout, QWidget,
)

from panels.base_device_panel import BaseDevicePanel
from drivers.pts_driver import (
    PTSx_Client, triple_from, DEFAULT_MAX_VOLTAGE, DEFAULT_MAX_CURRENT,
)

# ─────────────────────────────────────────────────────────────────────────────
# Настройки безопасности (правятся здесь)
# ─────────────────────────────────────────────────────────────────────────────
CONFIRM_VOLTAGE = 250.0    # В  — с этого напряжения просим подтверждение
CONFIRM_CURRENT = 20.0     # А  — с этого тока просим подтверждение

GREEN, GREY, YELLOW, RED = "#3fb950", "#8b949e", "#ffd43b", "#f85149"

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

_DB_ITEMS = [
    "0 — 120 А (станд.)", "1 — 12 А", "2 — клещи 100 А", "3 — клещи 1000 А",
    "4 — LEMflex 30 А", "5 — LEMflex 300 А", "6 — LEMflex 3000 А",
    "7 — Hotstick U", "8 — Hotstick I",
]
_DBU_ITEMS = [
    "0 — станд. вход", "1 — альтернативный", "2 — клещи 100 А (в U-тракт)",
    "3 — клещи 1000 А (в U-тракт)", "4 — LEMflex 30 А (в U-тракт)",
    "5 — LEMflex 300 А (в U-тракт)", "6 — LEMflex 3000 А (в U-тракт)",
    "7 — Hotstick U", "8 — Hotstick I (в U-тракт)",
]


class PTSPanel(BaseDevicePanel):
    value_updated   = pyqtSignal(str, float)
    pts_status_text = pyqtSignal(str, str, bool)          # текст, цвет, жирный
    pts_op_done     = pyqtSignal(str, str, str, str)      # операция, ошибка, ok-текст, ok-цвет
    pts_state       = pyqtSignal(dict)                    # состояние для вкладки "Статус"

    def __init__(self, device_name: str, parent=None):
        super().__init__("PTS", device_name, parent)
        self._client: Optional[PTSx_Client] = None
        self._op_lock = threading.Lock()      # один "длинный" сценарий за раз
        self._opts_cache: Optional[dict] = None
        self._sent = self._fresh_sent()
        self._last_alarm = ""
        self._harm_defs: dict = {}            # {(kind, phase): {order: (amp, phi)}}

        for name, unit, label in _PARAM_DEFS:
            self.parameters[name] = {"unit": unit, "label": label}
            self.last_values[name] = None

        self.poll_interval = 1.0
        self.poll_interval_spin.setValue(1.0)

        self.value_updated.connect(self._update_gui_and_emit)
        self.pts_status_text.connect(self._on_status_text)
        self.pts_op_done.connect(self._on_op_done)
        self.pts_state.connect(self._on_state)

        self._build_specific_ui()
        self._rebuild_values_table()

    @property
    def pts_client(self) -> Optional[PTSx_Client]:
        return self._client

    @staticmethod
    def _fresh_sent() -> dict:
        """Что мы уже отправили на прибор (чтобы не слать лишнее при каждом SET)."""
        return {"ramp": None, "wire": None, "bi": 0, "bu": 0}

    # ═════════════════════════════════════════════════════════════════════
    #  UI
    # ═════════════════════════════════════════════════════════════════════
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

        # кнопки управления и статус — над вкладками, всегда на виду
        self.add_setting_widget(self._build_control_box())

        tabs = QTabWidget()
        tabs.addTab(self._build_load_tab(),      "Нагрузка")
        tabs.addTab(self._build_options_tab(),   "Источник")
        tabs.addTab(self._build_harmonics_tab(), "Гармоники")
        tabs.addTab(self._build_reference_tab(), "Эталон")
        tabs.addTab(self._build_status_tab(),    "Статус")
        self.add_setting_widget(tabs)

        # Тестовая кнопка (можно удалить после проверки)
        self._test_btn = QPushButton("Тест чтения")
        self._test_btn.clicked.connect(self._test_read)
        self.add_setting_widget(self._test_btn)

        # лимиты → максимумы спинбоксов; любые опции → кэш опций
        self._sp_lim_u.valueChanged.connect(self._on_limits_changed)
        self._sp_lim_i.valueChanged.connect(self._on_limits_changed)
        self._watch(
            self._chk_ramp, self._sp_ramp_i, self._sp_ramp_u, self._cmb_relay,
            self._cmb_wire, self._spi_bi, self._spi_bu, self._chk_ph,
            self._sp_ph_a, self._sp_ph_b, self._sp_ph_c,
            self._sp_lim_u, self._sp_lim_i, self._sp_lim_s, self._chk_confirm,
        )
        self._refresh_opts()

    # ---- кнопки + статус ---------------------------------------------------
    def _build_control_box(self) -> QWidget:
        w = QWidget()
        vb = QVBoxLayout(w)
        vb.setContentsMargins(0, 0, 0, 0)
        vb.setSpacing(4)

        self._btn_start = QPushButton("▶  Старт / Применить (SET)")
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
        self._src_status_lbl.setStyleSheet(f"color:{GREY}; font-size:9pt;")
        vb.addWidget(self._src_status_lbl)
        return w

    # ---- вкладка "Нагрузка" ------------------------------------------------
    def _build_load_tab(self) -> QWidget:
        w = QWidget()
        vb = QVBoxLayout(w)
        vb.setSpacing(4)
        vb.setContentsMargins(6, 8, 6, 6)

        self._sym_chk = QCheckBox("Симметричный (все фазы одинаковые)")
        self._sym_chk.setChecked(True)
        self._sym_chk.toggled.connect(self._on_sym_toggled)
        vb.addWidget(self._sym_chk)
        vb.addWidget(_sep())

        self._sp_ua = _sp(0.0, 0.0, DEFAULT_MAX_VOLTAGE, 1, "В")
        self._sp_ub = _sp(0.0, 0.0, DEFAULT_MAX_VOLTAGE, 1, "В")
        self._sp_uc = _sp(0.0, 0.0, DEFAULT_MAX_VOLTAGE, 1, "В")
        self._row_u = _sp_row("U (В):", self._sp_ua, self._sp_ub, self._sp_uc)
        vb.addLayout(self._row_u)

        self._sp_ia = _sp(0.0, 0.0, DEFAULT_MAX_CURRENT, 4, "А")
        self._sp_ib = _sp(0.0, 0.0, DEFAULT_MAX_CURRENT, 4, "А")
        self._sp_ic = _sp(0.0, 0.0, DEFAULT_MAX_CURRENT, 4, "А")
        self._row_i = _sp_row("I (А):", self._sp_ia, self._sp_ib, self._sp_ic)
        vb.addLayout(self._row_i)

        self._sp_pa = _sp(0.0, -360.0, 360.0, 2, "°")
        self._sp_pb = _sp(0.0, -360.0, 360.0, 2, "°")
        self._sp_pc = _sp(0.0, -360.0, 360.0, 2, "°")
        self._row_p = _sp_row("φ (°):", self._sp_pa, self._sp_pb, self._sp_pc)
        vb.addLayout(self._row_p)
        self._sp_pa.setToolTip("Угол между U и I фазы (команда W)")

        self._sp_f = _sp(50.0, 0.0, 400.0, 2, "Гц")
        self._sp_f.setToolTip(
            "Частота (FRQ). 0 = синхронизация с сетью (если опция есть)")
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

        vb.addWidget(_sep())

        # абсолютные фазы напряжений (PH) — не синхронизируются при "симметричном"
        self._chk_ph = QCheckBox("Задавать абсолютные фазы U (PH)")
        self._chk_ph.setToolTip(
            "Команда PH: абсолютный фазовый угол напряжений U1…U3.\n"
            "Значения по умолчанию 0 / 240 / 120 — проверьте на приборе.")
        vb.addWidget(self._chk_ph)
        self._sp_ph_a = _sp(0.0,   0.0, 360.0, 2, "°")
        self._sp_ph_b = _sp(240.0, 0.0, 360.0, 2, "°")
        self._sp_ph_c = _sp(120.0, 0.0, 360.0, 2, "°")
        vb.addLayout(_sp_row("PH (°):", self._sp_ph_a, self._sp_ph_b, self._sp_ph_c))

        self._on_sym_toggled(True)

        hint = QLabel("Повторное нажатие «Старт / Применить» применяет новые "
                      "уставки без отключения выхода.")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{GREY}; font-size:8pt;")
        vb.addWidget(hint)
        vb.addStretch(1)
        return w

    # ---- вкладка "Источник" ------------------------------------------------
    def _build_options_tab(self) -> QWidget:
        w = QWidget()
        vb = QVBoxLayout(w)
        vb.setSpacing(6)
        vb.setContentsMargins(6, 8, 6, 6)

        # Пределы
        gb_lim = QGroupBox("Защитные пределы (проверяются в драйвере)")
        lv = QVBoxLayout(gb_lim)
        self._sp_lim_u = _sp(DEFAULT_MAX_VOLTAGE, 1.0, DEFAULT_MAX_VOLTAGE, 1, "В")
        self._sp_lim_i = _sp(DEFAULT_MAX_CURRENT, 0.1, DEFAULT_MAX_CURRENT, 2, "А")
        self._sp_lim_s = _sp(0.0, 0.0, 100000.0, 0, "ВА")
        self._sp_lim_s.setToolTip("Лимит полной мощности U·I на фазу. 0 = не проверять.")
        for text, sp in (("Макс. U:", self._sp_lim_u), ("Макс. I:", self._sp_lim_i),
                         ("Макс. S на фазу (0 = выкл):", self._sp_lim_s)):
            r = QHBoxLayout()
            r.addWidget(QLabel(text))
            r.addWidget(sp)
            lv.addLayout(r)
        self._chk_confirm = QCheckBox(
            f"Подтверждать мощные уставки (U ≥ {CONFIRM_VOLTAGE:.0f} В "
            f"или I ≥ {CONFIRM_CURRENT:.0f} А)")
        self._chk_confirm.setChecked(True)
        lv.addWidget(self._chk_confirm)
        vb.addWidget(gb_lim)

        # Рампа
        gb_ramp = QGroupBox("Рампа включения / выключения (RAMPI / RAMPU)")
        rv = QVBoxLayout(gb_ramp)
        self._chk_ramp = QCheckBox("Задавать рампу")
        rv.addWidget(self._chk_ramp)
        self._sp_ramp_i = _sp(0.0, 0.0, 600.0, 1, "с")
        self._sp_ramp_u = _sp(0.0, 0.0, 600.0, 1, "с")
        for text, sp in (("Ток:", self._sp_ramp_i), ("Напряжение:", self._sp_ramp_u)):
            r = QHBoxLayout()
            r.addWidget(QLabel(text))
            r.addWidget(sp)
            rv.addLayout(r)
        vb.addWidget(gb_ramp)

        # Реле / проводка / диапазоны
        gb_hw = QGroupBox("Токовые выходы, схема, диапазоны")
        hv = QVBoxLayout(gb_hw)

        self._cmb_relay = QComboBox()
        self._cmb_relay.addItems([
            "Не менять",
            "Разомкнуто: малые + большие выходы (SKLI0,0)",
            "Замкнуто: только сильноточные (SKLI0,1)",
        ])
        self._cmb_relay.setToolTip(
            "Реле между малыми и большими токовыми разъёмами (PTS x.xC до 120 А).\n"
            "В диапазонах тока 1–2 прибор сам размыкает реле.")
        hv.addWidget(QLabel("Реле токовых выходов:"))
        hv.addWidget(self._cmb_relay)

        self._cmb_wire = QComboBox()
        self._cmb_wire.addItems(["Не менять", "4-проводная (TRI0)", "3-проводная (TRI1)"])
        hv.addWidget(QLabel("Схема источника:"))
        hv.addWidget(self._cmb_wire)

        self._spi_bi = QSpinBox()
        self._spi_bi.setRange(0, 10)
        self._spi_bu = QSpinBox()
        self._spi_bu.setRange(0, 10)
        tip = ("Номер диапазона усилителя. 0 = автоматически наименьший "
               "подходящий (рекомендуется).")
        self._spi_bi.setToolTip(tip)
        self._spi_bu.setToolTip(tip)
        for text, sp in (("Диапазон тока BI (0 = авто):", self._spi_bi),
                         ("Диапазон напряжения BU (0 = авто):", self._spi_bu)):
            r = QHBoxLayout()
            r.addWidget(QLabel(text))
            r.addWidget(sp)
            hv.addLayout(r)
        vb.addWidget(gb_hw)

        vb.addStretch(1)
        return w

    # ---- вкладка "Гармоники" -----------------------------------------------
    def _build_harmonics_tab(self) -> QWidget:
        w = QWidget()
        vb = QVBoxLayout(w)
        vb.setSpacing(4)
        vb.setContentsMargins(6, 8, 6, 6)

        self._cmb_h_kind = QComboBox()
        self._cmb_h_kind.addItems(["Напряжение (OWU)", "Ток (OWI)"])
        self._cmb_h_phase = QComboBox()
        self._cmb_h_phase.addItems(["Фаза 1", "Фаза 2", "Фаза 3", "Все фазы"])
        self._spi_h_order = QSpinBox()
        self._spi_h_order.setRange(0, 31)
        self._spi_h_order.setValue(3)
        self._spi_h_order.setToolTip("0 = постоянная составляющая, 1 = основная, 2…31 = гармоники")
        self._sp_h_amp = _sp(10.0, 0.0, 100.0, 2, "%")
        self._sp_h_phi = _sp(0.0, 0.0, 360.0, 2, "°")

        for text, wd in (("Канал:", self._cmb_h_kind), ("Фаза:", self._cmb_h_phase),
                         ("Номер гармоники:", self._spi_h_order),
                         ("Амплитуда (% основной):", self._sp_h_amp),
                         ("Фаза гармоники:", self._sp_h_phi)):
            r = QHBoxLayout()
            r.addWidget(QLabel(text))
            r.addWidget(wd)
            vb.addLayout(r)

        btn_add = QPushButton("Добавить / изменить гармонику")
        btn_clr = QPushButton("Очистить гармоники выбранной фазы")
        btn_add.clicked.connect(self._on_harm_add)
        btn_clr.clicked.connect(self._on_harm_clear)
        vb.addWidget(btn_add)
        vb.addWidget(btn_clr)

        self._lbl_harm = QLabel("Гармоники не заданы")
        self._lbl_harm.setWordWrap(True)
        self._lbl_harm.setStyleSheet(f"color:{GREY}; font-size:8pt;")
        vb.addWidget(self._lbl_harm)

        warn = QLabel("Гармоники суммируются с основной: действующее значение "
                      "выходного тока/напряжения растёт — следите за пределами.\n"
                      "Включаются при ближайшем «Старт / Применить» "
                      "(или сразу, если PAR14 = 2/3).")
        warn.setWordWrap(True)
        warn.setStyleSheet(f"color:{YELLOW}; font-size:8pt;")
        vb.addWidget(warn)
        vb.addStretch(1)
        return w

    # ---- вкладка "Эталон" --------------------------------------------------
    def _build_reference_tab(self) -> QWidget:
        w = QWidget()
        vb = QVBoxLayout(w)
        vb.setSpacing(4)
        vb.setContentsMargins(6, 8, 6, 6)

        self._cmb_ref_range = QComboBox()
        self._cmb_ref_range.addItems(["Авто (AUT)", "Ручной (MAN)"])
        self._spi_ref_ri = QSpinBox()
        self._spi_ref_ri.setRange(1, 8)
        self._spi_ref_ru = QSpinBox()
        self._spi_ref_ru.setRange(1, 8)
        tip = "Номера диапазонов — табл. 5-3 / 5-4 документа. Действуют только при MAN."
        self._spi_ref_ri.setToolTip(tip)
        self._spi_ref_ru.setToolTip(tip)

        self._cmb_ref_meas = QComboBox()
        self._cmb_ref_meas.addItems(["Не менять", "P4", "P3", "N4", "N3", "K4", "K3"])
        self._cmb_ref_db = QComboBox()
        self._cmb_ref_db.addItems(["Не менять"] + _DB_ITEMS)
        self._cmb_ref_dbu = QComboBox()
        self._cmb_ref_dbu.addItems(["Не менять"] + _DBU_ITEMS)
        self._cmb_yi = _tri_combo()
        self._cmb_yu = _tri_combo()
        self._cmb_ial = _tri_combo()
        self._cmb_ual = _tri_combo()
        self._sp_timebase = _sp(1.0, 0.1, 999.9, 1, "с")
        self._sp_timebase.setToolTip("Время измерения T эталона (команда T)")

        rows = (
            ("Выбор диапазонов:", self._cmb_ref_range),
            ("Диапазон I (MAN):", self._spi_ref_ri),
            ("Диапазон U (MAN):", self._spi_ref_ru),
            ("Режим измерения:", self._cmb_ref_meas),
            ("Токовый вход (DB):", self._cmb_ref_db),
            ("Вход напряжения (DBU):", self._cmb_ref_dbu),
            ("Синхр. диапазонов I (YI):", self._cmb_yi),
            ("Синхр. диапазонов U (YU):", self._cmb_yu),
            ("Нижний предел I в AUT (IAL):", self._cmb_ial),
            ("Нижний предел U в AUT (UAL):", self._cmb_ual),
            ("Время измерения T:", self._sp_timebase),
        )
        for text, wd in rows:
            r = QHBoxLayout()
            r.addWidget(QLabel(text))
            r.addWidget(wd)
            vb.addLayout(r)

        btn_apply = QPushButton("Применить настройки эталона")
        btn_sg    = QPushButton("SG → лог")
        btn_ver   = QPushButton("VER → лог")
        btn_apply.clicked.connect(self._on_ref_apply)
        btn_sg.clicked.connect(lambda: self._log_command("SG", lambda c: c.reference.read_status()))
        btn_ver.clicked.connect(lambda: self._log_command("VER", lambda c: c.read_version()))
        vb.addWidget(btn_apply)
        r = QHBoxLayout()
        r.addWidget(btn_sg)
        r.addWidget(btn_ver)
        vb.addLayout(r)

        note = QLabel("Для 300 В / 120 А оставьте «Авто (AUT)»: прибор сам выберет "
                      "старшие диапазоны.")
        note.setWordWrap(True)
        note.setStyleSheet(f"color:{GREY}; font-size:8pt;")
        vb.addWidget(note)
        vb.addStretch(1)
        return w

    # ---- вкладка "Статус" --------------------------------------------------
    def _build_status_tab(self) -> QWidget:
        w = QWidget()
        vb = QVBoxLayout(w)
        vb.setContentsMargins(6, 8, 6, 6)
        self._lbl_state = QLabel("Нет данных (подключите прибор)")
        self._lbl_state.setWordWrap(True)
        self._lbl_state.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._lbl_state.setStyleSheet("font-family:Consolas,monospace; font-size:9pt;")
        vb.addWidget(self._lbl_state)
        btn = QPushButton("Обновить статус сейчас")
        btn.clicked.connect(self._on_status_refresh)
        vb.addWidget(btn)
        vb.addStretch(1)
        return w

    # ═════════════════════════════════════════════════════════════════════
    #  Подключение
    # ═════════════════════════════════════════════════════════════════════
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
            ref.set_timebase(self._sp_timebase.value())
            ref.set_auto_range()

            self._sent = self._fresh_sent()
            self._refresh_opts()
            self._apply_limits_to_client(self._opts_cache)
            self._log_device_info()

            for btn in (self._btn_start, self._btn_stop, self._btn_emg):
                btn.setEnabled(True)
            self._set_status("○  Подключено, выход выключен", GREEN)
            self.log_event(f"PTS подключён: {port} @ {baud} бод")

            self.start_polling()
            return True

        except Exception as e:
            self._client = None
            self.log_event(f"Ошибка подключения PTS: {e}")
            QMessageBox.critical(self, "PTS", f"Не удалось подключиться:\n{e}")
            return False

    def _log_device_info(self):
        """Версия прибора и границы частоты (PAR4/PAR5) — в лог, ошибки игнорируем."""
        try:
            self.log_event("PTS VER: " + self._client.read_version().replace("\n", " | "))
        except Exception as e:
            self.log_event(f"PTS VER: {e}")
        try:
            f_min = self._client.source.read_parameter_float(4)
            f_max = self._client.source.read_parameter_float(5)
            self.log_event(f"PTS источник: частота PAR4={f_min} … PAR5={f_max} Гц")
            if f_min is not None and f_max is not None and 0 < f_max <= 1000 and f_min >= 0:
                self._sp_f.setToolTip(
                    f"Допустимо по прибору: {f_min:g}…{f_max:g} Гц "
                    f"(0 = синхронизация с сетью)")
        except Exception as e:
            self.log_event(f"PTS PAR4/PAR5: {e}")

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
        self._set_status("○  Отключено", GREY)
        self.log_event("PTS отключён")

    # ═════════════════════════════════════════════════════════════════════
    #  Фоновый опрос
    # ═════════════════════════════════════════════════════════════════════
    def _poll_loop(self):
        while self.is_polling:
            try:
                data = self.read_device_values()
                for name, value in data.items():
                    self.value_updated.emit(name, value)
                if data:
                    QTimer.singleShot(0, lambda d=data: self.log_measurement(d))
                self._poll_status()
            except Exception as e:
                QTimer.singleShot(0, lambda err=str(e): self.log_event(f"Опрос PTS: {err}"))
            time.sleep(self.poll_interval)

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

    def _poll_status(self):
        """SE каждый цикл; STATI/STATU — только если SE показал ошибку/предупреждение."""
        client = self._client
        if not client:
            return
        st: dict = {}
        src, ref = client.source, client.reference
        try:
            se = src.read_extended_status()
            st["se"] = se
            if se and (se["error"] or se["warning"]):
                st["stati"] = src.read_amp_status("I")
                st["statu"] = src.read_amp_status("U")
        except Exception as e:
            st["se_err"] = str(e)
        try:
            st["roli"] = ref.read_overflow_current()
            st["rolu"] = ref.read_overflow_voltage()
        except Exception as e:
            st["rol_err"] = str(e)
        self.pts_state.emit(st)

    # ═════════════════════════════════════════════════════════════════════
    #  Статус / служебные слоты (выполняются в потоке GUI)
    # ═════════════════════════════════════════════════════════════════════
    def _set_status(self, text: str, color: str, bold: bool = False):
        """Безопасно из любого потока."""
        self.pts_status_text.emit(text, color, bold)

    def _on_status_text(self, text: str, color: str, bold: bool):
        weight = "font-weight:bold; " if bold else ""
        self._src_status_lbl.setText(text)
        self._src_status_lbl.setStyleSheet(f"color:{color}; {weight}font-size:9pt;")

    def _on_op_done(self, name: str, err: str, ok_text: str, ok_color: str):
        if err:
            self.log_event(f"PTS {name}: ОШИБКА — {err}")
            if "прерван" in err:            # SET прерван аварийной кнопкой
                return
            self._set_status(f"⚠  {name}: ошибка", RED)
            QMessageBox.critical(self, f"PTS — {name}", err)
        else:
            self.log_event(f"PTS {name}: выполнено")
            if ok_text:
                self._set_status(ok_text, ok_color)

    def _on_state(self, st: dict):
        lines = []
        alarm = ""
        se = st.get("se")
        if se:
            def yn(v, yes="ДА", no="нет"):
                return yes if v else no
            lines.append(f"Ошибка усилителя : {yn(se['error'])}")
            lines.append(f"Предупреждение   : {yn(se['warning'])}")
            lines.append(f"Прибор занят     : {yn(se['busy'])}")
            lines.append(f"Контакт дозир. U : {yn(se['dos_u'], 'разомкнут', 'замкнут')}")
            lines.append(f"Контакт дозир. I : {yn(se['dos_i'], 'разомкнут', 'замкнут')}")
            lines.append(f"Пакетное упр.    : {yn(se['packet'], 'активно', 'нет')}")
            lines.append(f"Ripple control   : {yn(se['ripple'], 'активен', 'нет')}")
            if se["error"]:
                alarm = "ОШИБКА усилителя (SE)"
            elif se["warning"]:
                alarm = "ПРЕДУПРЕЖДЕНИЕ: перегрузка (SE)"
        elif "se_err" in st:
            lines.append(f"SE: {st['se_err']}")

        text_map = self._client.source.AMP_STATUS_TEXT if self._client else {}
        for key, title in (("stati", "Усилители I"), ("statu", "Усилители U")):
            vals = st.get(key)
            if vals:
                lines.append(f"{title}: " + "  ".join(
                    f"L{k}={text_map.get(v, '?') if v is not None else '?'}"
                    for k, v in enumerate(vals, 1)))
                if any(v in (2, 3, 4) for v in vals if v is not None):
                    alarm = alarm or f"{title}: перегрузка/отключение"

        for key, title in (("roli", "Перегрузка эталона по I"),
                           ("rolu", "Перегрузка эталона по U")):
            vals = st.get(key)
            if vals:
                lines.append(f"{title}: " + " ".join(
                    f"L{k}={'ДА' if v else 'нет'}" for k, v in enumerate(vals, 1)))
                if any(vals):
                    alarm = alarm or f"{title}!"
        if "rol_err" in st:
            lines.append(f"ROLI/ROLU: {st['rol_err']}")

        self._lbl_state.setText("\n".join(lines) if lines else "Нет данных")

        if alarm and alarm != self._last_alarm:
            self._set_status(f"⚠  {alarm}", RED, True)
            self.log_event(f"PTS: {alarm}")
        self._last_alarm = alarm

    def _on_status_refresh(self):
        if not self._client:
            return
        threading.Thread(target=self._poll_status, daemon=True).start()

    # ═════════════════════════════════════════════════════════════════════
    #  Запуск операций в фоне
    # ═════════════════════════════════════════════════════════════════════
    def _async(self, name: str, fn, ok_text: str = "", ok_color: str = GREY):
        """Выполнить fn() в потоке; по окончанию сигнал pts_op_done."""
        def runner():
            err = ""
            try:
                with self._op_lock:
                    fn()
            except Exception as e:                 # noqa: BLE001
                err = str(e) or e.__class__.__name__
            self.pts_op_done.emit(name, err, ok_text, ok_color)
        threading.Thread(target=runner, daemon=True, name=f"PTS-{name}").start()

    def _log_command(self, title: str, getter):
        if not self._client:
            return
        client = self._client

        def work():
            self.log_event(f"PTS {title}: " + str(getter(client)).replace("\n", " | "))
        self._async(title, work)

    # ═════════════════════════════════════════════════════════════════════
    #  Опции (кэш для потоков)
    # ═════════════════════════════════════════════════════════════════════
    def _watch(self, *widgets):
        for wd in widgets:
            if isinstance(wd, (QDoubleSpinBox, QSpinBox)):
                wd.valueChanged.connect(self._refresh_opts)
            elif isinstance(wd, QComboBox):
                wd.currentIndexChanged.connect(self._refresh_opts)
            elif isinstance(wd, QCheckBox):
                wd.toggled.connect(self._refresh_opts)

    def _refresh_opts(self, *_):
        self._opts_cache = self._collect_opts()

    def _get_opts(self) -> dict:
        return self._opts_cache or self._collect_opts()

    def _collect_opts(self) -> dict:
        """Снимок опций из виджетов. Вызывать только из потока GUI."""
        return {
            "ramp": ((self._sp_ramp_i.value(), self._sp_ramp_u.value())
                     if self._chk_ramp.isChecked() else None),
            "relay": {0: None, 1: False, 2: True}[self._cmb_relay.currentIndex()],
            "wire3": {0: None, 1: False, 2: True}[self._cmb_wire.currentIndex()],
            "bi": self._spi_bi.value(),
            "bu": self._spi_bu.value(),
            "abs_phase": ((self._sp_ph_a.value(), self._sp_ph_b.value(),
                           self._sp_ph_c.value())
                          if self._chk_ph.isChecked() else None),
            "lim_u": self._sp_lim_u.value(),
            "lim_i": self._sp_lim_i.value(),
            "lim_s": self._sp_lim_s.value(),
            "confirm": self._chk_confirm.isChecked(),
        }

    @staticmethod
    def _limits_to_src(src, opts: dict):
        src.max_voltage = opts["lim_u"]
        src.max_current = opts["lim_i"]
        src.max_va_per_phase = opts["lim_s"] or None

    def _apply_limits_to_client(self, opts: dict):
        if self._client:
            self._limits_to_src(self._client.source, opts)

    def _on_limits_changed(self, *_):
        lu, li = self._sp_lim_u.value(), self._sp_lim_i.value()
        for sp in (self._sp_ua, self._sp_ub, self._sp_uc):
            sp.setMaximum(lu)
        for sp in (self._sp_ia, self._sp_ib, self._sp_ic):
            sp.setMaximum(li)

    def _apply_source_options(self, src, opts: dict):
        """Отправить на источник опции вкладки "Источник" и PH (до SET)."""
        self._limits_to_src(src, opts)

        if opts["ramp"] is not None and opts["ramp"] != self._sent["ramp"]:
            src.set_ramps(*opts["ramp"])
            self._sent["ramp"] = opts["ramp"]

        if opts["wire3"] is not None and opts["wire3"] != self._sent["wire"]:
            src.set_wire_mode(opts["wire3"])
            self._sent["wire"] = opts["wire3"]

        # реле шлём при каждом SET: в низких диапазонах прибор размыкает его сам
        if opts["relay"] is not None:
            src.set_current_relay(opts["relay"])

        if opts["bi"] != self._sent["bi"]:
            for ph in (1, 2, 3):
                src.set_current_range(ph, opts["bi"])
            self._sent["bi"] = opts["bi"]
        if opts["bu"] != self._sent["bu"]:
            for ph in (1, 2, 3):
                src.set_voltage_range(ph, opts["bu"])
            self._sent["bu"] = opts["bu"]

        if opts["abs_phase"] is not None:
            src.set_abs_phase(*opts["abs_phase"])

    # ═════════════════════════════════════════════════════════════════════
    #  Старт / Стоп / Аварийно
    # ═════════════════════════════════════════════════════════════════════
    def _collect_setpoints(self) -> dict:
        return {
            "u":   (self._sp_ua.value(), self._sp_ub.value(), self._sp_uc.value()),
            "i":   (self._sp_ia.value(), self._sp_ib.value(), self._sp_ic.value()),
            "phi": (self._sp_pa.value(), self._sp_pb.value(), self._sp_pc.value()),
            "f":   self._sp_f.value(),
        }

    def _confirm_heavy(self, sp: dict, opts: dict) -> bool:
        if not opts["confirm"]:
            return True
        u_max, i_max = max(sp["u"]), max(sp["i"])
        if u_max < CONFIRM_VOLTAGE and i_max < CONFIRM_CURRENT:
            return True
        s_max = max(u * i for u, i in zip(sp["u"], sp["i"]))
        answer = QMessageBox.question(
            self, "PTS — подтверждение",
            f"Будет включена мощная нагрузка:\n\n"
            f"  U макс = {u_max:.1f} В\n"
            f"  I макс = {i_max:.2f} А\n"
            f"  S макс на фазу = {s_max:.0f} ВА\n\n"
            f"Проверьте схему подключения. Включить?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        return answer == QMessageBox.Yes

    def _on_start(self):
        if not self._client:
            return
        sp = self._collect_setpoints()
        opts = self._collect_opts()
        self._opts_cache = opts
        if not self._confirm_heavy(sp, opts):
            self.log_event("Старт отменён пользователем")
            return
        client = self._client
        self._set_status("…  Применяю уставки (SET)", YELLOW)
        self.log_event(
            f"SET: Ua={sp['u'][0]:.1f} В  Ia={sp['i'][0]:.4f} А  "
            f"φa={sp['phi'][0]:.2f}°  f={sp['f']:.2f} Гц")

        def work():
            src = client.source
            self._apply_source_options(src, opts)
            src.apply_loadpoint(u=sp["u"], i=sp["i"], phi=sp["phi"], f=sp["f"])

        self._async("Старт", work, "●  Выход включён", GREEN)

    def _on_stop(self):
        if not self._client:
            return
        client = self._client
        self._async("Стоп", lambda: client.source.off(mode=0),
                    "○  Выход выключен (плавно)", YELLOW)

    def _on_emergency(self):
        if not self._client:
            return
        client = self._client
        self._set_status("⚠  АВАРИЙНОЕ ОТКЛЮЧЕНИЕ", RED, True)
        self.log_event("АВАРИЙНО: OFF1")
        client.source.abort()       # сразу освободить поток, ждущий BSY после SET

        def runner():
            err = ""
            try:
                client.source.emergency_off()
            except Exception as e:             # noqa: BLE001
                err = str(e)
            self.pts_op_done.emit("Аварийное отключение", err, "", GREY)
        threading.Thread(target=runner, daemon=True, name="PTS-EMG").start()

    # ═════════════════════════════════════════════════════════════════════
    #  Гармоники
    # ═════════════════════════════════════════════════════════════════════
    def _harm_target(self):
        kind = "U" if self._cmb_h_kind.currentIndex() == 0 else "I"
        idx = self._cmb_h_phase.currentIndex()
        phases = [1, 2, 3] if idx == 3 else [idx + 1]
        return kind, phases

    def _on_harm_add(self):
        if not self._client:
            return
        kind, phases = self._harm_target()
        order = self._spi_h_order.value()
        amp, phi = self._sp_h_amp.value(), self._sp_h_phi.value()
        client = self._client

        def work():
            for ph in phases:
                client.source.set_harmonic(kind, ph, order, amp, phi)

        for ph in phases:
            self._harm_defs.setdefault((kind, ph), {})[order] = (amp, phi)
        self._refresh_harm_label()
        self._async(f"Гармоника OW{kind} №{order}", work)

    def _on_harm_clear(self):
        if not self._client:
            return
        kind, phases = self._harm_target()
        client = self._client

        def work():
            for ph in phases:
                client.source.clear_harmonics(kind, ph)

        for ph in phases:
            self._harm_defs.pop((kind, ph), None)
        self._refresh_harm_label()
        self._async(f"Очистка гармоник OW{kind}", work)

    def _refresh_harm_label(self):
        if not self._harm_defs:
            self._lbl_harm.setText("Гармоники не заданы")
            return
        lines = []
        for (kind, ph), orders in sorted(self._harm_defs.items()):
            items = ", ".join(f"{o}: {a:g}%/{p:g}°" for o, (a, p) in sorted(orders.items()))
            lines.append(f"{kind}{ph}: {items}")
        self._lbl_harm.setText("Заданы (по данным панели):\n" + "\n".join(lines))

    # ═════════════════════════════════════════════════════════════════════
    #  Настройки эталона
    # ═════════════════════════════════════════════════════════════════════
    def _on_ref_apply(self):
        if not self._client:
            return
        client = self._client
        manual = self._cmb_ref_range.currentIndex() == 1
        ri, ru = self._spi_ref_ri.value(), self._spi_ref_ru.value()
        meas = self._cmb_ref_meas.currentText()
        db_i, dbu_i = self._cmb_ref_db.currentIndex(), self._cmb_ref_dbu.currentIndex()
        tri = {k: cb.currentIndex() for k, cb in (
            ("yi", self._cmb_yi), ("yu", self._cmb_yu),
            ("ial", self._cmb_ial), ("ual", self._cmb_ual))}
        tb = self._sp_timebase.value()

        def work():
            ref = client.reference
            if db_i > 0:
                ref.set_current_input(db_i - 1)
            if dbu_i > 0:
                ref.set_voltage_input(dbu_i - 1)
            if meas != "Не менять":
                ref.set_measure_mode(meas)
            ref.set_timebase(tb)
            if tri["yi"]:
                ref.set_current_sync(tri["yi"] == 1)
            if tri["yu"]:
                ref.set_voltage_sync(tri["yu"] == 1)
            if tri["ial"]:
                ref.set_current_range_limit(tri["ial"] == 1)
            if tri["ual"]:
                ref.set_voltage_range_limit(tri["ual"] == 1)
            if manual:
                ref.set_current_range(ri)
                ref.set_voltage_range(ru)
                ref.set_manual_range()
            else:
                ref.set_auto_range()

        self._async("Настройки эталона", work)

    # ═════════════════════════════════════════════════════════════════════
    #  API для автотестов (вызывается PTSGenerator из другого потока)
    # ═════════════════════════════════════════════════════════════════════
    def apply_settings(self, setpoints: dict):
        if not self._client:
            raise RuntimeError("PTSPanel: не подключён")
        src = self._client.source
        opts = self._get_opts()          # кэш — виджеты из чужого потока не трогаем

        u   = triple_from(setpoints.get("Ua"), setpoints.get("Ub"), setpoints.get("Uc"))
        i   = triple_from(setpoints.get("Ia"), setpoints.get("Ib"), setpoints.get("Ic"))
        phi = triple_from(setpoints.get("phi_a"), setpoints.get("phi_b"),
                          setpoints.get("phi_c"))
        f   = setpoints.get("f")

        with self._op_lock:
            self._apply_source_options(src, opts)
            src.apply_loadpoint(u=u, i=i, phi=phi, f=f)
        self._set_status("●  Выход включён (авто-тест)", GREEN)
        self.log_event(f"apply_settings: {setpoints}")

    def output_off(self):
        if self._client:
            try:
                self._client.source.off(mode=1)
                self._set_status("○  Выход выключен (авто-тест)", GREY)
                self.log_event("output_off: OFF1")
            except Exception:
                pass

    # ═════════════════════════════════════════════════════════════════════
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

    # ═════════════════════════════════════════════════════════════════════
    #  Конфигурация
    # ═════════════════════════════════════════════════════════════════════
    _CFG_SPINS = (
        ("Ua", "_sp_ua"), ("Ub", "_sp_ub"), ("Uc", "_sp_uc"),
        ("Ia", "_sp_ia"), ("Ib", "_sp_ib"), ("Ic", "_sp_ic"),
        ("phi_a", "_sp_pa"), ("phi_b", "_sp_pb"), ("phi_c", "_sp_pc"),
        ("f", "_sp_f"),
    )

    def get_config(self) -> dict:
        cfg = super().get_config()
        cfg.update({
            "port":      self._port_edit.text().strip(),
            "baudrate":  int(self._baud_combo.currentText()),
            "timeout":   self._timeout_edit.text().strip(),
            "symmetric": self._sym_chk.isChecked(),
            "setpoints": {key: getattr(self, attr).value()
                          for key, attr in self._CFG_SPINS},
            "options": {
                "lim_u": self._sp_lim_u.value(),
                "lim_i": self._sp_lim_i.value(),
                "lim_s": self._sp_lim_s.value(),
                "confirm": self._chk_confirm.isChecked(),
                "ramp_on": self._chk_ramp.isChecked(),
                "ramp_i": self._sp_ramp_i.value(),
                "ramp_u": self._sp_ramp_u.value(),
                "relay": self._cmb_relay.currentIndex(),
                "wire": self._cmb_wire.currentIndex(),
                "bi": self._spi_bi.value(),
                "bu": self._spi_bu.value(),
                "ph_on": self._chk_ph.isChecked(),
                "ph": [self._sp_ph_a.value(), self._sp_ph_b.value(),
                       self._sp_ph_c.value()],
                "timebase": self._sp_timebase.value(),
            },
        })
        return cfg

    def apply_config(self, config: dict):
        super().apply_config(config)
        if not hasattr(self, "_port_edit") or not hasattr(self, "_chk_confirm"):
            return
        self._port_edit.setText(config.get("port", "COM1"))
        self._baud_combo.setCurrentText(str(config.get("baudrate", 19200)))
        self._timeout_edit.setText(str(config.get("timeout", "2.0")))
        self._sym_chk.setChecked(config.get("symmetric", True))

        # сначала пределы — они задают максимумы спинбоксов уставок
        o = config.get("options", {})
        self._sp_lim_u.setValue(float(o.get("lim_u", DEFAULT_MAX_VOLTAGE)))
        self._sp_lim_i.setValue(float(o.get("lim_i", DEFAULT_MAX_CURRENT)))
        self._sp_lim_s.setValue(float(o.get("lim_s", 0.0)))
        self._chk_confirm.setChecked(bool(o.get("confirm", True)))
        self._chk_ramp.setChecked(bool(o.get("ramp_on", False)))
        self._sp_ramp_i.setValue(float(o.get("ramp_i", 0.0)))
        self._sp_ramp_u.setValue(float(o.get("ramp_u", 0.0)))
        self._cmb_relay.setCurrentIndex(int(o.get("relay", 0)))
        self._cmb_wire.setCurrentIndex(int(o.get("wire", 0)))
        self._spi_bi.setValue(int(o.get("bi", 0)))
        self._spi_bu.setValue(int(o.get("bu", 0)))
        self._chk_ph.setChecked(bool(o.get("ph_on", False)))
        ph = o.get("ph", [0.0, 240.0, 120.0])
        if len(ph) == 3:
            self._sp_ph_a.setValue(float(ph[0]))
            self._sp_ph_b.setValue(float(ph[1]))
            self._sp_ph_c.setValue(float(ph[2]))
        self._sp_timebase.setValue(float(o.get("timebase", 1.0)))
        self._on_limits_changed()

        sp = config.get("setpoints", {})
        for key, attr in self._CFG_SPINS:
            if key in sp:
                getattr(self, attr).setValue(float(sp[key]))
        self._refresh_opts()

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


def _tri_combo() -> QComboBox:
    cb = QComboBox()
    cb.addItems(["Не менять", "Включить", "Выключить"])
    return cb


def _sep() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.HLine)
    f.setStyleSheet("color:#30363d;")
    return f
