"""
pts_driver.py  v2.0.0  (21.04.2026)

Драйвер для генератора MTE/EMH PTSx.xC
Протокол: RS232, ASCII-команды, терминатор CR (0x0D)
Документ: Interface_description_PTSx_xC.pdf

Исправления v2.0.0 (по сравнению с v1.0.0):
  - read_until(b'\r') вместо readline()
    PTS шлёт CR как терминатор, не LF. readline() ждал \n и блокировался
    на весь timeout (2с×N команд) за каждую строку. Теперь быстро.
  - threading.Lock — безопасный параллельный доступ из PTSPanel и PTSGenerator.
  - _parse_values: пропускает нечисловые поля (R/L/? в ответе ?9).
  - SET возвращает SET=1 (не =O) — это нормально по документации.
  - Таймаут порта 0.5с (не 2с) — быстрое чтение строк.
"""

import serial
import threading
import time
from typing import Optional, List


# ---------------------------------------------------------------------------
# Транспорт
# ---------------------------------------------------------------------------

class _PTSTransport:
    """Потокобезопасный RS232 транспорт."""

    def __init__(self, port: str, baudrate: int = 19200,
                 line_timeout: float = 0.5, cmd_timeout: float = 3.0):
        self._cmd_timeout = cmd_timeout
        self._lock = threading.Lock()

        self._ser = serial.Serial()
        self._ser.port         = port
        self._ser.baudrate     = baudrate
        self._ser.bytesize     = 8
        self._ser.parity       = serial.PARITY_NONE
        self._ser.stopbits     = 1
        self._ser.timeout      = line_timeout   # timeout на read_until
        self._ser.write_timeout = 2.0
        self._ser.xonxoff      = False
        self._ser.rtscts       = False
        self._ser.open()

    def close(self):
        if self._ser.is_open:
            try:
                self._ser.close()
            except Exception:
                pass

    def _read_line(self) -> str:
        """Прочитать одну строку до CR. Возвращает очищенную строку или ''."""
        data = self._ser.read_until(b'\r')
        return data.decode('ascii', errors='replace').strip('\r\n').strip()

    def send_raw(self, cmd: str) -> str:
        """
        Отправить команду (добавляется CR), прочитать все строки ответа.
        Потокобезопасен через Lock.

        MODE1: строка1 = эхо команды (cmd=O/E), строка2+ = данные
        MODE0: только строки данных
        """
        with self._lock:
            self._ser.reset_input_buffer()
            self._ser.write((cmd + '\r').encode('ascii'))

            lines    = []
            deadline = time.time() + self._cmd_timeout

            while time.time() < deadline:
                line = self._read_line()
                if not line:
                    break   # нет данных
                lines.append(line)
                # После получения строки активно ждём следующую до 200 мс.
                # PTS присылает эхо (r.?2=O) и затем данные (EA,...) с задержкой.
                # 30 мс было мало — строка данных терялась.
                t_wait = time.time() + 0.2
                while time.time() < t_wait:
                    if self._ser.in_waiting > 0:
                        break       # данные уже в буфере
                    time.sleep(0.01)
                if self._ser.in_waiting == 0:
                    break

            return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Базовый класс подсистемы
# ---------------------------------------------------------------------------

class _PTSSubsystem:
    PREFIX = ''

    def __init__(self, transport: _PTSTransport):
        self._t = transport

    def cmd(self, command: str) -> str:
        full = f'{self.PREFIX}{command}' if self.PREFIX else command
        return self._t.send_raw(full)

    def _check_ok(self, response: str, command: str) -> bool:
        if '=O' in response:
            return True
        if '=E' in response or '=?' in response:
            raise RuntimeError(f'PTS команда [{command}] отклонена: {response!r}')
        return True  # MODE0 не возвращает =O

    @staticmethod
    def _parse_values(response: str) -> List[float]:
        """
        Извлечь числа из строки ответа.

        Ответ (MODE1):
            r.?2=O
            EA,  230.00,  230.01, 229.99

        Ищем строку с запятой, пропускаем идентификатор (EA / E@ / ...),
        разбираем остаток в float. Нечисловые поля (R/L/? — направление
        вращения в ?9) пропускаются без ошибки.
        """
        for line in response.split('\n'):
            line = line.strip()
            if ',' not in line:
                continue
            after_id = line.split(',', 1)[1]
            result = []
            for part in after_id.split(','):
                part = part.strip()
                if not part or part == '--------':
                    continue
                try:
                    result.append(float(part))
                except ValueError:
                    pass   # R / L / ?
            if result:
                return result
        return []


# ---------------------------------------------------------------------------
# Reference Standard (r.)
# ---------------------------------------------------------------------------

class _PTSReference(_PTSSubsystem):
    PREFIX = 'r.'

    def set_mode_p4(self) -> bool:
        return self._check_ok(self.cmd('P4'), 'P4')

    def set_mode_p3(self) -> bool:
        return self._check_ok(self.cmd('P3'), 'P3')

    def set_timebase(self, seconds: float) -> bool:
        return self._check_ok(self.cmd(f'T{seconds}'), f'T{seconds}')

    def set_auto_range(self) -> bool:
        return self._check_ok(self.cmd('AUT'), 'AUT')

    def set_manual_range(self) -> bool:
        return self._check_ok(self.cmd('MAN'), 'MAN')

    def set_current_range(self, r1: int, r2: int = None, r3: int = None) -> str:
        if r2 is None and r3 is None:
            return self.cmd(f'I{r1},{r1},{r1}')
        return self.cmd(f'I{r1},{r2},{r3}')

    def set_voltage_range(self, r1: int, r2: int = None, r3: int = None) -> str:
        if r2 is None and r3 is None:
            return self.cmd(f'U{r1},{r1},{r1}')
        return self.cmd(f'U{r1},{r2},{r3}')

    def enable_all_results(self) -> bool:
        return self._check_ok(self.cmd('SP0,1'), 'SP0,1')

    def disable_all_results(self) -> bool:
        return self._check_ok(self.cmd('SP0,0'), 'SP0,0')

    def enable_result(self, result_no: int) -> bool:
        return self._check_ok(self.cmd(f'SP{result_no},1'), f'SP{result_no},1')

    def read_currents(self) -> List[float]:
        """[I1, I2, I3] А  (?1 → E@,I1,I2,I3)"""
        return self._parse_values(self.cmd('?1'))

    def read_voltages(self) -> List[float]:
        """[U1, U2, U3] В  (?2 → EA,U1,U2,U3)"""
        return self._parse_values(self.cmd('?2'))

    def read_active_power_phases(self) -> List[float]:
        """[P1, P2, P3] Вт  (?3 → EB,P1,P2,P3)"""
        return self._parse_values(self.cmd('?3'))

    def read_reactive_power_phases(self) -> List[float]:
        """[Q1, Q2, Q3] вар  (?4 → EC,Q1,Q2,Q3)"""
        return self._parse_values(self.cmd('?4'))

    def read_apparent_power_phases(self) -> List[float]:
        """[S1, S2, S3] ВА  (?5 → ED,S1,S2,S3)"""
        return self._parse_values(self.cmd('?5'))

    def read_active_power_sum(self) -> Optional[float]:
        """Σ P [Вт]  (?6 → EE,P)"""
        vals = self._parse_values(self.cmd('?6'))
        return vals[0] if vals else None

    def read_reactive_power_sum(self) -> Optional[float]:
        """Σ Q [вар]  (?7 → EF,Q)"""
        vals = self._parse_values(self.cmd('?7'))
        return vals[0] if vals else None

    def read_phase_angles(self) -> List[float]:
        """
        [φ1, φ2, φ3] °  (?9 → EH,φ1,φ2,φ3,<ps>)
        <ps> = R/L/? — направление вращения, пропускается, возвращаем только [:3].
        """
        vals = self._parse_values(self.cmd('?9'))
        return vals[:3]

    def read_frequency(self) -> Optional[float]:
        """Частота [Гц]  (?13 → EL,frq)"""
        vals = self._parse_values(self.cmd('?13'))
        return vals[0] if vals else None

    def read_all(self) -> str:
        """RDR — все текущие значения сразу."""
        return self.cmd('RDR')

    def read_status(self) -> str:
        return self.cmd('SG')

    def set_meter_constant(self, input_no: int, constant: float,
                           unit: str = 'i/kWh') -> bool:
        return self._check_ok(
            self.cmd(f'cpX{input_no},{constant}'), f'cpX{input_no}')

    def set_error_test_duration(self, input_no: int, seconds: float) -> bool:
        return self._check_ok(
            self.cmd(f'TFX{input_no},{seconds}'), f'TFX{input_no}')

    def set_error_repetitions(self, input_no: int, n: int) -> bool:
        return self._check_ok(
            self.cmd(f'TNX{input_no},{n}'), f'TNX{input_no}')

    def set_error_reference(self, input_no: int, ref: int = 34) -> bool:
        return self._check_ok(
            self.cmd(f'FFSX{input_no},{ref}'), f'FFSX{input_no}')

    def start_error_measurement(self, input_no: int = 0) -> bool:
        return self._check_ok(
            self.cmd(f'FFX{input_no}'), f'FFX{input_no}')

    def stop_error_measurement(self) -> bool:
        return self._check_ok(self.cmd('FF0'), 'FF0')

    def read_error_result(self, input_no: int = 1) -> dict:
        resp = self.cmd('?23')
        vals = self._parse_values(resp)
        if len(vals) >= 2:
            return {'energy': vals[0], 'error': vals[1]}
        return {}

    def read_statistical_error(self, input_no: int = 1) -> dict:
        result_map = {1: '?30', 2: '?31', 3: '?32'}
        resp = self.cmd(result_map.get(input_no, '?30'))
        vals = self._parse_values(resp)
        if len(vals) >= 5:
            return dict(status=vals[0], count=int(vals[1]),
                        mean=vals[2], std=vals[3], freq=vals[4])
        return {}


# ---------------------------------------------------------------------------
# Power Source (s.)
# ---------------------------------------------------------------------------

class _PTSSource(_PTSSubsystem):
    PREFIX = 's.'

    def set_voltage(self, u1: float, u2: float = None, u3: float = None):
        """Задать напряжение (В) по фазам. Применяется при on()."""
        if u2 is None:
            u2 = u3 = u1
        self.cmd(f'U1,{u1}')
        self.cmd(f'U2,{u2}')
        self.cmd(f'U3,{u3}')

    def set_current(self, i1: float, i2: float = None, i3: float = None):
        """Задать ток (А) по фазам."""
        if i2 is None:
            i2 = i3 = i1
        self.cmd(f'I1,{i1}')
        self.cmd(f'I2,{i2}')
        self.cmd(f'I3,{i3}')

    def set_angle(self, phi1: float, phi2: float = None, phi3: float = None):
        """Угол между U и I (°). phi=0 → cosφ=1. Команда W (Winkel)."""
        if phi2 is None:
            phi2 = phi3 = phi1
        self.cmd(f'W1,{phi1}')
        self.cmd(f'W2,{phi2}')
        self.cmd(f'W3,{phi3}')

    def set_frequency(self, freq: float) -> bool:
        return self._check_ok(self.cmd(f'FRQ{freq}'), f'FRQ{freq}')

    def set_ramp_current(self, phase: int, duration: float) -> bool:
        return self._check_ok(
            self.cmd(f'RAMPI{phase},{duration}'), f'RAMPI{phase}')

    def set_ramp_voltage(self, phase: int, duration: float) -> bool:
        return self._check_ok(
            self.cmd(f'RAMPU{phase},{duration}'), f'RAMPU{phase}')

    def on(self, bsy_timeout: float = 30.0):
        """
        Применить уставки и включить генератор (SET).
        SET возвращает SET=1 (не =O) — это нормально по документации.
        Блокируется пока BSY=1.

        Важно: после отправки SET нужно подождать 200 мс прежде чем
        проверять BSY. Иначе возможно прочитать BSY=0 от предыдущего
        состояния, до того как прибор успел выставить BSY=1.
        """
        self.cmd('SET')           # SET=1 — не проверяем через _check_ok
        time.sleep(0.2)           # Дать прибору время выставить BSY=1
        self._wait_ready(bsy_timeout)

    def off(self, mode: int = 0):
        """
        Выключить генератор.
        mode=0 — плавно U+I (рампа)
        mode=1 — мгновенно U+I
        mode=2 — только U / mode=3 — только I
        """
        cmd = f'OFF{mode}' if mode else 'OFF'
        self._check_ok(self.cmd(cmd), cmd)

    def restore_on(self, mode: int = 1):
        return self._check_ok(self.cmd(f'ON{mode}'), f'ON{mode}')

    def is_busy(self) -> bool:
        resp = self.cmd('BSY')
        # Возможные форматы: 's.BSY=1', 'BSY=1', '1'
        return 'BSY=1' in resp or resp.strip() == '1'

    def _wait_ready(self, timeout: float = 30.0):
        t0 = time.time()
        while time.time() - t0 < timeout:
            if not self.is_busy():
                return
            time.sleep(0.1)
        raise TimeoutError(
            f'PTS Source: SET не завершился за {timeout} с')

    def read_currents(self) -> List[float]:
        """[I1, I2, I3] А (s.i → E@,...)"""
        return self._parse_values(self.cmd('i'))

    def read_voltages(self) -> List[float]:
        """[U1, U2, U3] В (s.u → EA,...)"""
        return self._parse_values(self.cmd('u'))

    def read_angles(self) -> List[float]:
        """
        [φ1, φ2, φ3] ° (s.p → EH,φ1,φ2,φ3)
        У источника ответ p только 3 значения (без ps), в отличие от r.?9.
        """
        return self._parse_values(self.cmd('p'))

    def status_current_amps(self, phase: int = None) -> str:
        if phase:
            return self.cmd(f'STATI{phase}')
        return self.cmd('STATI')

    def status_voltage_amps(self, phase: int = None) -> str:
        if phase:
            return self.cmd(f'STATU{phase}')
        return self.cmd('STATU')

    def extended_status(self) -> str:
        return self.cmd('SE')

    def set_harmonic_current(self, phase: int, order: int,
                             amplitude_pct: float, phi_deg: float = 0):
        return self.cmd(f'OWI{phase},{order},{amplitude_pct},{phi_deg}')

    def set_harmonic_voltage(self, phase: int, order: int,
                             amplitude_pct: float, phi_deg: float = 0):
        return self.cmd(f'OWU{phase},{order},{amplitude_pct},{phi_deg}')

    def clear_harmonics_current(self, phase: int):
        return self.cmd(f'OWI{phase},0,0')

    def clear_harmonics_voltage(self, phase: int):
        return self.cmd(f'OWU{phase},0,0')


# ---------------------------------------------------------------------------
# Главный клиент
# ---------------------------------------------------------------------------

class PTSx_Client:
    """
    Клиент MTE/EMH PTSx.xC.

    RS232: 4800–115200 бод, 8N1, без аппаратного потока управления.

    Пример:
        with PTSx_Client('COM3') as pts:
            pts.mode1()
            pts.source.set_voltage(230)
            pts.source.set_current(5)
            pts.source.set_frequency(50)
            pts.source.set_angle(0)
            pts.source.on()
            u = pts.reference.read_voltages()
            pts.source.off()
    """

    def __init__(self, port: str, baudrate: int = 19200,
                 line_timeout: float = 0.5, cmd_timeout: float = 3.0):
        self._transport = _PTSTransport(port, baudrate, line_timeout, cmd_timeout)
        self.reference  = _PTSReference(self._transport)
        self.source     = _PTSSource(self._transport)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        self._transport.close()

    def _cmd(self, command: str) -> str:
        return self._transport.send_raw(command)

    def mode1(self):
        """
        Включить расширенный формат ответа (MODE1).
        Отправляем дважды: первый раз устройство может быть в MODE0
        (без подтверждения), второй гарантирует MODE1.
        """
        self._cmd('MODE1')
        time.sleep(0.15)
        self._cmd('MODE1')

    def mode0(self):
        self._cmd('MODE0')

    def set_default_device(self, device: int):
        """PCD: device=1 → Reference, device=2 → Source."""
        return self._cmd(f'PCD{device}')

    def read_version(self) -> str:
        return self._cmd('VER')

    def reset(self):
        return self._cmd('R')

    def lock_keypad(self):
        return self._cmd('DK')

    def unlock_keypad(self):
        return self._cmd('EK')
