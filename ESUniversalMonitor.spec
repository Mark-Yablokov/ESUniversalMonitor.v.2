# ESUniversalMonitor.spec
#
# Сборка одного EXE файла (onefile, без консоли).
#
# Команда:
#   pyinstaller ESUniversalMonitor.spec --clean
#
# Для отладки временно поставьте console=True ниже.
# ─────────────────────────────────────────────────────────────────────────────

import os, sys
from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

# ── Скрытые импорты ───────────────────────────────────────────────────────────
hidden_imports = [
    # PyQt5
    "PyQt5",
    "PyQt5.QtCore",
    "PyQt5.QtGui",
    "PyQt5.QtWidgets",
    "PyQt5.sip",

    # pyqtgraph – только то, что реально используется
    "pyqtgraph",
    "pyqtgraph.graphicsItems",
    "pyqtgraph.graphicsItems.PlotItem",
    "pyqtgraph.graphicsItems.PlotDataItem",
    "pyqtgraph.graphicsItems.InfiniteLine",
    "pyqtgraph.graphicsItems.LegendItem",
    "pyqtgraph.graphicsItems.LinearRegionItem",
    "pyqtgraph.widgets",
    "pyqtgraph.widgets.PlotWidget",
    "pyqtgraph.DateAxisItem",

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

    # pyserial (COM-порты)
    "serial",
    "serial.tools",
    "serial.tools.list_ports",
    "serial.serialutil",
    "serial.serialwin32",
    "serial.serialposix",

    # Стандартные модули
    "csv",
    "json",
    "struct",
    "threading",
    "dataclasses",
    "typing",
    "datetime",
    "logging",

    # Собственные пакеты
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

# ── Дата-файлы ───────────────────────────────────────────────────────────────
datas = []
datas += collect_data_files("pyqtgraph")

# Если есть иконка приложения
_icon_path = os.path.join(SPECPATH, "icon.ico")

# ── Исключения ───────────────────────────────────────────────────────────────
excludes = [
    "matplotlib",
    "scipy",
    "pandas",
    "IPython",
    "notebook",
    "tkinter",
    "PIL",
    "cv2",
    # Исключаем PyQt6, чтобы не было конфликта с PyQt5
    "PyQt6",
    "PyQt6.QtCore",
    "PyQt6.QtGui",
    "PyQt6.QtWidgets",
    # Неиспользуемый OpenGL-модуль pyqtgraph
    "pyqtgraph.opengl",
    # Все примеры pyqtgraph (чтобы не раздувать лог и EXE)
    "pyqtgraph.examples",
]

# ─────────────────────────────────────────────────────────────────────────────
a = Analysis(
    ["main.py"],
    pathex=[SPECPATH],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
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
    name="ESUniversalMonitor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[
        "qwindows.dll",
        "vcruntime140.dll",
    ],
    runtime_tmpdir=None,
    console=False,            # для готовой версии False, для отладки True
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_icon_path if os.path.exists(_icon_path) else None,
    version_info=None,
)