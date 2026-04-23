"""
panels/pts_panel.py  v1.1.0  (23.04.2026)

Исправления v1.1.0:
  - Исправлены импорты после реструктуризации проекта:
      base_device_panel → panels.base_device_panel
      pts_driver        → drivers.pts_driver
  - Убран устаревший sys.path.insert хак (больше не нужен)

Панель генератора/эталона MTE/EMH PTSx.xC.

PTS — одновременно источник питания (source) и эталонный измеритель (reference).
Панель подключается один раз. PTSGenerator берёт клиент отсюда через pts_client.

Параметры эталона: Ua/Ub/Uc, Ia/Ib/Ic, Pa/Pb/Pc, Qa/Qb/Qc, phi_a/b/c, f
"""

from typing import Optional

from PyQt5.QtWidgets import QLineEdit, QComboBox

from panels.base_device_panel import BaseDevicePanel
from drivers.pts_driver import PTSx_Client


class PTSPanel(BaseDevicePanel):

    PARAM_DEFS = [
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
    ]

    def __init__(self, device_name: str, parent=None):
        super().__init__("PTS", device_name, parent)
        self._client: Optional[PTSx_Client] = None

        for name, unit, label in self.PARAM_DEFS:
            self.parameters[name] = {"unit": unit, "label": label}

        self.poll_interval = 1.0
        self._build_specific_ui()
        self._rebuild_values_table()

    @property
    def pts_client(self) -> Optional[PTSx_Client]:
        """PTSGenerator использует этот клиент чтобы не открывать порт повторно."""
        return self._client

    def _build_specific_ui(self):
        self._port_edit = QLineEdit("COM1")
        self._port_edit.setPlaceholderText("Например: COM3")
        self.add_setting_row("COM-порт:", self._port_edit)

        self._baud_combo = QComboBox()
        self._baud_combo.addItems(["9600", "19200", "38400", "57600", "115200"])
        self._baud_combo.setCurrentText("19200")
        self.add_setting_row("Скорость:", self._baud_combo)

        self._timeout_edit = QLineEdit("2.0")
        self.add_setting_row("Таймаут (с):", self._timeout_edit)

    def connect_device(self) -> bool:
        port = self._port_edit.text().strip()
        baud = int(self._baud_combo.currentText())
        try:
            timeout = float(self._timeout_edit.text().strip())
        except ValueError:
            timeout = 2.0
        try:
            self._client = PTSx_Client(port, baud, timeout)
            self._client.mode1()
            self._client.reference.enable_all_results()
            self._client.reference.set_timebase(1)
            self.log_event(f"PTS подключён: {port} @ {baud}")
            return True
        except Exception as e:
            self._client = None
            self.log_event(f"Ошибка подключения PTS: {e}")
            return False

    def disconnect_device(self):
        if self._client:
            try:
                self._client.source.off()
            except Exception:
                pass
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
        self.log_event("PTS отключён")

    def read_device_values(self) -> dict:
        if not self._client:
            return {}
        ref = self._client.reference
        values: dict = {}

        def safe(fn, keys):
            try:
                for k, v in zip(keys, fn()):
                    if v is not None:
                        values[k] = float(v)
            except Exception:
                pass

        safe(ref.read_voltages,              ["Ua", "Ub", "Uc"])
        safe(ref.read_currents,              ["Ia", "Ib", "Ic"])
        safe(ref.read_active_power_phases,   ["Pa", "Pb", "Pc"])
        safe(ref.read_reactive_power_phases, ["Qa", "Qb", "Qc"])
        safe(ref.read_phase_angles, ["phi_a", "phi_b", "phi_c"])
        try:
            f = ref.read_frequency()
            if f is not None:
                values["f"] = float(f)
        except Exception:
            pass

        return values

    def get_config(self) -> dict:
        cfg = super().get_config()
        cfg.update({
            "port":     self._port_edit.text().strip(),
            "baudrate": int(self._baud_combo.currentText()),
            "timeout":  self._timeout_edit.text().strip(),
        })
        return cfg

    def apply_config(self, config: dict):
        super().apply_config(config)
        if hasattr(self, "_port_edit"):
            self._port_edit.setText(config.get("port", "COM1"))
            self._baud_combo.setCurrentText(str(config.get("baudrate", 19200)))
            self._timeout_edit.setText(str(config.get("timeout", "2.0")))
