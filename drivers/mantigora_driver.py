"""
mantigora_driver.py
Драйвер высоковольтного источника питания серии HT (Mantigora).

Протокол обмена — простой бинарный через USB (FTDI виртуальный COM-порт).
Документация: «Описание протокола обмена приборов серии HV и HT».

Зависимости:
    pip install pyserial

Протокол (все значения — сырые байты):
    [0x01] + 2 байта U_code (LE) + 2 байта I_code (LE)  → установить уставки
    [0x02]                                                → включить / обновить выход
    [0x03]                                                → выключить выход (сброс в 0)
    [0x05]                                                → запросить измерение
        Ответ 5 байт: [I_low][I_high][U_low][U_high][0x0D]

Параметры порта: 9600 бод, 8 бит, чётная чётность (Even), 1 стоп-бит.

Коэффициенты пересчёта (КОД = значение × K):
    Напряжение:  КОД = U(В)   × KV
    Ток:         КОД = I(мкА) × KI

    Модель   KV      KI_6W    KI_15W   KI_60W
    2  кВ    32      21.33    8.533    2.133
    6  кВ    10.67   64       25.6     6.4
    10 кВ    6.4     106      42.4     10.6
    20 кВ    3.2     213.3    85.32    21.33
    30 кВ    2.133   320      128      32

v2.1.0 — исправлен порядок байт (little-endian) и чётность порта (Even) (2026-04-22)
v2.1.1 — connect() теперь возвращает True при успешном открытии порта (2026-04-23)
"""

import struct
import threading
import time
from typing import Optional, Tuple

try:
    import serial
    import serial.tools.list_ports
    _SERIAL_AVAILABLE = True
except ImportError:
    _SERIAL_AVAILABLE = False


# ---------------------------------------------------------------------------
# Коэффициенты пересчёта
# ---------------------------------------------------------------------------

# KV: напряжение (В) → код
KV = {2: 32.0, 6: 10.67, 10: 6.4, 20: 3.2, 30: 2.133}

# KI: ток (мкА) → код, по мощности модуля
KI = {
    6:  {2: 21.33, 6: 64.0,  10: 106.0, 20: 213.3, 30: 320.0},
    15: {2: 8.533, 6: 25.6,  10: 42.4,  20: 85.32, 30: 128.0},
    60: {2: 2.133, 6: 6.4,   10: 10.6,  20: 21.33, 30: 32.0},
}

# Максимальный ток (мА) по модели из паспорта
MAX_CURRENT_MA = {
    (6,  2):  3.0,
    (15, 2):  7.5,   (15, 6):  2.5,  (15, 10): 1.5,  (15, 20): 0.75, (15, 30): 0.5,
    (60, 2):  30.0,  (60, 6):  10.0, (60, 10): 6.0,  (60, 20): 3.0,  (60, 30): 2.0,
}


def list_com_ports() -> list:
    """Вернуть список доступных COM-портов."""
    if not _SERIAL_AVAILABLE:
        return []
    return [p.device for p in serial.tools.list_ports.comports()]


# ---------------------------------------------------------------------------
# Класс драйвера
# ---------------------------------------------------------------------------

class MantigoraDriverError(Exception):
    """Базовый класс исключений драйвера."""


class MantigoraDriver:
    """
    Драйвер высоковольтного источника питания серии HT (Mantigora).

    Пример использования:
        drv = MantigoraDriver(port="COM1", voltage_kv=2, power_w=6)
        drv.connect()
        drv.set_voltage(500)           # 500 В
        drv.set_current_limit(1.0)     # 1.0 мА
        drv.start()
        u, i = drv.read_measurement()  # В и мА
        drv.stop()
        drv.disconnect()
    """

    # Коды команд
    CMD_SET     = 0x01   # установить уставки
    CMD_START   = 0x02   # включить/обновить выход
    CMD_STOP    = 0x03   # выключить выход
    CMD_MEASURE = 0x05   # измерить U и I

    END_BYTE    = 0x0D   # конец пакета ответа (Enter)

    def __init__(
        self,
        port:       str = "COM1",
        baudrate:   int = 9600,
        voltage_kv: int = 2,
        power_w:    int = 6,
    ):
        """
        Parameters
        ----------
        port : str
            COM-порт, например "COM1".
        baudrate : int
            Скорость порта. По умолчанию 9600.
        voltage_kv : int
            Максимальное напряжение модуля в кВ (2, 6, 10, 20, 30).
        power_w : int
            Мощность модуля в Вт (6, 15, 60).
        """
        if not _SERIAL_AVAILABLE:
            raise MantigoraDriverError(
                "pyserial не установлен. Выполните: pip install pyserial"
            )

        self.port       = port
        self.baudrate   = baudrate
        self.voltage_kv = voltage_kv
        self.power_w    = power_w

        if voltage_kv not in KV:
            raise MantigoraDriverError(
                f"Неверное voltage_kv={voltage_kv}. Допустимо: {list(KV)}"
            )
        if power_w not in KI:
            raise MantigoraDriverError(
                f"Неверное power_w={power_w}. Допустимо: {list(KI)}"
            )

        self._kv = KV[voltage_kv]
        self._ki = KI[power_w][voltage_kv]
        self._voltage_max_v  = voltage_kv * 1000.0
        self._current_max_ma = MAX_CURRENT_MA.get((power_w, voltage_kv), 99.0)

        self._ser:  Optional[serial.Serial] = None
        self._lock = threading.Lock()

        self._is_connected:  bool  = False
        self._output_active: bool  = False

        self._setpoint_v:  float = 0.0
        self._setpoint_ma: float = 0.0

    # ------------------------------------------------------------------
    # Подключение / отключение
    # ------------------------------------------------------------------
    def connect(self) -> bool:
        """
        Открыть COM-порт.

        Returns
        -------
        bool
            True при успешном открытии порта.

        Raises
        ------
        MantigoraDriverError
            Если порт уже открыт или возникла ошибка serial.
        """
        with self._lock:
            if self._is_connected:
                raise MantigoraDriverError("Уже подключено")

            ser = serial.Serial(
                port          = self.port,
                baudrate      = self.baudrate,
                bytesize      = serial.EIGHTBITS,
                parity        = serial.PARITY_EVEN,   # чётная чётность
                stopbits      = serial.STOPBITS_ONE,
                timeout       = 1.0,
                write_timeout = 1.0,
            )
            self._ser           = ser
            self._is_connected  = True
            self._output_active = False
            return True  # ← исправление v2.1.1: явный возврат True

    def disconnect(self) -> None:
        """Выключить выход и закрыть COM-порт."""
        with self._lock:
            if not self._is_connected:
                return
            try:
                if self._output_active:
                    self._ser.write(bytes([self.CMD_STOP]))
                    self._output_active = False
                self._ser.close()
            except Exception:
                pass
            finally:
                self._ser          = None
                self._is_connected = False

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    @property
    def output_active(self) -> bool:
        return self._output_active

    # ------------------------------------------------------------------
    # Управление выходом
    # ------------------------------------------------------------------
    def set_voltage(self, voltage_v: float) -> None:
        """
        Установить уставку напряжения (В).
        Выход не включается — для активации вызовите start().
        """
        self._check_connected()
        if not (0.0 <= voltage_v <= self._voltage_max_v):
            raise MantigoraDriverError(
                f"Напряжение {voltage_v} В вне диапазона "
                f"[0, {self._voltage_max_v:.0f}] В"
            )
        self._setpoint_v = voltage_v

    def set_current_limit(self, current_ma: float) -> None:
        """
        Установить ограничение тока (мА).
        Выход не включается — для активации вызовите start().
        """
        self._check_connected()
        if not (0.0 <= current_ma <= self._current_max_ma):
            raise MantigoraDriverError(
                f"Ток {current_ma} мА вне диапазона "
                f"[0, {self._current_max_ma:.3f}] мА"
            )
        self._setpoint_ma = current_ma

    def start(self) -> None:
        """
        Передать уставки в устройство и включить выход.
        Команда [0x01 + U_code + I_code], затем [0x02].
        """
        self._check_connected()
        with self._lock:
            u_code = int(round(self._setpoint_v  * self._kv))
            i_code = int(round(self._setpoint_ma * 1000.0 * self._ki))  # мА → мкА

            # Клиппинг в 16 бит
            u_code = max(0, min(0xFFFF, u_code))
            i_code = max(0, min(0xFFFF, i_code))

            # Команда 0x01 + 4 байта в little-endian (U первым, затем I — по протоколу)
            payload = bytes([self.CMD_SET]) + struct.pack("<HH", u_code, i_code)
            self._ser.write(payload)
            time.sleep(0.05)

            # Команда 0x02 — включить выход
            self._ser.write(bytes([self.CMD_START]))
            self._output_active = True

    def stop(self) -> None:
        """Выключить выход — команда [0x03]. Напряжение сбрасывается в 0."""
        self._check_connected()
        with self._lock:
            self._ser.write(bytes([self.CMD_STOP]))
            self._output_active = False

    def apply_setpoints(self) -> None:
        """
        Передать текущие уставки без включения выхода.
        Только команда [0x01], без [0x02].
        """
        self._check_connected()
        with self._lock:
            u_code = int(round(self._setpoint_v  * self._kv))
            i_code = int(round(self._setpoint_ma * 1000.0 * self._ki))
            u_code = max(0, min(0xFFFF, u_code))
            i_code = max(0, min(0xFFFF, i_code))
            payload = bytes([self.CMD_SET]) + struct.pack("<HH", u_code, i_code)
            self._ser.write(payload)

    # ------------------------------------------------------------------
    # Чтение измерений
    # ------------------------------------------------------------------
    def read_measurement(self) -> Tuple[float, float]:
        """
        Запросить и прочитать фактические U и I.

        Returns
        -------
        (voltage_v, current_ma) : tuple[float, float]
            Фактическое напряжение (В) и ток (мА).

        Протокол ответа (5 байт, little-endian):
            [I_low][I_high][U_low][U_high][0x0D]
        """
        self._check_connected()
        with self._lock:
            self._ser.reset_input_buffer()
            self._ser.write(bytes([self.CMD_MEASURE]))
            data = self._ser.read(5)

        if len(data) < 5:
            raise MantigoraDriverError(
                f"Нет ответа от устройства (получено {len(data)} байт из 5). "
                f"Проверьте COM-порт и скорость."
            )

        if data[4] != self.END_BYTE:
            raise MantigoraDriverError(
                f"Неверный конец пакета: 0x{data[4]:02X} (ожидался 0x0D)"
            )

        # Little-endian: младший байт первым
        i_code = data[0] | (data[1] << 8)
        u_code = data[2] | (data[3] << 8)

        voltage_v  = u_code / self._kv
        current_ma = (i_code / self._ki) / 1000.0   # мкА → мА

        return voltage_v, current_ma

    # ------------------------------------------------------------------
    # Вспомогательные
    # ------------------------------------------------------------------
    def _check_connected(self) -> None:
        if not self._is_connected or self._ser is None:
            raise MantigoraDriverError("Устройство не подключено")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.disconnect()

    def __repr__(self) -> str:
        state = "подключено" if self._is_connected else "отключено"
        return (
            f"MantigoraDriver(port={self.port}, "
            f"{self.voltage_kv}кВ/{self.power_w}Вт, {state})"
        )


# ---------------------------------------------------------------------------
# Быстрый тест из командной строки
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    print("Доступные COM-порты:", list_com_ports())

    parser = argparse.ArgumentParser(description="Тест Mantigora HT-драйвера")
    parser.add_argument("--port",     default="COM1",  help="COM-порт")
    parser.add_argument("--baud",     type=int, default=9600,   help="Скорость")
    parser.add_argument("--kv",       type=int, default=2,      help="Макс. напряжение кВ")
    parser.add_argument("--watt",     type=int, default=6,      help="Мощность Вт")
    parser.add_argument("--voltage",  type=float, default=100.0, help="Уставка U (В)")
    parser.add_argument("--current",  type=float, default=1.0,   help="Уставка I (мА)")
    parser.add_argument("--duration", type=float, default=5.0,   help="Время работы (с)")
    args = parser.parse_args()

    drv = MantigoraDriver(
        port=args.port, baudrate=args.baud,
        voltage_kv=args.kv, power_w=args.watt
    )
    drv.connect()
    print(f"Подключено: {drv}")
    print(f"  Диапазон U: 0…{drv._voltage_max_v:.0f} В")
    print(f"  Диапазон I: 0…{drv._current_max_ma:.3f} мА")

    drv.set_voltage(args.voltage)
    drv.set_current_limit(args.current)
    drv.start()
    print(f"  Выход включён. U={args.voltage} В, I_max={args.current} мА")

    deadline = time.time() + args.duration
    while time.time() < deadline:
        try:
            v, i = drv.read_measurement()
            print(f"  U={v:.1f} В   I={i:.4f} мА")
        except MantigoraDriverError as e:
            print(f"  Ошибка чтения: {e}")
        time.sleep(0.5)

    drv.stop()
    print("  Выход выключен")
    drv.disconnect()
    print("Отключено.")
