# generators/base_generator.py

"""
Базовый класс для всех генераторов сигналов.
Каждый генератор должен предоставлять унифицированные методы для задания пределов
и управления выходными параметрами тестового сигнала.
"""

from abc import ABC, abstractmethod
from typing import Optional, List


class BaseGenerator(ABC):
    """
    Абстрактный базовый класс, определяющий интерфейс управления
    измерительным генератором (источником сигнала).
    """

    @abstractmethod
    def connect(self, device_panels: Optional[List] = None) -> bool:
        """
        Подключиться к генератору. При необходимости может принимать
        список панелей устройств для поиска подходящего драйвера.

        Args:
            device_panels: Опциональный список панелей-устройств,
                           среди которых ищется связанный прибор.

        Returns:
            bool: True при успешном подключении, иначе False.
        """
        pass

    @abstractmethod
    def disconnect(self) -> None:
        """Отключиться от генератора."""
        pass

    @abstractmethod
    def is_connected(self) -> bool:
        """Вернуть True, если генератор подключен и готов к работе."""
        pass

    @abstractmethod
    def set_voltage(self, value: float) -> None:
        """Установить выходное напряжение (В)."""
        pass

    @abstractmethod
    def set_current(self, value: float) -> None:
        """Установить выходной ток (А)."""
        pass

    @abstractmethod
    def set_frequency(self, value: float) -> None:
        """Установить частоту выходного сигнала (Гц)."""
        pass

    @abstractmethod
    def set_phase(self, angle: float) -> None:
        """
        Установить угол фазового сдвига (градусы).
        Обычно применимо для многофазных или синхронизированных систем.
        """
        pass

    @abstractmethod
    def enable_output(self, enable: bool = True) -> None:
        """Включить или выключить выход генератора."""
        pass

    @abstractmethod
    def get_output_state(self) -> bool:
        """Вернуть True, если выход активен."""
        pass

    @abstractmethod
    def get_actual_voltage(self) -> float:
        """Считать текущее установленное/измеренное напряжение."""
        pass

    @abstractmethod
    def get_actual_current(self) -> float:
        """Считать текущий установленный/измеренный ток."""
        pass

    @abstractmethod
    def get_actual_frequency(self) -> float:
        """Считать текущую частоту."""
        pass

    @abstractmethod
    def apply_settings(self, settings: dict) -> None:
        """
        Применить набор параметров из словаря.
        Ключи: 'voltage', 'current', 'frequency', 'phase', 'output'.
        """
        pass

    @abstractmethod
    def get_info(self) -> dict:
        """Вернуть словарь с идентификационной информацией о генераторе."""
        pass