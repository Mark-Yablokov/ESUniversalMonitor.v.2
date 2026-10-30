"""
Автор: m.yablokov
drivers/pts_driver.py  v3.0.0

Драйвер для генератора MTE/EMH PTSx.xC
Протокол: RS232, ASCII-команды, терминатор CR (0x0D)
Документ: Interface_description_PTSx_xC.pdf

Что нового в v3.0.0 (по сравнению с v2.0.0):
  ЗАЩИТА (нужна для 300 В / 120 А)
  - Лимиты источника: max_voltage (300 В), max_current (120 А) и необязательный
    max_va_per_phase (лимит полной мощности на фазу). Проверяются ДО отправки
    команд — при превышении бросается ValueError, ничего на прибор не уходит.
  - emergency_off() / abort(): аварийное отключение OFF1 + прерывание ожидания
    BSY после SET (раньше SET мог "держать" вызывающий поток до 30 с).
  - Все команды установки проверяются на ответ =E (раньше U/I/W не проверялись).

  ИСТОЧНИК (s.)
  - apply_loadpoint(): одна точка нагрузки (U, I, φ, f) + SET одной функцией —
    используется и панелью, и генератором.
  - Диапазоны источника BI/BU, амплитуда AI/AU (в %), абсолютные фазы PH,
    реле токовых выходов SKLI (для 120 А), 3/4-проводный режим TRI,
    рампы RAMPI/RAMPU (для всех фаз), гармоники OWI/OWU, Ripple control
    (RCSI/RCSU/RCSIP/RCSUP).
  - Статусы: SE (ошибка/предупреждение/занят), STATI/STATU по фазам.
  - Чтение параметров PAR<n> (например PAR4/PAR5 — границы частоты).

  ЭТАЛОН (r.)
  - Режим измерения P4/P3/N4/N3/K4/K3, выбор токового (DB) и
    напряжения (DBU) входов, синхронизация диапазонов YI/YU, нижний предел
    диапазона IAL/UAL, флаги перегрузки ROLI/ROLU.

  ПРОЧЕЕ
  - Числа форматируются без экспоненты (1e-05 → 0.00001), PTS её не понимает.
  - Разбор ответов (BSY, SE, STATI, ROLI, PAR) устойчив к MODE0/MODE1.
"""

import re
import serial
import threading
import time
from typing import Dict, List, Optional, Sequence, Tuple


# ---------------------------------------------------------------------------
# Предельные значения источника (ЕДИНОЕ место для правки лимитов)
# ---------------------------------------------------------------------------
DEFAULT_MAX_VOLTAGE = 300.0   # В   — максимум напряжения на фазу
DEFAULT_MAX_CURRENT = 120.0   # А   — максимум тока на фазу


def _num(x: float) -> str:
    """Число → строка без экспоненты и лишних нулей (PTS не понимает 1e-05)."""
    s = f'{float(x):.6f}'.rstrip('0').rstrip('.')
    return s if s not in ('', '-0') else '0'


def triple_from(a: Optional[float], b: Optional[float],
                c: Optional[float]) -> Optional[Tuple[float, float, float]]:
    """
    Собрать значения трёх фаз из частично заполненных (a, b, c).
    Правило как в старых версиях панели/генератора:
      - все None → None (параметр не менять)
      - a=None → 0.0; b и c, если None, берутся равными a.
    """
    if a is None and b is None and c is None:
        return None
    a = 0.0 if a is None else a
    b = a if b is None else b
    c = a if c is None else c
    return (a, b, c)


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
        self._ser.port          = port
        self._ser.baudrate      = baudrate
        self._ser.bytesize      = 8
        self._ser.parity        = serial.PARITY_NONE
        self._ser.stopbits      = 1
        self._ser.timeout       = line_timeout   # timeout на read_until
        self._ser.write_timeout = 2.0
        self._ser.xonxoff       = False
        self._ser.rtscts        = False
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

    # ---- разбор ответов ---------------------------------------------------

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

    @staticmethod
    def _value_after_eq(response: str) -> Optional[str]:
        """
        Значение после '=' в последней подходящей строке.
        'PAR4=45.0' → '45.0';  MODE0: '45.0' → '45.0'.
        Строки-подтверждения (=O / =E / =?) пропускаются.
        """
        for line in reversed(response.split('\n')):
            line = line.strip()
            if not line:
                continue
            if '=' in line:
                val = line.split('=', 1)[1].strip()
                if val in ('O', 'E', '?', ''):
                    continue
                return val
            return line
        return None

    @classmethod
    def _parse_int(cls, response: str) -> Optional[int]:
        val = cls._value_after_eq(response)
        if val is not None and re.fullmatch(r'-?\d+', val):
            return int(val)
        return None

    @classmethod
    def _parse_float(cls, response: str) -> Optional[float]:
        val = cls._value_after_eq(response)
        try:
            return float(val) if val is not None else None
        except ValueError:
            return None

    @classmethod
    def _parse_digit_flags(cls, response: str, n: int = 3) -> Optional[List[int]]:
        """'ROLI=000' → [0, 0, 0]"""
        for line in reversed(response.split('\n')):
            line = line.strip()
            if '=' in line:
                line = line.split('=', 1)[1]
            line = line.replace(',', '').replace(' ', '')
            if re.fullmatch(r'\d{%d}' % n, line):
                return [int(ch) for ch in line]
        return None


# ---------------------------------------------------------------------------
# Reference Standard (r.)
# ---------------------------------------------------------------------------

class _PTSReference(_PTSSubsystem):
    PREFIX = 'r.'

    MEASURE_MODES = ('P4', 'P3', 'N4', 'N3', 'K4', 'K3')

    # ---- режимы / входы / диапазоны ---------------------------------------

    def set_mode_p4(self) -> bool:
        return self._check_ok(self.cmd('P4'), 'P4')

    def set_mode_p3(self) -> bool:
        return self._check_ok(self.cmd('P3'), 'P3')

    def set_measure_mode(self, mode: str) -> bool:
        """P4/P3 — активная; N4/N3 — реактивная "натуральная"; K4/K3 — искусственная."""
        mode = mode.upper()
        if mode not in self.MEASURE_MODES:
            raise ValueError(f'Неизвестный режим {mode!r}')
        return self._check_ok(self.cmd(mode), mode)

    def read_measure_mode(self) -> Optional[str]:
        """M → 'P4'"""
        return self._value_after_eq(self.cmd('M'))

    def set_timebase(self, seconds: float) -> bool:
        c = f'T{_num(seconds)}'
        return self._check_ok(self.cmd(c), c)

    def set_auto_range(self) -> bool:
        return self._check_ok(self.cmd('AUT'), 'AUT')

    def set_manual_range(self) -> bool:
        return self._check_ok(self.cmd('MAN'), 'MAN')

    def set_current_range(self, r1: int, r2: int = None, r3: int = None) -> bool:
        """Диапазоны тока (действуют только при MAN). Нумерация — табл. 5-3."""
        if r2 is None and r3 is None:
            r2 = r3 = r1
        c = f'I{r1},{r2},{r3}'
        return self._check_ok(self.cmd(c), c)

    def set_voltage_range(self, r1: int, r2: int = None, r3: int = None) -> bool:
        """Диапазоны напряжения (действуют только при MAN). Табл. 5-4."""
        if r2 is None and r3 is None:
            r2 = r3 = r1
        c = f'U{r1},{r2},{r3}'
        return self._check_ok(self.cmd(c), c)

    def set_current_input(self, n: int) -> bool:
        """DB<n>: 0=120А (станд.), 1=12А, 2=CT100А, 3=CT1000А, 4..6=LEMflex, 7/8=Hotstick."""
        if not 0 <= n <= 8:
            raise ValueError('DB: 0..8')
        return self._check_ok(self.cmd(f'DB{n}'), f'DB{n}')

    def set_voltage_input(self, n: int) -> bool:
        """DBU<n>: 0=станд. вход, 1=альтернативный, 2..8 — см. документ (5.2)."""
        if not 0 <= n <= 8:
            raise ValueError('DBU: 0..8')
        return self._check_ok(self.cmd(f'DBU{n}'), f'DBU{n}')

    def set_current_sync(self, on: bool) -> bool:
        """YI: во всех фазах использовать наибольший диапазон тока."""
        c = f'YI{1 if on else 0}'
        return self._check_ok(self.cmd(c), c)

    def set_voltage_sync(self, on: bool) -> bool:
        c = f'YU{1 if on else 0}'
        return self._check_ok(self.cmd(c), c)

    def set_current_range_limit(self, on: bool) -> bool:
        """IAL: 1 = AUT не выбирает самые нижние диапазоны тока."""
        c = f'IAL{1 if on else 0}'
        return self._check_ok(self.cmd(c), c)

    def set_voltage_range_limit(self, on: bool) -> bool:
        c = f'UAL{1 if on else 0}'
        return self._check_ok(self.cmd(c), c)

    # ---- результаты -------------------------------------------------------

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

    def read_apparent_power_sum(self) -> Optional[float]:
        """Σ S [ВА]  (?8 → EG,S)"""
        vals = self._parse_values(self.cmd('?8'))
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
        """SG — статус эталона (диапазоны, режим, время, входы)."""
        return self.cmd('SG')

    def read_overflow_current(self) -> Optional[List[int]]:
        """ROLI → [fl1, fl2, fl3], 1 = перегрузка по току в фазе."""
        return self._parse_digit_flags(self.cmd('ROLI'))

    def read_overflow_voltage(self) -> Optional[List[int]]:
        """ROLU → [fl1, fl2, fl3], 1 = перегрузка по напряжению в фазе."""
        return self._parse_digit_flags(self.cmd('ROLU'))

    # ---- измерение погрешности (как было) ---------------------------------

    def set_meter_constant(self, input_no: int, constant: float,
                           unit: str = 'i/kWh') -> bool:
        return self._check_ok(
            self.cmd(f'cpX{input_no},{_num(constant)}'), f'cpX{input_no}')

    def set_error_test_duration(self, input_no: int, seconds: float) -> bool:
        return self._check_ok(
            self.cmd(f'TFX{input_no},{_num(seconds)}'), f'TFX{input_no}')

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

    # Расшифровка STATI/STATU (док. 6.5)
    AMP_STATUS_TEXT = {
        0: 'выключен',
        1: 'включён',
        2: 'ПЕРЕГРУЗКА',
        3: 'отключён по перегрузке',
        4: 'отключён (DIS)',
    }

    def __init__(self, transport: _PTSTransport):
        super().__init__(transport)
        # --- защитные лимиты (меняются снаружи, например из панели) ---
        self.max_voltage: float = DEFAULT_MAX_VOLTAGE
        self.max_current: float = DEFAULT_MAX_CURRENT
        self.max_va_per_phase: Optional[float] = None   # None = не проверять
        # --- служебное ---
        self._abort = threading.Event()
        self._ramp_max = 0.0
        self._last_u = [0.0, 0.0, 0.0]
        self._last_i = [0.0, 0.0, 0.0]

    # ---- защита -----------------------------------------------------------

    def _check_limits(self, name: str, values: Sequence[float], limit: float):
        for k, v in enumerate(values, 1):
            if v < 0 or v > limit:
                raise ValueError(
                    f'{name}{k} = {v} выходит за допустимый предел 0…{limit}')

    def _check_power(self, u: Optional[Sequence[float]] = None,
                     i: Optional[Sequence[float]] = None):
        """Проверка S = U·I на фазу (если задан max_va_per_phase)."""
        if not self.max_va_per_phase:
            return
        u = u if u is not None else self._last_u
        i = i if i is not None else self._last_i
        for k in range(3):
            s = u[k] * i[k]
            if s > self.max_va_per_phase:
                raise ValueError(
                    f'Фаза {k + 1}: S = {u[k]:.1f} В × {i[k]:.2f} А = {s:.0f} ВА '
                    f'превышает лимит {self.max_va_per_phase:.0f} ВА')

    # ---- уставки ----------------------------------------------------------

    def set_voltage(self, u1: float, u2: float = None, u3: float = None):
        """Задать напряжение (В) по фазам. Применяется при on()."""
        if u2 is None:
            u2 = u3 = u1
        vals = [u1, u2, u3]
        self._check_limits('U', vals, self.max_voltage)
        for ph, v in enumerate(vals, 1):
            c = f'U{ph},{_num(v)}'
            self._check_ok(self.cmd(c), c)
        self._last_u = [float(v) for v in vals]

    def set_current(self, i1: float, i2: float = None, i3: float = None):
        """Задать ток (А) по фазам."""
        if i2 is None:
            i2 = i3 = i1
        vals = [i1, i2, i3]
        self._check_limits('I', vals, self.max_current)
        for ph, v in enumerate(vals, 1):
            c = f'I{ph},{_num(v)}'
            self._check_ok(self.cmd(c), c)
        self._last_i = [float(v) for v in vals]

    def set_angle(self, phi1: float, phi2: float = None, phi3: float = None):
        """Угол между U и I (°). phi=0 → cosφ=1. Команда W. Прибор берёт по модулю 360."""
        if phi2 is None:
            phi2 = phi3 = phi1
        for ph, v in enumerate((phi1, phi2, phi3), 1):
            c = f'W{ph},{_num(v % 360.0)}'
            self._check_ok(self.cmd(c), c)

    def set_abs_phase(self, a1: float, a2: float, a3: float):
        """PH: абсолютный фазовый угол напряжений U1..U3 (°)."""
        for ph, v in enumerate((a1, a2, a3), 1):
            c = f'PH{ph},{_num(v % 360.0)}'
            self._check_ok(self.cmd(c), c)

    def set_frequency(self, freq: float) -> bool:
        """FRQ: 0 = синхронизация с сетью (если опция установлена)."""
        c = f'FRQ{_num(freq)}'
        return self._check_ok(self.cmd(c), c)

    def set_amplitude_current_pct(self, phase: int, pct: float) -> bool:
        """AI: ток фазы в % от конца внутреннего диапазона."""
        c = f'AI{phase},{_num(pct)}'
        return self._check_ok(self.cmd(c), c)

    def set_amplitude_voltage_pct(self, phase: int, pct: float) -> bool:
        """AU: напряжение фазы в % от конца внутреннего диапазона."""
        c = f'AU{phase},{_num(pct)}'
        return self._check_ok(self.cmd(c), c)

    # ---- диапазоны, реле, режим проводки, рампы ---------------------------

    def set_current_range(self, phase: int, rng: int) -> bool:
        """BI: диапазон усилителя тока. rng=0 → наименьший подходящий (авто)."""
        c = f'BI{phase},{rng}'
        return self._check_ok(self.cmd(c), c)

    def set_voltage_range(self, phase: int, rng: int) -> bool:
        """BU: диапазон усилителя напряжения. rng=0 → авто."""
        c = f'BU{phase},{rng}'
        return self._check_ok(self.cmd(c), c)

    def set_current_relay(self, closed: bool, phase: int = 0) -> bool:
        """
        SKLI: реле между малыми и большими токовыми выходами.
          closed=False (SKLI0,0) — разомкнуто: малые + большие выходы
          closed=True  (SKLI0,1) — замкнуто: только сильноточные выходы
        phase=0 — все фазы. Только PTS x.xC до 120 А. В диапазонах тока 1–2
        прибор сам размыкает реле.
        """
        c = f'SKLI{phase},{1 if closed else 0}'
        return self._check_ok(self.cmd(c), c)

    def set_wire_mode(self, three_wire: bool) -> bool:
        """TRI (у источника): 0 = 4-проводная схема, 1 = 3-проводная."""
        c = f'TRI{1 if three_wire else 0}'
        return self._check_ok(self.cmd(c), c)

    def set_ramp_current(self, phase: int, duration: float) -> bool:
        c = f'RAMPI{phase},{_num(duration)}'
        ok = self._check_ok(self.cmd(c), c)
        self._ramp_max = max(self._ramp_max, float(duration))
        return ok

    def set_ramp_voltage(self, phase: int, duration: float) -> bool:
        c = f'RAMPU{phase},{_num(duration)}'
        ok = self._check_ok(self.cmd(c), c)
        self._ramp_max = max(self._ramp_max, float(duration))
        return ok

    def set_ramps(self, current_s: float, voltage_s: float):
        """Рампа включения/выключения (0…600 с) сразу для всех трёх фаз."""
        if not (0.0 <= current_s <= 600.0 and 0.0 <= voltage_s <= 600.0):
            raise ValueError('Рампа должна быть в пределах 0…600 с')
        self._ramp_max = 0.0
        for ph in (1, 2, 3):
            self.set_ramp_current(ph, current_s)
            self.set_ramp_voltage(ph, voltage_s)

    # ---- включение / выключение -------------------------------------------

    def abort(self):
        """Прервать ожидание BSY в on() (вызывать из другого потока)."""
        self._abort.set()

    def on(self, bsy_timeout: float = 30.0):
        """
        Применить уставки и включить генератор (SET).
        SET возвращает SET=1 (не =O) — это нормально по документации.
        Блокируется пока BSY=1, но не дольше max(bsy_timeout, рампа+10 с).

        После отправки SET ждём 200 мс прежде чем проверять BSY — иначе можно
        прочитать BSY=0 от предыдущего состояния.
        """
        self._abort.clear()
        self._check_power()
        self.cmd('SET')
        time.sleep(0.2)
        self._wait_ready(max(bsy_timeout, self._ramp_max + 10.0))

    def off(self, mode: int = 0):
        """
        Выключить генератор.
        mode=0 — плавно U+I (рампа)
        mode=1 — мгновенно U+I
        mode=2 — только U / mode=3 — только I
        """
        cmd = f'OFF{mode}' if mode else 'OFF'
        self._check_ok(self.cmd(cmd), cmd)

    def emergency_off(self, attempts: int = 3):
        """Аварийное отключение: прервать ожидание SET и послать OFF1 (до 3 раз)."""
        self._abort.set()
        last = None
        for _ in range(attempts):
            try:
                self.off(mode=1)
                return True
            except Exception as e:      # noqa: BLE001
                last = e
                time.sleep(0.1)
        raise RuntimeError(f'OFF1 не выполнен: {last}')

    def restore_on(self, mode: int = 1):
        """ON<mode>: вернуть состояние до OFF (1=U+I, 2=только U, 3=только I)."""
        c = f'ON{mode}'
        return self._check_ok(self.cmd(c), c)

    def is_busy(self) -> bool:
        resp = self.cmd('BSY')
        m = re.search(r'BSY\s*=\s*([01])', resp)
        if m:
            return m.group(1) == '1'
        return resp.strip().split('\n')[-1].strip() == '1'   # MODE0

    def _wait_ready(self, timeout: float = 30.0):
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self._abort.is_set():
                raise RuntimeError('SET прерван (аварийное отключение)')
            if not self.is_busy():
                return
            time.sleep(0.1)
        raise TimeoutError(f'PTS Source: SET не завершился за {timeout} с')

    # ---- одна точка нагрузки ----------------------------------------------

    def apply_loadpoint(self,
                        u: Optional[Sequence[float]] = None,
                        i: Optional[Sequence[float]] = None,
                        phi: Optional[Sequence[float]] = None,
                        f: Optional[float] = None,
                        wait: bool = True,
                        bsy_timeout: float = 30.0):
        """
        Задать (то, что не None) и включить точку нагрузки одним вызовом.
          u, i, phi — тройки по фазам (можно собрать через triple_from)
          f         — частота, Гц
        Все пределы проверяются ДО отправки первой команды.
        """
        if u is not None:
            self._check_limits('U', u, self.max_voltage)
        if i is not None:
            self._check_limits('I', i, self.max_current)
        self._check_power(u, i)

        if u is not None:
            self.set_voltage(*u)
        if i is not None:
            self.set_current(*i)
        if phi is not None:
            self.set_angle(*phi)
        if f is not None:
            self.set_frequency(f)
        if wait:
            self.on(bsy_timeout)
        else:
            self.cmd('SET')

    # ---- чтение -----------------------------------------------------------

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

    def read_amp_status(self, kind: str = 'I') -> List[Optional[int]]:
        """
        Статус усилителей по фазам: kind='I' (ток) или 'U' (напряжение).
        Значения 0..4, расшифровка — AMP_STATUS_TEXT. Не удалось прочитать → None.
        """
        kind = kind.upper()
        if kind not in ('I', 'U'):
            raise ValueError("kind: 'I' или 'U'")
        return [self._parse_int(self.cmd(f'STAT{kind}{ph}')) for ph in (1, 2, 3)]

    def extended_status(self) -> str:
        return self.cmd('SE')

    def read_extended_status(self) -> Optional[Dict[str, bool]]:
        """
        SE=E<Err><Warn>S<DOS_U><DOS_I><Busy><Packet><Ripple>
        Возвращает dict флагов (True = флаг выставлен) или None, если не разобрали.
        dos_u / dos_i = True означает, что контакт дозирования ОТКРЫТ.
        """
        resp = self.cmd('SE')
        for line in reversed(resp.split('\n')):
            line = line.strip()
            if '=' in line:
                line = line.split('=', 1)[1].strip()
            m = re.fullmatch(r'E([01]{2})S([01]{5})', line)
            if m:
                e, s = m.group(1), m.group(2)
                return {
                    'error':   e[0] == '1',
                    'warning': e[1] == '1',
                    'dos_u':   s[0] == '1',
                    'dos_i':   s[1] == '1',
                    'busy':    s[2] == '1',
                    'packet':  s[3] == '1',
                    'ripple':  s[4] == '1',
                }
        return None

    def read_parameter(self, number: int) -> Optional[str]:
        """PAR<n> — прочитать параметр источника (только чтение!)."""
        return self._value_after_eq(self.cmd(f'PAR{number}'))

    def read_parameter_float(self, number: int) -> Optional[float]:
        try:
            v = self.read_parameter(number)
            return float(v) if v is not None else None
        except ValueError:
            return None

    # ---- гармоники --------------------------------------------------------

    def set_harmonic(self, kind: str, phase: int, order: int,
                     amplitude_pct: float, phi_deg: float = 0.0) -> bool:
        """
        OWI/OWU: гармоника для усилителя тока (kind='I') или напряжения ('U').
          order 0 = DC, 1 = основная, 2…31 = гармоники
          amplitude_pct — % от амплитуды основной
        Включается при ближайшем SET (PAR14=0/1) или сразу (PAR14=2/3).
        ВНИМАНИЕ: суммарное действующее значение растёт — следите за 300 В / 120 А.
        """
        kind = kind.upper()
        if kind not in ('I', 'U'):
            raise ValueError("kind: 'I' или 'U'")
        if not 0 <= order <= 31:
            raise ValueError('Номер гармоники 0…31')
        c = f'OW{kind}{phase},{order},{_num(amplitude_pct)},{_num(phi_deg)}'
        return self._check_ok(self.cmd(c), c)

    def clear_harmonics(self, kind: str, phase: int) -> bool:
        """OWI<ph>,0,0 / OWU<ph>,0,0 — удалить гармоники 2…31 фазы."""
        kind = kind.upper()
        c = f'OW{kind}{phase},0,0'
        return self._check_ok(self.cmd(c), c)

    def set_harmonic_current(self, phase: int, order: int,
                             amplitude_pct: float, phi_deg: float = 0):
        return self.set_harmonic('I', phase, order, amplitude_pct, phi_deg)

    def set_harmonic_voltage(self, phase: int, order: int,
                             amplitude_pct: float, phi_deg: float = 0):
        return self.set_harmonic('U', phase, order, amplitude_pct, phi_deg)

    def clear_harmonics_current(self, phase: int):
        return self.clear_harmonics('I', phase)

    def clear_harmonics_voltage(self, phase: int):
        return self.clear_harmonics('U', phase)

    # ---- Ripple control (телеграмма) --------------------------------------

    @staticmethod
    def _ripple_name(kind: str, percent: bool) -> str:
        kind = kind.upper()
        if kind not in ('I', 'U'):
            raise ValueError("kind: 'I' или 'U'")
        return f'RCS{kind}{"P" if percent else ""}'

    def ripple_define(self, kind: str, phase: int, duration_ms: int,
                      amplitude: float, frequency: float,
                      percent: bool = False) -> bool:
        """
        Добавить импульс (amplitude>0) или паузу (amplitude=0) в телеграмму фазы.
        До 250 точек на фазу. amplitude — в А/В (percent=False) или в % (RCS?P).
        Запуск телеграммы — командой on() (SET), после окончания она стирается.
        """
        if amplitude < 0:
            raise ValueError('Амплитуда ripple не может быть отрицательной')
        if not percent:
            self._check_limits('Ripple', [amplitude],
                               self.max_current if kind.upper() == 'I'
                               else self.max_voltage)
        name = self._ripple_name(kind, percent)
        c = (f'{name}{phase},{int(duration_ms)},'
             f'{_num(amplitude)},{_num(frequency)}')
        return self._check_ok(self.cmd(c), c)

    def ripple_stop(self, kind: str, phase: int, percent: bool = False) -> bool:
        name = self._ripple_name(kind, percent)
        c = f'{name}{phase},0'
        return self._check_ok(self.cmd(c), c)

    def ripple_progress(self, kind: str, phase: int) -> Optional[float]:
        """0 = не запущена/закончена; 0.0 = готова к запуску; 0.1…100 = % выполнено."""
        return self._parse_float(self.cmd(f'RCS{kind.upper()}{phase}'))


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
            pts.source.apply_loadpoint(u=(230, 230, 230), i=(5, 5, 5),
                                       phi=(0, 0, 0), f=50)
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

    def set_limits(self, max_voltage: float = None, max_current: float = None,
                   max_va_per_phase: Optional[float] = None):
        """Задать защитные лимиты источника (None у max_va_per_phase = выключить)."""
        if max_voltage is not None:
            self.source.max_voltage = float(max_voltage)
        if max_current is not None:
            self.source.max_current = float(max_current)
        self.source.max_va_per_phase = (float(max_va_per_phase)
                                        if max_va_per_phase else None)

    def emergency_off(self):
        return self.source.emergency_off()

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
