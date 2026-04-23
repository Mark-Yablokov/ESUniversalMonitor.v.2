# Автор: m.yablokov
# generators/mantigora_generator.py — Генератор НТ-2000Р v1.0.0
#
# Принцип (аналогичен PTSGenerator):
#   connect(device_panels) → ищет MantigoraPanel среди добавленных вкладок
#   Если найдена и подключена → использует её клиент (own_connection=False)
#   Если не найдена → пробует создать своё подключение
#
# MantigoraPanel должна предоставлять:
#   panel.device_type       → "Mantigora HT"
#   panel.is_hw_connected   → bool
#   panel.set_voltage(v)    → задать уставку (В)
#   panel.output_on()       → включить ВВ
#   panel.output_off()      → выключить ВВ

from typing import Dict, List, Optional
from generators.base_generator import BaseGenerator


class MantigoraGenerator(BaseGenerator):
    """
    Генератор для НТ-2000Р (Mantigora HT).

    Каналы:
        U_output  — выходное напряжение, В

    Использование:
        gen = MantigoraGenerator()
        gen.apply_config({"type": "Mantigora HT"})
        gen.connect(device_panels)   # передаём список панелей из MainWindow
        gen.set_point({"U_output": 1000.0})
        gen.output_off()
        gen.disconnect()
    """

    CHANNELS: List[str] = ["U_output"]
    DEVICE_TYPE: str = "Mantigora HT"

    def __init__(self):
        self._panel = None        # MantigoraPanel
        self._own_connection = False
        self._connected = False
        self._config: dict = {"type": self.DEVICE_TYPE}

    # ── BaseGenerator interface ───────────────────────────────────────────────

    @property
    def channel_names(self) -> List[str]:
        return self.CHANNELS

    @property
    def is_connected(self) -> bool:
        return self._connected

    def connect(self, device_panels=None) -> bool:
        """
        Ищет MantigoraPanel в device_panels.
        Если панель найдена и подключена — берёт её соединение.
        Возвращает True при успехе.
        """
        if device_panels:
            for panel in device_panels:
                # Проверяем тип панели
                dtype = getattr(panel, "device_type", None)
                if dtype != self.DEVICE_TYPE:
                    continue

                # Проверяем что панель подключена к железу
                hw_ok = (
                    getattr(panel, "is_hw_connected", None)
                    or getattr(panel, "is_connected", None)
                )
                if not hw_ok:
                    continue

                # Проверяем что панель умеет управлять напряжением
                if not hasattr(panel, "set_voltage"):
                    continue

                self._panel = panel
                self._own_connection = False
                self._connected = True
                return True

        # Панель не найдена — MantigoraPanel нужна чтобы управлять DLL
        # Самостоятельное соединение без панели не поддерживается
        return False

    def disconnect(self):
        """Не закрывает соединение если оно принадлежит MantigoraPanel."""
        if self._own_connection and self._panel is not None:
            try:
                self._panel.output_off()
                self._panel.disconnect_device()
            except Exception:
                pass

        self._panel = None
        self._own_connection = False
        self._connected = False

    def set_point(self, setpoints: Dict[str, float]):
        """
        Устанавливает уставку напряжения и включает выход.

        setpoints:
            {"U_output": 1000.0}   — напряжение в Вольтах
        """
        if not self._connected or self._panel is None:
            raise RuntimeError("MantigoraGenerator: нет подключения")

        if "U_output" in setpoints:
            voltage = float(setpoints["U_output"])
            self._panel.set_voltage(voltage)   # MantigoraPanel.set_voltage()
            self._panel.output_on()            # включить ВВ

    def output_off(self):
        """Выключить высоковольтный выход."""
        if self._panel is not None:
            try:
                self._panel.output_off()
            except Exception:
                pass

    # ── Config ───────────────────────────────────────────────────────────────

    def get_config(self) -> dict:
        return dict(self._config)

    def apply_config(self, config: dict):
        self._config.update(config)
