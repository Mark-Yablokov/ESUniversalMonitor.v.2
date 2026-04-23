"""
generators/pts_generator.py  v1.2.0

Исправления v1.2.0:
  - from pts_driver → from drivers.pts_driver (после реструктуризации)
  - Убран устаревший sys.path.insert хак

PTSGenerator ищет PTSPanel в device_panels и использует её уже открытый клиент.
Если PTSPanel не найдена или не подключена — создаёт своё подключение.
"""

from typing import Dict, List, Optional
from generators.base_generator import BaseGenerator
from drivers.pts_driver import PTSx_Client


class PTSGenerator(BaseGenerator):

    GENERATOR_TYPE = "PTS (RS232)"
    CHANNELS = ["Ua", "Ub", "Uc", "Ia", "Ib", "Ic", "phi_a", "phi_b", "phi_c", "f"]

    def __init__(self, port: str = "COM1", baudrate: int = 19200, timeout: float = 2.0):
        self._port      = port
        self._baudrate  = baudrate
        self._timeout   = timeout
        self._client: Optional[PTSx_Client] = None
        self._own_connection = False   # True = мы сами открыли, мы сами закроем

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
        # Ищем PTSPanel среди панелей устройств
        if device_panels:
            for panel in device_panels:
                if (getattr(panel, "device_type", "") == "PTS"
                        and hasattr(panel, "pts_client")
                        and panel.pts_client is not None):
                    self._client = panel.pts_client
                    self._own_connection = False
                    return True

        # Своё подключение
        try:
            self._client = PTSx_Client(self._port, self._baudrate, self._timeout)
            self._client.mode1()
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
        self._own_connection = False

    def set_point(self, setpoints: Dict[str, float]):
        if not self._client:
            raise RuntimeError("PTSGenerator: не подключён")

        src = self._client.source
        changed = False

        ua, ub, uc = setpoints.get("Ua"), setpoints.get("Ub"), setpoints.get("Uc")
        if any(v is not None for v in (ua, ub, uc)):
            ua = ua if ua is not None else 0.0
            ub = ub if ub is not None else ua
            uc = uc if uc is not None else ua
            src.set_voltage(ua, ub, uc)
            changed = True

        ia, ib, ic = setpoints.get("Ia"), setpoints.get("Ib"), setpoints.get("Ic")
        if any(v is not None for v in (ia, ib, ic)):
            ia = ia if ia is not None else 0.0
            ib = ib if ib is not None else ia
            ic = ic if ic is not None else ia
            src.set_current(ia, ib, ic)
            changed = True

        pa = setpoints.get("phi_a")
        pb = setpoints.get("phi_b")
        pc = setpoints.get("phi_c")
        if any(v is not None for v in (pa, pb, pc)):
            pa = pa if pa is not None else 0.0
            pb = pb if pb is not None else pa
            pc = pc if pc is not None else pa
            src.set_angle(pa, pb, pc)
            changed = True

        f = setpoints.get("f")
        if f is not None:
            src.set_frequency(f)
            changed = True

        if changed:
            src.on()

    def output_off(self):
        if self._client:
            self._client.source.off()

    def get_config(self) -> dict:
        return {
            "type":     self.GENERATOR_TYPE,
            "port":     self._port,
            "baudrate": self._baudrate,
            "timeout":  self._timeout,
        }

    def apply_config(self, config: dict):
        self._port     = config.get("port", "COM1")
        self._baudrate = int(config.get("baudrate", 19200))
        self._timeout  = float(config.get("timeout", 2.0))
