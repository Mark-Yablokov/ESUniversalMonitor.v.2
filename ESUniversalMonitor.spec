# ESUniversalMonitor.spec
#
# Сборка одного EXE файла (onefile, без консоли).
#
# Команда в терминале VSCode (из корня проекта):
#
#   pyinstaller ESUniversalMonitor.spec --clean
#
# Результат: dist/ESUniversalMonitor.exe
#
# Отладочная сборка (с окном консоли для вывода ошибок):
#
#   pyinstaller ESUniversalMonitor.spec --clean
#   (временно поставьте console=True ниже)
#
# Требования:
#   pip install pyinstaller
#   pip install -r requirements.txt
# ─────────────────────────────────────────────────────────────────────────────

import os
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

block_cipher = None

# ── Корень проекта ────────────────────────────────────────────────────────────
# Spec-файл лежит в корне проекта, поэтому SPECPATH == корень.

# ── Скрытые импорты ───────────────────────────────────────────────────────────
# PyInstaller не всегда находит динамические импорты PyQt5, pyqtgraph, pyvisa.
hidden_imports = [
    # PyQt5
    "PyQt5",
    "PyQt5.QtCore",
    "PyQt5.QtGui",
    "PyQt5.QtWidgets",
    "PyQt5.sip",

    # pyqtgraph
    "pyqtgraph",
    "pyqtgraph.graphicsItems",
    "pyqtgraph.graphicsItems.PlotItem",
    "pyqtgraph.graphicsItems.PlotDataItem",
    "pyqtgraph.graphicsItems.InfiniteLine",
    "pyqtgraph.graphicsItems.LegendItem",
    "pyqtgraph.widgets",
    "pyqtgraph.widgets.PlotWidget",

    # numpy
    "numpy",
    "numpy.core",

    # PyVISA (Rigol)
    "pyvisa",
    "pyvisa.resources",
    "pyvisa.highlevel",
    "pyvisa_py",
    "pyvisa_py.tcpip",
    "pyvisa_py.usb",
    "pyvisa_py.serial",

    # pyserial (COM-порты: Mantigora, PTS)
    "serial",
    "serial.tools",
    "serial.tools.list_ports",
    "serial.serialutil",
    "serial.serialwin32",   # Windows
    "serial.serialposix",   # Linux/macOS (на всякий случай)

    # Стандартные модули, которые иногда пропускаются
    "csv",
    "json",
    "struct",
    "threading",
    "dataclasses",
    "typing",
    "datetime",
    "logging",

    # Собственные пакеты проекта
    "core",
    "core.measurement_types",
    "drivers",
    "drivers.mantigora_driver",
    "drivers.pts_driver",
    "drivers.modBus",
    "generators",
    "generators.base_generator",
    "generators.pts_generator",
    "generators.mantigora_generator",
    "panels",
    "panels.base_device_panel",
    "panels.rigol_panel",
    "panels.pts_panel",
    "panels.modbus_panel",
    "panels.mantigora_panel",
    "tabs",
    "tabs.dashboard",
    "tabs.manual_generation_tab",
    "tabs.auto_test_tab",
    "utils",
    "utils.config_manager",
    "utils.history_manager",
]

# Добавляем все подмодули pyqtgraph автоматически
hidden_imports += collect_submodules("pyqtgraph")

# ── Дата-файлы (не-Python ресурсы) ───────────────────────────────────────────
datas = []

# pyqtgraph может использовать шейдеры / иконки
datas += collect_data_files("pyqtgraph")

# PyQt5 платформенные плагины (критично для Windows: platforms/qwindows.dll)
try:
    import PyQt5
    qt_dir = os.path.dirname(PyQt5.__file__)
    datas += [
        (os.path.join(qt_dir, "Qt5", "plugins", "platforms"),
         os.path.join("PyQt5", "Qt5", "plugins", "platforms")),
        (os.path.join(qt_dir, "Qt5", "plugins", "styles"),
         os.path.join("PyQt5", "Qt5", "plugins", "styles")),
    ]
except Exception:
    pass

# Если есть иконка приложения
_icon_path = os.path.join(SPECPATH, "icon.ico")

# ─────────────────────────────────────────────────────────────────────────────
a = Analysis(
    ["main.py"],                         # Точка входа
    pathex=[SPECPATH],                   # Корень проекта в sys.path
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Исключаем тяжёлые пакеты которые не нужны в runtime
        "matplotlib",
        "scipy",
        "pandas",
        "IPython",
        "notebook",
        "tkinter",
        "PIL",
        "cv2",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="ESUniversalMonitor",           # Имя EXE файла
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,                            # UPX сжатие (уменьшает размер ~30%)
    upx_exclude=[
        "qwindows.dll",                  # Qt плагины лучше не сжимать
        "vcruntime140.dll",
    ],
    runtime_tmpdir=None,
    console=False,                       # False = без чёрного окна консоли
                                         # True  = показать консоль (для отладки)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_icon_path if os.path.exists(_icon_path) else None,
    version_info=None,
)
