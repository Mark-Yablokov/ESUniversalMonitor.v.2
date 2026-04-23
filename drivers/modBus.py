'''
Создано 14.10.2024
Последняя ревизия 17.04.2026
Версия 2.0.3
Автор: m.yablokov
Редакция: адаптировано под UniversalMonitor

Изменения v2.0.3:
  - В ModBus_TCP_Client при parse=True добавлен адрес устройства первым байтом
    в PDU перед вызовом parsing(). Это исправляет несовместимость с парсером,
    который ожидает PDU с адресом (как в RTU). Теперь TCP-ответы корректно
    распознаются, и данные с ENMV-3 читаются успешно.
'''

import serial
import time
import socket
import math
import struct
from typing import Optional

# ---------------------------------------------------------------------------
# Базовый класс — CRC, парсер
# ---------------------------------------------------------------------------
class _ModBus_Base:

    @staticmethod
    def CRC16(data: bytes) -> int:
        crc = 0xFFFF
        for byte in data:
            crc ^= byte
            for _ in range(8):
                odd = crc & 1
                crc >>= 1
                if odd:
                    crc ^= 0xA001
        return crc

    def parsing(self, pdu: bytes) -> Optional[dict]:
        """Разбор Modbus PDU (с адресом устройства первым байтом)."""
        if len(pdu) < 2:
            return None

        fc = pdu[1]
        # Exception
        if fc & 0x80:
            if len(pdu) >= 3:
                return {
                    'address': pdu[0],
                    'Err_code': fc,
                    'Exc_code': pdu[2]
                }
            return None

        # FC 0x01: Read Coils
        if fc == 0x01 and len(pdu) >= 3:
            byte_count = pdu[2]
            coils = []
            for i in range(byte_count):
                b = pdu[3 + i]
                for bit in range(8):
                    coils.append((b >> bit) & 1)
            return {
                'address': pdu[0],
                'code': fc,
                'nubrOfBytes': byte_count,
                'coils': coils
            }

        # FC 0x02: Read Discrete Inputs
        if fc == 0x02 and len(pdu) >= 3:
            byte_count = pdu[2]
            coils = []
            for i in range(byte_count):
                b = pdu[3 + i]
                for bit in range(8):
                    coils.append((b >> bit) & 1)
            return {
                'address': pdu[0],
                'code': fc,
                'nubrOfBytes': byte_count,
                'coils': coils
            }

        # FC 0x03: Read Holding Registers
        if fc == 0x03 and len(pdu) >= 3:
            byte_count = pdu[2]
            regs = []
            for i in range(0, byte_count, 2):
                regs.append(int.from_bytes(pdu[3+i:5+i], byteorder='big', signed=False))
            return {
                'address': pdu[0],
                'code': fc,
                'nubrOfCoils': byte_count // 2,
                'reg': regs
            }

        # FC 0x05: Write Single Coil
        if fc == 0x05 and len(pdu) == 6:
            coil = int.from_bytes(pdu[2:4], 'big')
            state = pdu[4] == 0xFF
            return {
                'address': pdu[0],
                'code': fc,
                'coilIndex': coil,
                'state': state
            }

        # FC 0x06: Write Single Register
        if fc == 0x06 and len(pdu) == 6:
            reg = int.from_bytes(pdu[2:4], 'big')
            value = int.from_bytes(pdu[4:6], 'big')
            return {
                'address': pdu[0],
                'code': fc,
                'regIndex': reg,
                'value': value
            }

        # FC 0x0F: Write Multiple Coils
        if fc == 0x0F and len(pdu) == 6:
            start = int.from_bytes(pdu[2:4], 'big')
            count = int.from_bytes(pdu[4:6], 'big')
            return {
                'address': pdu[0],
                'code': fc,
                'startCoil': start,
                'coilCount': count
            }

        # FC 0x10: Write Multiple Registers
        if fc == 0x10 and len(pdu) == 6:
            start = int.from_bytes(pdu[2:4], 'big')
            count = int.from_bytes(pdu[4:6], 'big')
            return {
                'address': pdu[0],
                'code': fc,
                'startReg': start,
                'regCount': count
            }

        return None


# ---------------------------------------------------------------------------
# Клиент для чистого Modbus TCP (MBAP)
# ---------------------------------------------------------------------------
class ModBus_TCP_Client(_ModBus_Base):
    """Клиент для стандартного Modbus TCP."""

    def __init__(self, ip: str = '192.168.0.83', port: int = 502, timeout: float = 5.0):
        self._ip = ip
        self._port = port
        self._timeout = timeout
        self._sock: Optional[socket.socket] = None
        self._transaction_id = 1
        self._connect()

    def _connect(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.settimeout(self._timeout)
        self._sock.connect((self._ip, self._port))

    def close(self):
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def is_connected(self) -> bool:
        return self._sock is not None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def request(self, pdu: list, parse: bool = True) -> Optional[dict]:
        """
        pdu: полный Modbus PDU включая адрес устройства (первый байт).
        """
        if not self._sock:
            raise ConnectionError("Нет подключения к Modbus TCP")

        full_pdu = bytes(pdu)
        unit_id = full_pdu[0]
        pdu_data = full_pdu[1:]   # функция и данные (без адреса)

        length = len(pdu_data) + 1
        mbap = struct.pack('>HHHB', self._transaction_id, 0, length, unit_id)
        self._transaction_id = (self._transaction_id + 1) % 65536

        packet = mbap + pdu_data
        try:
            self._sock.send(packet)

            mbap_resp = self._recv_exact(7)
            if not mbap_resp:
                raise TimeoutError("Нет ответа (MBAP заголовок)")

            _trans_id, _proto_id, resp_len, _resp_unit = struct.unpack('>HHHB', mbap_resp)
            pdu_length = resp_len - 1
            pdu_resp = self._recv_exact(pdu_length)
            if not pdu_resp:
                raise TimeoutError("Нет данных PDU")

            if parse:
                # Добавляем адрес устройства первым байтом для парсера
                pdu_with_addr = bytes([unit_id]) + pdu_resp
                return self.parsing(pdu_with_addr)
            return pdu_resp

        except socket.timeout:
            raise TimeoutError(f"Таймаут при обращении к {self._ip}:{self._port}")
        except Exception as e:
            raise ConnectionError(f"Ошибка Modbus TCP: {e}")

    def _recv_exact(self, size: int) -> Optional[bytes]:
        data = b''
        while len(data) < size:
            try:
                chunk = self._sock.recv(size - len(data))
                if not chunk:
                    return None
                data += chunk
            except socket.timeout:
                return None
        return data


# ---------------------------------------------------------------------------
# Socket клиент для RTU over TCP (с CRC)
# ---------------------------------------------------------------------------
class ModBus_Socket_Client(_ModBus_Base):
    def __init__(self, ip: str = '192.168.100.10', port: int = 8500, timeout: float = 1.0):
        self._ip = ip
        self._port = port
        self._timeout = timeout
        self._mbSocket: Optional[socket.socket] = None
        self._connect()

    def _connect(self):
        self._mbSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._mbSocket.settimeout(self._timeout)
        self._mbSocket.connect((self._ip, self._port))

    def close(self):
        if self._mbSocket:
            try:
                self._mbSocket.close()
            except Exception:
                pass
            self._mbSocket = None

    def is_connected(self) -> bool:
        return self._mbSocket is not None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def request(self, data: list, parse: bool = True) -> Optional[dict | bytes]:
        temp = data[:]
        crc = self.CRC16(bytes(temp))
        temp.append(crc & 0xFF)
        temp.append(crc >> 8 & 0xFF)
        try:
            self._mbSocket.send(bytearray(temp))
            answer = self._mbSocket.recv(256)
        except socket.timeout:
            raise TimeoutError(f'Modbus RTU over TCP: нет ответа от {self._ip}:{self._port}')
        except OSError as e:
            raise ConnectionError(f'Modbus RTU over TCP: ошибка соединения — {e}')
        if parse:
            return self.parsing(answer)
        return answer


# ---------------------------------------------------------------------------
# Serial (RTU) клиент
# ---------------------------------------------------------------------------
class ModBus_Serial_Client(_ModBus_Base):
    _FIXED_REPLY_LEN = {
        0x05: 8,
        0x06: 8,
        0x0F: 8,
        0x10: 8,
    }

    def __init__(self, Port: str = 'COM5', BauldRate: int = 115200):
        self._mbSerial = serial.Serial()
        self._mbSerial.port = Port
        self._mbSerial.baudrate = BauldRate
        self._mbSerial.stopbits = 1
        self._mbSerial.parity = serial.PARITY_NONE
        self._mbSerial.timeout = 1.0
        self._mbSerial.write_timeout = 1
        try:
            self._mbSerial.open()
        except serial.SerialException as e:
            raise ConnectionError(f'Modbus RTU: не удалось открыть {Port} — {e}')
        self._lenOfReq = 0

    def close(self):
        if self._mbSerial.is_open:
            try:
                self._mbSerial.close()
            except Exception:
                pass

    def is_connected(self) -> bool:
        return self._mbSerial.is_open

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _expected_reply_len(self, data: list) -> int:
        fc = data[1] if len(data) > 1 else 0x00
        if fc in (0x01, 0x02):
            bit_count = int.from_bytes(data[4:6], byteorder='big')
            return math.ceil(bit_count / 8) + 5
        if fc == 0x03:
            return int.from_bytes(data[4:6], byteorder='big') * 2 + 5
        return self._FIXED_REPLY_LEN.get(fc, 8)

    def sendData(self, data: list):
        temp = data[:]
        crc = self.CRC16(bytes(temp))
        temp.append(crc & 0xFF)
        temp.append(crc >> 8 & 0xFF)
        self._lenOfReq = self._expected_reply_len(data)
        if self._mbSerial.writable():
            self._mbSerial.write(bytearray(temp))

    def readData(self) -> bytes:
        if self._mbSerial.readable():
            return self._mbSerial.read_all()
        return b''

    def request(self, data: list, parse: bool = True) -> Optional[dict | bytes]:
        self.sendData(data)
        t0 = time.time() * 1000
        while (self._mbSerial.in_waiting < self._lenOfReq
               and time.time() * 1000 - t0 < 200):
            time.sleep(0.001)
        answer = self.readData()
        if not answer:
            raise TimeoutError(f'Modbus RTU: нет ответа (порт {self._mbSerial.port})')
        if parse:
            return self.parsing(answer)
        return answer


# ---------------------------------------------------------------------------
# Статический класс для построения команд
# ---------------------------------------------------------------------------
class ModBus_Cmd:
    @classmethod
    def readCoils(cls, modbusAdr: int, startAdr: int, coilNumber: int) -> list:
        return [modbusAdr, 0x01, startAdr >> 8, startAdr & 0xFF,
                coilNumber >> 8, coilNumber & 0xFF]

    @classmethod
    def readDiscreteInputs(cls, modbusAdr: int, startAdr: int, inpNumber: int) -> list:
        return [modbusAdr, 0x02, startAdr >> 8, startAdr & 0xFF,
                inpNumber >> 8, inpNumber & 0xFF]

    @classmethod
    def readHoldingRegisters(cls, modbusAdr: int, startReg: int, regNumber: int) -> list:
        return [modbusAdr, 0x03, startReg >> 8, startReg & 0xFF,
                regNumber >> 8, regNumber & 0xFF]

    @classmethod
    def writeSingleCoil(cls, modbusAdr: int, coilIndex: int, state: bool) -> list:
        value = 0xFF if state else 0x00
        return [modbusAdr, 0x05, coilIndex >> 8, coilIndex & 0xFF, value, 0x00]

    @classmethod
    def writeSingleRegister(cls, modbusAdr: int, regIndex: int, value: int) -> list:
        return [modbusAdr, 0x06, regIndex >> 8, regIndex & 0xFF,
                value >> 8, value & 0xFF]

    @classmethod
    def writeMultipleCoils(cls, modbusAdr: int, startCoilIndex: int,
                           valueArray: list) -> list:
        coilNumber = len(valueArray)
        byteCount = math.ceil(coilNumber / 8)
        req = [modbusAdr, 0x0F, startCoilIndex >> 8, startCoilIndex & 0xFF,
               coilNumber >> 8, coilNumber & 0xFF, byteCount]
        offset = 0
        remaining = coilNumber
        while remaining > 0:
            chunk = min(remaining, 8)
            value = 0
            for i in range(chunk):
                if valueArray[offset + i]:
                    value |= 1 << i
            req.append(value)
            offset += chunk
            remaining -= chunk
        return req

    @classmethod
    def writeMultipleRegisters(cls, modbusAdr: int, startReg: int,
                               values: list) -> list:
        regCount = len(values)
        byteCount = regCount * 2
        req = [modbusAdr, 0x10, startReg >> 8, startReg & 0xFF,
               regCount >> 8, regCount & 0xFF, byteCount]
        for v in values:
            req.append(v >> 8)
            req.append(v & 0xFF)
        return req