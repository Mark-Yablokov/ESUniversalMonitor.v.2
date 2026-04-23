# core/measurement_types.py

"""
Общие типы данных для описания методик поверки и тестовых точек.
Эти структуры используются как в ручном, так и в автоматическом режиме.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any


@dataclass
class ToleranceSpec:
    """
    Спецификация допусков для измеряемой величины.
    """
    absolute: Optional[float] = None      # абсолютная погрешность (±)
    relative: Optional[float] = None      # относительная погрешность в %
    custom_formula: Optional[str] = None  # строка с формулой для сложных случаев

    def validate_value(self, measured: float, reference: float) -> bool:
        """
        Проверить, укладывается ли измеренное значение в допуск относительно эталонного.
        Возвращает True, если значение в допуске.
        """
        if self.absolute is not None:
            if abs(measured - reference) > self.absolute:
                return False
        if self.relative is not None:
            if reference != 0:
                rel_error = abs((measured - reference) / reference) * 100
                if rel_error > self.relative:
                    return False
        # Пользовательская формула пока не реализована
        return True


@dataclass
class ParameterLink:
    """
    Связь между параметром генерируемой величины и измеряемым каналом.
    Например: генератор выдаёт напряжение, измеряем канал 'U'.
    """
    generator_param: str      # 'voltage', 'current', 'frequency', 'power'
    measurement_channel: str  # имя канала в измерительном приборе


@dataclass
class TestPoint:
    """
    Одна тестовая точка в методике поверки.
    Содержит задаваемые значения и допуски.
    """
    name: str                           # имя точки, например "Uном Iном cos=1"
    setpoints: Dict[str, float]         # параметры установки: {'voltage': 220, 'current': 5, ...}
    tolerances: Dict[str, ToleranceSpec] = field(default_factory=dict)  # допуски по каналам
    wait_before_measure: float = 1.0    # время ожидания после установки режима, сек
    repeat_count: int = 1               # сколько измерений сделать в точке

    def to_dict(self) -> Dict[str, Any]:
        """Сериализация в словарь."""
        data = {
            'name': self.name,
            'setpoints': self.setpoints,
            'wait_before_measure': self.wait_before_measure,
            'repeat_count': self.repeat_count,
            'tolerances': {}
        }
        for ch, spec in self.tolerances.items():
            data['tolerances'][ch] = {
                'absolute': spec.absolute,
                'relative': spec.relative,
                'custom_formula': spec.custom_formula
            }
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'TestPoint':
        """Десериализация из словаря."""
        tolerances = {}
        for ch, spec_dict in data.get('tolerances', {}).items():
            tolerances[ch] = ToleranceSpec(
                absolute=spec_dict.get('absolute'),
                relative=spec_dict.get('relative'),
                custom_formula=spec_dict.get('custom_formula')
            )
        return cls(
            name=data['name'],
            setpoints=data['setpoints'],
            tolerances=tolerances,
            wait_before_measure=data.get('wait_before_measure', 1.0),
            repeat_count=data.get('repeat_count', 1)
        )