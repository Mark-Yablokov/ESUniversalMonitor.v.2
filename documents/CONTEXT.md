# ESUniversalMonitor – Контекст проекта

**Версия:** 0.4.0  
**Дата обновления:** 2026-04-24

---

## 1. Назначение системы

**ESUniversalMonitor** — десктопное приложение на базе **PyQt5** для автоматизации
поверки (калибровки) электросчётчиков и промышленных измерительных приборов.

Основные функции:
- Подключение к измерительным приборам и источникам сигналов через COM/LAN.
- Ручная установка параметров и единичные измерения.
- Автоматический прогон методик с проверкой допусков.
- Визуализация погрешностей на графике.
- Сохранение истории в CSV и методик в JSON.

---

## 2. Структура проекта (актуальная)

```
ESUniversalMonitor/
├── main.py                        # Точка входа
├── core/
│   ├── __init__.py
│   └── measurement_types.py       # TestPoint, ToleranceSpec, ParameterLink
├── generators/
│   ├── __init__.py                # GENERATOR_TYPES, create_generator (если реализовано)
│   ├── base_generator.py
│   ├── pts_generator.py
│   └── mantigora_generator.py
├── panels/
│   ├── __init__.py
│   ├── base_device_panel.py       # device_name, parameters, last_values, get_measurement()
│   ├── rigol_panel.py
│   ├── pts_panel.py
│   ├── modbus_panel.py
│   └── mantigora_panel.py         # v2.2 — минимум 10 В, 0 В = выкл.
├── tabs/
│   ├── __init__.py
│   ├── dashboard.py
│   ├── manual_generation_tab.py   # v4.0 — не блокирующий диалог, настраиваемый график
│   └── auto_test_tab.py           # v2.1 — GENERATOR_CONFIGS, динамические колонки
├── drivers/
│   ├── __init__.py
│   ├── modBus.py
│   ├── pts_driver.py
│   └── mantigora_driver.py        # v2.2 — big-endian ответ, baudrate 38400
├── utils/
│   ├── __init__.py
│   ├── config_manager.py
│   └── history_manager.py
├── documents/
│   └── CONTEXT.md                 # Этот файл
├── config_devices/                # JSON конфигурации устройств (runtime)
├── history/                       # CSV результатов (runtime)
├── methodology/                   # JSON методик поверки (runtime)
├── requirements.txt
└── ESUniversalMonitor.spec        # PyInstaller spec для сборки EXE
```

---

## 3. Поддерживаемое оборудование

| Устройство | Интерфейс | Класс панели |
|---|---|---|
| Rigol DM3068 (мультиметр) | LAN / PyVISA | `RigolPanel` |
| Modbus RTU/TCP | COM / TCP | `ModbusPanel` |
| MTE / EMH PTSx.xC (калибратор AC) | RS232 | `PTSPanel` |
| Mantigora HT/HP (ВВ источник DC) | RS232 / FTDI | `MantigoraPanel` |

---

## 4. Интерфейс панелей (BaseDevicePanel)

Все панели наследуют `BaseDevicePanel` и предоставляют:

```python
panel.device_name        # str — имя устройства (уникальное)
panel.parameters         # dict {param_name: {"unit": ...}} — заполняется при настройке
panel.last_values        # dict {param_name: float} — кэш последнего опроса
panel.is_connected       # bool
panel.get_measurement()  # dict — живые значения (для Mantigora — live read)
panel.get_device_id()    # str
```

`MantigoraPanel` дополнительно предоставляет:
```python
panel.set_voltage(v)     # установить напряжение (В); 0 = выкл; <10 В = ValueError
panel.output_on()        # включить выход
panel.output_off()       # выключить выход
```

---

## 5. Генераторы (GENERATOR_CONFIGS)

Реестр в `auto_test_tab.py`. Чтобы добавить новый генератор:

1. Добавить запись в `GENERATOR_CONFIGS`
2. Добавить страницу в `MethodologyDialog._build_generator_tab()` (QStackedWidget)
3. Добавить ветку в `_GeneratorPanelProxy.connect()` и `set_point()`

```python
GENERATOR_CONFIGS = {
    "PTS": {
        "setpoint_cols": [("Ua","Ua (В)"), ("Ia","Ia (А)"), ("f","f (Гц)"), ...],
    },
    "Mantigora": {
        "setpoint_cols": [("voltage","U (В)"), ("current_ma","I (мА)")],
    },
}
```

**Правило уставок Mantigora:** `voltage == 0` → `drv.stop()`, `0 < voltage < 10` → `drv.stop()`, `voltage ≥ 10` → нормальная работа.

---

## 6. Полная история изменений

### v0.1.0 — первоначальная структура (до 2026-04-22)

- Базовая PyQt5 архитектура: панели устройств, вкладки, драйверы.
- Поддержка Rigol DM3068, Modbus, PTS, Mantigora.
- Dashboard, ручной режим, авто-тест (базовый).

---

### v0.2.0 — исправление критических ошибок (2026-04-22 / 2026-04-23)

**Файл:** `generators/base_generator.py`
- Исправлена сигнатура `connect()`: добавлен опциональный параметр `device_panels`.

**Файл:** `panels/mantigora_panel.py`
- Исправлена передача `hp_mode` в конструктор `MantigoraDriver`.

**Файлы:** `tabs/manual_generation_tab.py`, `tabs/auto_test_tab.py`
- Устранено дублирование датаклассов: `ToleranceSpec`, `ParameterLink`, `TestPoint`
  вынесены в `core/measurement_types.py`.

**Новые файлы:**
- `core/__init__.py`, `core/measurement_types.py`
- `requirements.txt`

---

### v0.3.0 — исправление Mantigora и новые вкладки (2026-04-23 / 2026-04-24)

#### drivers/mantigora_driver.py → v2.2.0

**Баг:** устройство шлёт 16-битные коды в big-endian, код читал little-endian.  
Симптом: уставка 50 В → отображение 512.2 В (физически 50 В подтверждено мультиметром).

Математика: код 0x0640 → BE байты [0x06, 0x40] → LE чтение 0x4006 = 16390 → 16390/32 = 512.2 В.

**Исправление** в `read_measurement()`:
```python
# БЫЛО (неверно — little-endian):
i_code = data[0] | (data[1] << 8)
u_code = data[2] | (data[3] << 8)

# СТАЛО (верно — big-endian):
i_code = (data[0] << 8) | data[1]
u_code = (data[2] << 8) | data[3]
```

**Дополнительно:** baudrate изменён на 38400 для HT2000-P.

> Важно: SET-команда (0x01) остаётся little-endian — это подтверждено физически
> (50 В устанавливаются корректно). Протокол устройства асимметричен.

---

#### panels/mantigora_panel.py → v2.1 → v2.2

**v2.1** — переписана под новый API драйвера:
- Конструктор: `MantigoraDriver(port, voltage_kv=, power_w=)` (вместо `hp_mode=`, `model=`)
- `apply_settings()` заменён на `driver.set_voltage()` + `driver.set_current_limit()` + `driver.start()`
- `is_connected` — свойство, не метод (убраны лишние скобки)
- `query_command()` / `send_command()` заменены на `driver.read_measurement()`
- Единицы тока: мА (согласовано с драйвером)

**v2.2** — ограничение минимального напряжения:
- Константа `MANTIGORA_MIN_VOLTAGE = 10.0`
- `apply_output()`: 0 В → `stop()`, 1–9 В → предупреждение, ≥10 В → нормально
- `set_voltage()`: 0 В → `stop()`, 1–9 В → `ValueError`, ≥10 В → нормально
- Tooltip на спиннере напряжения

---

#### tabs/manual_generation_tab.py → v4.0.0

Полный рефакторинг вкладки ручных измерений.

**Проблема 1:** `QMessageBox.exec_()` блокировал главное окно — нельзя переключить
вкладку на генератор чтобы выставить сигнал.

**Решение:** `_InstructionBanner` — встроенная панель (оранжевая рамка) вместо
модального диалога. Показывает целевое значение + live показания эталона и
поверяемого (обновление каждые 600 мс). Не блокирует UI.

**Проблема 2:** Два фиксированных графика (Δ и δ%/γ%).

**Решение:** Один настраиваемый PlotWidget + QComboBox:
- `Δ — абсолютная погрешность`
- `δ% — относительная`
- `γ% — приведённая`
- `Сравнение: эталон + поверяемый`

**Проблема 3:** `_available_params()` использовал `panel.get_measurement()` — видел
только Mantigora (единственная панель с live-чтением). Modbus/Rigol/PTS не
появлялись пока не опрошены.

**Исправление:**
```python
# БЫЛО — только подключённые, с числовыми значениями:
dev_id = panel.get_device_id()
meas = panel.get_measurement()
if isinstance(val, (int, float)) and val is not None: ...

# СТАЛО — из panel.parameters (заполняется при настройке, до подключения):
dev_name = getattr(panel, 'device_name', None) or panel.get_device_id()
params = getattr(panel, 'parameters', {})
# Fallback на get_measurement() для Mantigora (нет panel.parameters)
```

**Аналогично `_read_scaled()`:**
```python
# Путь 1: panel.last_values (кэш потока опроса — Modbus, Rigol, PTS)
# Путь 2: panel.get_measurement() — fallback для Mantigora
```

**Прочие улучшения:**
- API адаптирован к стандарту `get_device_id()` / `get_measurement()`
- Режим графика сохраняется в JSON методики
- Кнопка «Пропустить точку» в InstructionBanner

---

#### tabs/auto_test_tab.py → v2.1.0

**Проблема:** нет возможности выбрать тип генератора — всё было захардкожено под PTS.

**Решение:**

1. `GENERATOR_CONFIGS` — реестр генераторов с колонками уставок:
   - PTS: Ua/Ub/Uc/Ia/Ib/Ic/φa/φb/φc/f
   - Mantigora: U (В) / I (мА)

2. Динамическая вкладка «Генератор» в `MethodologyDialog` (QStackedWidget):
   - При выборе PTS: порт + скорость
   - При выборе Mantigora: + макс. напряжение / мощность / серия
   - Скорость по умолчанию подставляется автоматически (PTS→19200, Mantigora→38400)

3. Динамические колонки таблицы точек: меняются при смене типа генератора.
   Метки и задержки сохраняются.

4. `_GeneratorPanelProxy` — fallback генератор (не требует изменений в `generators/`):
   - Ищет нужную панель среди подключённых по имени класса
   - Делегирует set_point() / output_off() через `panel.driver`

5. Обработка 0 В для Mantigora в `set_point()`:
   - `voltage == 0` или `voltage < 10` → `drv.stop()` (без записи на COM-порт)
   - `voltage ≥ 10` → нормальная работа

---

## 7. Сборка в EXE (PyInstaller)

### Требования

```bash
pip install pyinstaller
pip install -r requirements.txt
```

### Команда в терминале VSCode

```bash
pyinstaller ESUniversalMonitor.spec --clean
```

Результат: `dist/ESUniversalMonitor.exe` (single-file, без консоли).

### Отладочная сборка (с консолью для вывода ошибок)

```bash
pyinstaller ESUniversalMonitor.spec --clean -c
```

или временно поставить `console=True` в `.spec`.

### Структура после сборки

```
dist/
└── ESUniversalMonitor.exe   # единственный файл для распространения
```

Папки `methodology/`, `config_devices/`, `history/` создаются рядом с EXE
автоматически при первом запуске (если не существуют).

---

## 8. Зависимости (requirements.txt)

```
PyQt5>=5.15
pyqtgraph>=0.13
numpy>=1.24
pyvisa>=1.13
pyvisa-py>=0.7
pyserial>=3.5
pyinstaller>=6.0   # только для сборки
```

---

## 9. Как развернуть проект с нуля

```bash
git clone <repo>
cd ESUniversalMonitor
pip install -r requirements.txt
python main.py
```

При возникновении вопросов или необходимости дальнейших доработок —
используй этот контекст для быстрого восстановления картины проекта.
