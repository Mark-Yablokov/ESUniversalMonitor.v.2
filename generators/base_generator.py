# generators/base_generator.py

"""
Автор: m.yablokov
Базовый класс для всех генераторов сигналов.

Абстрактный контракт сведён к реальному минимуму, который оба генератора
(PTSGenerator, MantigoraGenerator) фактически реализуют.

Конкретные вспомогательные методы (apply_settings, enable_output) добавлены
для совместимости с вкладками UI — они делегируют вызовы к абстрактным методам.
"""

from abc import ABC, abstractmethod
from typing import List, Optional


class BaseGenerator(ABC):
    """
    Абстрактный базовый класс для управления источником сигнала.

    Наследники обязаны реализовать семь абстрактных методов/свойств.
    Остальные методы имеют реализацию по умолчанию.
    """

    # ── Обязательный интерфейс ────────────────────────────────────────────────

    @abstractmethod
    def connect(self, device_panels: Optional[List] = None) -> bool:
        """
        Подключиться к генератору.

        Args:
            device_panels: Опциональный список панелей устройств,
                           среди которых ищется связанный прибор.
        Returns:
            True при успешном подключении.
        """

    @abstractmethod
    def disconnect(self) -> None:
        """Отключиться от генератора."""

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """True, если генератор подключён и готов к работе."""

    @abstractmethod
    def set_point(self, setpoints: dict) -> None:
        """
        Применить набор уставок и активировать выход.

        Ключи словаря зависят от типа генератора:
          - PTSGenerator:       Ua, Ub, Uc, Ia, Ib, Ic, phi_a, phi_b, phi_c, f
          - MantigoraGenerator: U_output
        """

    @abstractmethod
    def output_off(self) -> None:
        """Выключить выход генератора."""

    @abstractmethod
    def get_config(self) -> dict:
        """Вернуть словарь с текущей конфигурацией генератора (для сохранения)."""

    @abstractmethod
    def apply_config(self, config: dict) -> None:
        """Загрузить конфигурацию из словаря."""

    # ── Опциональный интерфейс (реализации по умолчанию) ─────────────────────

    @property
    def channel_names(self) -> List[str]:
        """Список имён каналов генератора. Переопределяется в наследниках."""
        return []

    def apply_settings(self, settings: dict) -> None:
        """
        Удобная обёртка для вкладок UI.
        Передаёт словарь напрямую в set_point().
        """
        self.set_point(settings)

    def enable_output(self, enable: bool = True) -> None:
        """
        Включить / выключить выход.
        Включение управляется через set_point() — здесь обрабатывается только выключение.
        """
        if not enable:
            self.output_off()

    def get_info(self) -> dict:
        """Идентификационная информация о генераторе (переопределяется по желанию)."""
        return {"type": self.__class__.__name__, "connected": self.is_connected}
