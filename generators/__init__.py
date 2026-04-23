"""
generators/__init__.py

Исправления (22.04.2026):
  - Добавлен импорт и регистрация MantigoraGenerator
"""

from generators.base_generator import BaseGenerator
from generators.pts_generator import PTSGenerator
from generators.mantigora_generator import MantigoraGenerator

# Реестр генераторов: тип → класс
# Добавляйте новые генераторы сюда
GENERATOR_REGISTRY = {
    PTSGenerator.GENERATOR_TYPE:    PTSGenerator,
    MantigoraGenerator.DEVICE_TYPE: MantigoraGenerator,
}

GENERATOR_TYPES = list(GENERATOR_REGISTRY.keys())


def create_generator(config: dict) -> BaseGenerator:
    """Создать генератор по конфигу. Raises KeyError если тип неизвестен."""
    gen_type = config.get("type", "")
    cls = GENERATOR_REGISTRY.get(gen_type)
    if cls is None:
        raise KeyError(f"Неизвестный тип генератора: '{gen_type}'")
    obj = cls()
    obj.apply_config(config)
    return obj
