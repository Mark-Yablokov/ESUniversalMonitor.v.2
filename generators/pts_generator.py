"""
Автор: m.yablokov
generators/pts_generator.py  v2.0.0

Изменения v2.0.0:
  - Если найдена подключённая PTSPanel, точка нагрузки применяется через
    panel.apply_settings(): так учитываются опции панели (рампы, реле SKLI,
    диапазоны, PH, лимиты 300 В / 120 А).
  - Своё подключение: лимиты (max_voltage / max_current / max_va_per_phase)
    задаются в конфиге и проверяются драйвером до отправки команд.
  - Сборка точки нагрузки — через apply_loadpoint() драйвера (один SET).
  - emergency_off(): мгновенное отключение OFF1 с прерыванием ожидания SET.

PTSGenerator ищет PTSPanel в device_panels и использует её уже открытый клиент.
Если PTSPanel не найдена или не подключена — создаёт своё подключение.
"""

from typing import Dict, List, Optional
from generators.base_generator import BaseGenerator
from drivers.pts_driver import (
    PTSx_Client, triple_from, DEFAULT_MAX_VOLTAGE, DEFAULT_MAX_CURRENT,
)


class PTSGenerator(BaseGenerator):

    GENERATOR_TYPE = "PTS (RS232)"
    CHANNELS = ["Ua", "Ub", "Uc", "Ia", "Ib", "Ic", "phi_a", "phi_b", "phi_c", "f"]

    def __init__(self, port: str = "COM1", baudrate: int = 19200, timeout: float = 2.0,
                 max_voltage: float = DEFAULT_MAX_VOLTAGE,
                 max_current: float = DEFAULT_MAX_CURRENT,
                 max_va_per_phase: float = 0.0):
        self._port             = port
        self._baudrate         = baudrate
        self._timeout          = timeout
        self._max_voltage      = max_voltage
        self._max_current      = max_current
        self._max_va_per_phase = max_va_per_phase   # 0 = не проверять
        self._client: Optional[PTSx_Client] = None
        self._panel = None                  # PTSPanel, если взяли её клиент
        self._own_connection = False        # True = мы сами открыли, мы сами закроем

    @property
    def is_connected(self) -> bool:
        return self._client is not None

    @property
    def channel_names(self) -> List[str]:
        return self.CHANNELS

    def connect(self, device_panels: list = None) -> bool:
        """
        Попытаться найти уже подключённую PTSPanel и взять её клиент.
        Если не найдена — открыть своё подключение.
        """
        if device_panels:
            for panel in device_panels:
                if (getattr(panel, "device_type", "") == "PTS"
                        and hasattr(panel, "pts_client")
                        and panel.pts_client is not None):
                    self._client = panel.pts_client
                    self._panel = panel
                    self._own_connection = False
                    return True

        # Своё подключение
        try:
            self._client = PTSx_Client(self._port, self._baudrate, self._timeout)
            self._client.mode1()
            self._client.set_limits(self._max_voltage, self._max_current,
                                    self._max_va_per_phase or None)
            self._panel = None
            self._own_connection = True
            return True
        except Exception as e:
            self._client = None
            raise ConnectionError(f"PTS [{self._port}]: {e}") from e

    def disconnect(self):
        if self._own_connection and self._client:
            try:
                self._client.source.off()
            except Exception:
                pass
            try:
                self._client.close()
            except Exception:
                pass
        self._client = None
        self._panel = None
        self._own_connection = False

    def set_point(self, setpoints: Dict[str, float]):
        if not self._client:
            raise RuntimeError("PTSGenerator: не подключён")

        # Есть панель — она применит и свои опции (рампы, SKLI, диапазоны, PH...)
        if self._panel is not None:
            self._panel.apply_settings(setpoints)
            return

        u = triple_from(setpoints.get("Ua"), setpoints.get("Ub"), setpoints.get("Uc"))
        i = triple_from(setpoints.get("Ia"), setpoints.get("Ib"), setpoints.get("Ic"))
        phi = triple_from(setpoints.get("phi_a"), setpoints.get("phi_b"),
                          setpoints.get("phi_c"))
        f = setpoints.get("f")

        if u is None and i is None and phi is None and f is None:
            return      # нечего применять
        # пределы проверяются внутри; при нарушении — ValueError, прибор не тронут
        self._client.source.apply_loadpoint(u=u, i=i, phi=phi, f=f)

    def output_off(self):
        if self._panel is not None:
            self._panel.output_off()
        elif self._client:
            self._client.source.off()

    def emergency_off(self):
        """Мгновенное отключение (OFF1), прерывает ожидание SET."""
        if self._client:
            self._client.source.emergency_off()

    def get_config(self) -> dict:
        return {
            "type":             self.GENERATOR_TYPE,
            "port":             self._port,
            "baudrate":         self._baudrate,
            "timeout":          self._timeout,
            "max_voltage":      self._max_voltage,
            "max_current":      self._max_current,
            "max_va_per_phase": self._max_va_per_phase,
        }

    def apply_config(self, config: dict):
        self._port             = config.get("port", "COM1")
        self._baudrate         = int(config.get("baudrate", 19200))
        self._timeout          = float(config.get("timeout", 2.0))
        self._max_voltage      = float(config.get("max_voltage", DEFAULT_MAX_VOLTAGE))
        self._max_current      = float(config.get("max_current", DEFAULT_MAX_CURRENT))
        self._max_va_per_phase = float(config.get("max_va_per_phase", 0.0))
