# ESUniversalMonitor – Контекст проекта

**Версия:** 0.2.1  
**Дата обновления:** 2026-04-23  
**Репозиторий:** https://github.com/Mark-Yablokov/ESUniversalMonitor.v.2  

---

## 1. Назначение системы

**ESUniversalMonitor** — десктопное приложение на базе **PyQt5** для автоматизации поверки (калибровки) электросчётчиков и промышленных измерительных приборов.

**Основные функции:**

- Подключение к измерительным приборам (мультиметры, счётчики Modbus) и источникам испытательных сигналов (генераторы, высоковольтные источники).
- Ручная установка параметров тестового сигнала и выполнение единичных измерений.
- Автоматическое прохождение тестовых точек по заданной методике с проверкой допусков.
- Визуализация live-графиков (Dashboard) и сохранение истории в CSV.
- Загрузка методик поверки из JSON-файлов.
- Сохранение и восстановление конфигурации устройств между сессиями.

---

## 2. Структура проекта

```
ESUniversalMonitor/
├── main.py                     # Точка входа, главное окно (MainWindow)
├── requirements.txt            # Зависимости Python
│
├── core/                       # Общие структуры данных
│   ├── __init__.py
│   └── measurement_types.py    # TestPoint, ToleranceSpec, ParameterLink
│
├── generators/                 # Управление источниками сигналов
│   ├── __init__.py             # Экспортирует PTSGenerator, MantigoraGenerator
│   ├── base_generator.py       # Абстрактный базовый класс (7 методов)
│   ├── pts_generator.py        # Генератор PTS (RS232)
│   └── mantigora_generator.py  # Высоковольтный источник Mantigora (через панель)
│
├── panels/                     # Графические панели устройств (QWidget)
│   ├── __init__.py
│   ├── base_device_panel.py    # Базовый класс: polling, сигналы, get_config
│   ├── rigol_panel.py          # Rigol DM3068 (LAN / PyVISA)
│   ├── modbus_panel.py         # Универсальный Modbus RTU/TCP
│   ├── pts_panel.py            # Панель для генератора PTS
│   └── mantigora_panel.py      # Высоковольтный источник Mantigora HT/HP
│
├── tabs/                       # Основные вкладки главного окна
│   ├── __init__.py
│   ├── dashboard.py            # Live-графики и история
│   ├── manual_generation_tab.py # Ручной режим: генерация + измерение
│   └── auto_test_tab.py        # Автоматический прогон методик
│
├── drivers/                    # Низкоуровневые драйверы устройств
│   ├── __init__.py
│   ├── modBus.py               # Собственная реализация Modbus
│   ├── pts_driver.py           # Драйвер PTSx.xC (RS232)
│   └── mantigora_driver.py     # Драйвер Mantigora HT (RS232/USB-FTDI)
│
├── utils/                      # Вспомогательные утилиты
│   ├── __init__.py
│   ├── config_manager.py
│   └── history_manager.py
│
├── documents/
│   └── CONTEXT.md              # Этот файл
│
├── config/
│   └── config_devices/         # JSON-конфигурации устройств (runtime)
├── history/                    # CSV-файлы с измерениями (runtime)
└── methodology/                # JSON-файлы методик поверки (runtime)
```

---

## 3. Поддерживаемое оборудование

| Устройство | Тип | Интерфейс | Драйвер / библиотека |
|---|---|---|---|
| Rigol DM3068 | Мультиметр | LAN | PyVISA |
| Modbus-устройства | Счётчики, ПЛК | RTU / TCP | `drivers/modBus.py` |
| MTE / EMH PTSx.xC | Генератор эталонных сигналов | RS232 | `drivers/pts_driver.py` |
| Mantigora HT / HP | Высоковольтный источник | RS232 / USB-FTDI | `drivers/mantigora_driver.py` |

---

## 4. Архитектура — ключевые связи между модулями

### 4.1 Панели устройств (panels/)

Все панели наследуют `BaseDevicePanel`, который обеспечивает:
- Сигнал `data_updated(device_name, param_name, value, unit)` — для Dashboard и CSV.
- Атрибуты `device_type: str` и `device_name: str`.
- Методы `get_config() → dict` и `apply_config(config: dict)` — для сохранения/загрузки.
- Методы `start_polling()` / `stop_polling()` и абстрактный `_init_ui()`.

`MantigoraPanel` использует **QTimer** (а не поток BaseDevicePanel) для опроса — это безопаснее для COM-порта.

### 4.2 Генераторы (generators/)

`BaseGenerator` определяет контракт из **7 обязательных методов**:

| Метод | Назначение |
|---|---|
| `connect(device_panels=None) → bool` | Подключиться (найти нужную панель или COM-порт) |
| `disconnect() → None` | Отключиться |
| `is_connected → bool` | **`@property`** — состояние подключения |
| `set_point(setpoints: dict) → None` | Применить уставки и включить выход |
| `output_off() → None` | Выключить выход |
| `get_config() → dict` | Текущая конфигурация |
| `apply_config(config: dict) → None` | Загрузить конфигурацию |

Также есть **конкретные методы** (не абстрактные):
- `apply_settings(settings: dict)` — обёртка над `set_point()`, используется вкладками UI.
- `enable_output(enable: bool)` — обёртка над `output_off()`.

> ⚠️ **Важно:** `is_connected` — это `@property`, **не метод**. Вызывается без скобок:  
> `if generator.is_connected:` — правильно  
> `if generator.is_connected():` — **TypeError**

### 4.3 Драйвер Mantigora (drivers/mantigora_driver.py)

**Протокол:** бинарный через USB (FTDI виртуальный COM-порт).  
**Параметры порта:** 9600 бод, 8 бит, чётная чётность (Even), 1 стоп-бит.

**Команды:**

| Код | Назначение | Данные |
|---|---|---|
| `0x01` | Установить уставки | 2 байта U_code + 2 байта I_code (little-endian) |
| `0x02` | Включить / обновить выход | — |
| `0x03` | Выключить выход | — |
| `0x05` | Запросить измерение | Ответ: 5 байт `[I_low][I_high][U_low][U_high][0x0D]` |

**Конструктор:**
```python
MantigoraDriver(port="COM1", baudrate=9600, voltage_kv=2, power_w=6)
```
- `voltage_kv`: максимальное напряжение модуля в кВ — `2, 6, 10, 20, 30`
- `power_w`: мощность модуля в Вт — `6, 15, 60`

**Публичный API:**
```python
driver.connect() → bool          # Открыть порт, вернуть True
driver.disconnect() → None
driver.is_connected → bool       # @property

driver.set_voltage(voltage_v)    # Запомнить уставку U (В), не включает выход
driver.set_current_limit(ma)     # Запомнить уставку I (мА), не включает выход
driver.start()                   # Отправить [0x01 + коды] и [0x02]
driver.stop()                    # Отправить [0x03]
driver.read_measurement()        # Отправить [0x05], вернуть (voltage_v, current_ma)
driver.apply_setpoints()         # Только [0x01], без включения
```

**Коэффициенты пересчёта:**
```
KOD_U = U(В)   × KV     KOD_I = I(мкА) × KI

Модель   KV      KI_6W    KI_15W   KI_60W
2  кВ    32      21.33    8.533    2.133
6  кВ    10.67   64       25.6     6.4
10 кВ    6.4     106      42.4     10.6
20 кВ    3.2     213.3    85.32    21.33
30 кВ    2.133   320      128      32
```

### 4.4 MantigoraPanel → MantigoraGenerator (интерфейс)

`MantigoraGenerator.connect()` ищет среди `device_panels` панель с `device_type == "Mantigora HT"`.  
Затем управляет выходом через публичный API панели:

```python
panel.set_voltage(voltage_v)   # Установить уставку U
panel.output_on()              # Активировать выход (driver.start())
panel.output_off()             # Выключить выход (driver.stop())
```

### 4.5 main.py — управление устройствами

**Типы устройств** в диалоге добавления и в конфигах:

| Строка в UI | Ключ `type` в JSON | Класс панели |
|---|---|---|
| `"Rigol DM3068"` | `"Rigol"` | `RigolPanel` |
| `"Modbus"` | `"Modbus"` | `ModbusPanel` |
| `"PTS"` | `"PTS"` | `PTSPanel` |
| `"Mantigora HT"` | `"Mantigora HT"` | `MantigoraPanel` |

> При загрузке конфига принимаются оба варианта ключа: `"Mantigora"` (старый) и `"Mantigora HT"` (новый).

**Путь к конфигам:** `config/config_devices/<device_name>.json`  
**Путь к истории:** `history/history_YYYYMMDD.csv`  
**Путь к методикам:** `methodology/*.json`

---

## 5. Структуры данных (core/measurement_types.py)

### TestPoint
Описывает одну тестовую точку в методике:
```python
@dataclass
class TestPoint:
    name: str                           # Имя точки
    setpoints: dict                     # Уставки: {"voltage": 220.0, "current": 5.0, ...}
    tolerances: dict[str, ToleranceSpec]# Допуски по каналам
    wait_before_measure: float = 2.0    # Пауза перед измерением (с)
    repeat_count: int = 1               # Количество повторов
```

### ToleranceSpec
```python
@dataclass
class ToleranceSpec:
    absolute: Optional[float] = None   # Абсолютный допуск
    relative: Optional[float] = None   # Относительный допуск (%)

    def validate_value(self, measured, reference) -> bool: ...
```

### ParameterLink
Связь измеряемого канала с эталонным параметром (для автоматического сопоставления).

---

## 6. Ключи словаря setpoints по типу генератора

### PTSGenerator
```python
{
    "Ua": 220.0,   # Напряжение фазы A (В)
    "Ub": 220.0,   # Напряжение фазы B (В)
    "Uc": 220.0,   # Напряжение фазы C (В)
    "Ia": 5.0,     # Ток фазы A (А)
    "Ib": 5.0,     # Ток фазы B (А)
    "Ic": 5.0,     # Ток фазы C (А)
    "phi_a": 0.0,  # Угол фазы A (°)
    "phi_b": 120.0,
    "phi_c": 240.0,
    "f": 50.0      # Частота (Гц)
}
```

### MantigoraGenerator
```python
{
    "U_output": 1000.0   # Выходное напряжение (В)
}
```

---

## 7. История изменений

### v0.2.1 — 2026-04-23 (текущая)

Комплексный bugfix: исправлены все критические ошибки, не позволявшие запустить генераторы и подключиться к Mantigora.

#### generators/base_generator.py
- **Было:** 13 абстрактных методов (`set_voltage`, `set_current`, `get_actual_voltage` и др.), которые ни один наследник не реализовывал → `TypeError` при создании любого генератора.
- **Стало:** 7 абстрактных методов, отражающих реальный контракт. Добавлены конкретные методы `apply_settings()` (→ `set_point()`) и `enable_output()` (→ `output_off()`). `is_connected` переведён в `@property`.

#### drivers/mantigora_driver.py
- **Было:** `connect()` не возвращал ничего (`None`) → подключение всегда считалось неуспешным. Конструктор принимал `model` и `hp_mode` (несуществующие параметры).
- **Стало:** `connect()` возвращает `True`. Конструктор: `(port, baudrate, voltage_kv, power_w)`. Полностью переработан API: `set_voltage()`, `set_current_limit()`, `start()`, `stop()`, `read_measurement()`, `apply_setpoints()`. `is_connected` — `@property`. Исправлен порядок байт (U первым, I вторым) в команде `0x01`.

#### panels/mantigora_panel.py
- **Было:** конструктор `(config=None, parent)` — `device_type` получал имя вместо типа. Метод `_setup_ui()` не переопределял `_init_ui()` базового класса → UI никогда не строился. Драйвер создавался с несуществующими параметрами. Управление выходом через `send_command()` / `query_command()` — методов нет в драйвере.
- **Стало:** конструктор `(device_name, parent)`. Метод переименован в `_init_ui()`. Драйвер создаётся как `MantigoraDriver(port=port, voltage_kv=max_kv, power_w=power_w)`. Управление выходом: `driver.set_voltage()` / `driver.start()` / `driver.stop()` / `driver.read_measurement()`. Polling реализован через QTimer. Добавлены методы `set_voltage()`, `output_on()`, `output_off()` для `MantigoraGenerator`. Добавлены `get_config()` / `apply_config()`.

#### tabs/manual_generation_tab.py
- **Было:** `generator.is_connected()` — вызов property как метода → `TypeError`.
- **Стало:** `generator.is_connected` (без скобок).

#### tabs/auto_test_tab.py
- **Было:** `generator.is_connected()` → `TypeError`. `generator.apply_settings(point.setpoints)` вызывался в `AutoTestWorker`, но метод отсутствовал у генераторов.
- **Стало:** `generator.is_connected` (без скобок). `generator.set_point(point.setpoints)` (реальный метод генераторов).

#### main.py
- **Было:** `elif dev_type == "Mantigora":` — конфиги, сохранённые с `type="Mantigora HT"`, не загружались.
- **Стало:** `elif dev_type in ("Mantigora", "Mantigora HT"):` — принимаются оба варианта.

---

### v0.2.0 — 2026-04-21 (предыдущая)

- Добавлена вкладка «Авто-испытание» (`AutoTestTab`) с `AutoTestWorker` (QThread).
- Добавлена поддержка `PTSPanel` (тип `"PTS"`).
- Создан модуль `core/measurement_types.py` с общими структурами данных (`TestPoint`, `ToleranceSpec`, `ParameterLink`).
- Создан `requirements.txt`.
- Рефакторинг импортов после реструктуризации папок.
- Исправлены несоответствия имён типов в диалоге добавления устройства.

---

## 8. Запуск проекта

```bash
# 1. Установить зависимости
pip install -r requirements.txt

# 2. Запустить приложение
python main.py
```

**requirements.txt:**
```
PyQt5
pyqtgraph
numpy
pyvisa
pyvisa-py
pyserial
```

---

## 9. Паттерны для работы с кодом

### Добавление нового типа устройства

1. Создать `panels/my_device_panel.py`, унаследовав `BaseDevicePanel`.
2. Реализовать `_init_ui()`, `connect_device()`, `disconnect_device()`, `get_device_id()`, `get_measurement()`, `get_config()`, `apply_config()`.
3. Зарегистрировать тип в `main.py`:
   - Добавить строку в `available_types` в `_show_add_device_dialog()`.
   - Добавить `elif dev_type == "MyDevice":` в оба места: создание и загрузка конфига.

### Добавление нового генератора

1. Создать `generators/my_generator.py`, унаследовав `BaseGenerator`.
2. Реализовать все 7 абстрактных методов.
3. Добавить в `generators/__init__.py`.
4. Добавить тип в `ManualGenerationTab` и `AutoTestTab`.

### Создание методики поверки (JSON)

```json
{
  "name": "Поверка счётчика 220В 5А",
  "description": "...",
  "points": [
    {
      "name": "Номинальный режим",
      "setpoints": {"Ua": 220.0, "Ia": 5.0, "phi_a": 0.0, "f": 50.0},
      "tolerances": {
        "voltage": {"absolute": 0.5, "relative": 0.2},
        "current": {"absolute": 0.01, "relative": 0.2}
      },
      "wait_before_measure": 3.0,
      "repeat_count": 3
    }
  ]
}
```

---

## 10. Известные ограничения и планы

- **MantigoraGenerator.set_point()** поддерживает только ключ `"U_output"`. Ток-уставка берётся из текущего состояния панели.
- **Dashboard** строит графики для всех панелей, но выбор конкретного канала для отображения — в разработке.
- **Отчёты** автотеста сохраняются в упрощённом CSV. Полноценный PDF-отчёт — в планах.
- **Rigol DM3068** требует установленного NI-VISA или pyvisa-py + соответствующего бэкенда.
- Протокол Modbus — собственная реализация (`modBus.py`), сторонние библиотеки (`pymodbus`) не используются.
