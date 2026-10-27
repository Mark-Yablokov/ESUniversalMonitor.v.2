"""
Автор: m.yablokov
panels/camera_panel.py

Панель климатической камеры SM-70/100-120 (контроллер SM-Klimat-SMk)
для UniversalMonitor. Связь с камерой — по OPC UA (библиотека opcua).

Что умеет:
  - читать температуру и влажность (+ текущие уставки) и отдавать их в Dashboard;
  - задавать уставки температуры и влажности;
  - команды ПУСК (F9), СТОП (F10), переключение режима влажности (F7);
  - сохранять / загружать настройки подключения в config/config_devices/<имя>.json.

Файл переписан с Tkinter-версии под PyQt5 и базовый класс panels.base_device_panel.
"""

import os
import threading
import traceback
from typing import Dict, Any, Optional

from opcua import Client as OPCUA_Client
from opcua import ua

from PyQt5.QtWidgets import (
    QLabel, QLineEdit, QCheckBox, QPushButton, QGroupBox,
    QHBoxLayout, QVBoxLayout, QDoubleSpinBox, QMessageBox, QWidget
)
from PyQt5.QtCore import QThread, pyqtSignal

from panels.base_device_panel import BaseDevicePanel


# ================== NodeId узлов OPC-сервера ==================
# Взяты из вашего explore_opc.py. Если сервер вернёт BadNodeIdUnknown,
# попробуйте добавить номер пространства имён: "ns=2;s=SM-70/..."
NODE_TEMP          = "s=SM-70/100-120.SM-Klimat-SMk.Парам.Тек темпер"
NODE_HUM           = "s=SM-70/100-120.SM-Klimat-SMk.Парам.ТЕК  ВЛАЖН"   # два пробела — так в сервере
NODE_SETPOINT_TEMP = "s=SM-70/100-120.SM-Klimat-SMk.Ввод.Уст температуры"
NODE_SETPOINT_HUM  = "s=SM-70/100-120.SM-Klimat-SMk.Ввод.Уст влажности"
NODE_F9_START      = "s=SM-70/100-120.SM-Klimat-SMk.Обслуж.F9 пуск"
NODE_F10_STOP      = "s=SM-70/100-120.SM-Klimat-SMk.Обслуж.F10"
NODE_F7_HUM        = "s=SM-70/100-120.SM-Klimat-SMk.Обслуж.F7 вл"

# Имена параметров, которые видят Dashboard / Испытания / Авто-испытание
PARAM_TEMP     = "Температура"
PARAM_HUM      = "Влажность"
PARAM_SP_TEMP  = "Уставка температуры"
PARAM_SP_HUM   = "Уставка влажности"

# Сколько ошибок чтения подряд считаем потерей связи
MAX_CONSECUTIVE_ERRORS = 3


# ================== Драйвер ==================
class CameraOPCDriver:
    """Тонкая обёртка над opcua.Client. Все обращения к серверу защищены Lock-ом,
    потому что читает поток опроса, а пишет GUI-поток."""

    def __init__(self, url: str, user: Optional[str] = None, password: Optional[str] = None):
        self.url = url
        self.user = user
        self.password = password
        self.client: Optional[OPCUA_Client] = None
        self.nodes: Dict[str, Any] = {}
        self.lock = threading.Lock()
        self.is_connected = False

    def connect(self):
        """Возвращает (успех, сообщение)."""
        try:
            self.client = OPCUA_Client(self.url, timeout=5)
            if self.user and self.password:
                self.client.set_user(self.user)
                self.client.set_password(self.password)
            self.client.connect()

            node_ids = {
                "temp": NODE_TEMP,
                "hum": NODE_HUM,
                "setpoint_temp": NODE_SETPOINT_TEMP,
                "setpoint_hum": NODE_SETPOINT_HUM,
                "f9": NODE_F9_START,
                "f10": NODE_F10_STOP,
                "f7": NODE_F7_HUM,
            }
            self.nodes = {name: self.client.get_node(nid) for name, nid in node_ids.items()}
            self.is_connected = True
            return True, self.url
        except Exception as e:
            self.is_connected = False
            self._safe_disconnect()
            return False, str(e)

    def _safe_disconnect(self):
        if self.client:
            try:
                self.client.disconnect()
            except Exception:
                pass
            self.client = None

    def disconnect(self):
        with self.lock:
            self._safe_disconnect()
            self.is_connected = False

    def read(self, name: str):
        with self.lock:
            return self.nodes[name].get_value()

    def read_all(self) -> Dict[str, float]:
        """Температура и влажность обязательны (ошибка -> исключение).
        Уставки читаем «по возможности»."""
        data: Dict[str, float] = {
            PARAM_TEMP: float(self.read("temp")),
            PARAM_HUM: float(self.read("hum")),
        }
        for key, pname in (("setpoint_temp", PARAM_SP_TEMP), ("setpoint_hum", PARAM_SP_HUM)):
            try:
                data[pname] = float(self.read(key))
            except Exception:
                pass
        return data

    def _detect_variant_type(self, node):
        """Определяем, каким типом писать в узел.
        1) берём тип текущего значения узла (самый надёжный способ);
        2) если не вышло — тип из атрибута DataType узла;
        3) иначе None (тогда сработает запасной перебор типов)."""
        try:
            vtype = node.get_data_value().Value.VariantType
            if vtype is not None:
                return vtype
        except Exception:
            pass
        try:
            return node.get_data_type_as_variant_type()
        except Exception:
            return None

    def write(self, name: str, value):
        """Запись с учётом типа узла (Boolean / Float / Int...).
        Если тип узнать не удалось — пробуем Float -> Boolean -> Int32."""
        node = self.nodes[name]
        with self.lock:
            vtype = self._detect_variant_type(node)

            if vtype is not None:
                tname = getattr(vtype, "name", str(vtype))
                if tname == "Boolean":
                    py_value = bool(value)
                elif tname in ("Float", "Double"):
                    py_value = float(value)
                else:
                    py_value = int(value)
                node.set_value(ua.Variant(py_value, vtype))
                return

            try:
                node.set_value(ua.Variant(float(value), ua.VariantType.Float))
            except Exception:
                try:
                    node.set_value(ua.Variant(bool(value), ua.VariantType.Boolean))
                except Exception:
                    node.set_value(ua.Variant(int(value), ua.VariantType.Int32))


# ================== Поток опроса ==================
class CameraPollingThread(QThread):
    new_values = pyqtSignal(dict)          # {имя_параметра: значение}
    error_occurred = pyqtSignal(str)       # разовая ошибка чтения
    connection_lost = pyqtSignal(str)      # несколько ошибок подряд

    def __init__(self, driver: CameraOPCDriver, get_interval):
        super().__init__()
        self.driver = driver
        self.get_interval = get_interval   # функция -> текущий интервал в секундах
        self.running = False

    def run(self):
        self.running = True
        errors = 0
        while self.running:
            try:
                data = self.driver.read_all()
                errors = 0
                self.new_values.emit(data)
            except Exception as e:
                errors += 1
                self.error_occurred.emit(f"Ошибка чтения ({errors}/{MAX_CONSECUTIVE_ERRORS}): {e}")
                if errors >= MAX_CONSECUTIVE_ERRORS:
                    self.running = False
                    self.connection_lost.emit(str(e))
                    break

            # ждём интервал маленькими шагами, чтобы быстро остановиться по stop()
            waited = 0
            total_ms = int(self.get_interval() * 1000)
            while self.running and waited < total_ms:
                self.msleep(100)
                waited += 100

    def stop(self):
        self.running = False
        self.wait(3000)


# ================== Панель устройства ==================
class CameraPanel(BaseDevicePanel):
    def __init__(self, device_name: str, parent=None):
        super().__init__("Camera", device_name, parent)
        self.driver: Optional[CameraOPCDriver] = None
        self.poll_thread: Optional[CameraPollingThread] = None
        self.hum_mode_state = False
        self._build_specific_ui()
        self._init_parameters()

    # ---------- UI ----------
    def _build_specific_ui(self):
        # --- Настройки подключения (левая колонка, QFormLayout из базового класса) ---
        self.url_edit = QLineEdit("opc.tcp://localhost:16664")
        self.add_setting_row("OPC URL:", self.url_edit)

        self.auth_check = QCheckBox("Требуется авторизация")
        self.auth_check.setChecked(True)
        self.auth_check.toggled.connect(self._on_auth_toggled)
        self.add_setting_widget(self.auth_check)

        self.user_edit = QLineEdit("admin")
        self.add_setting_row("Username:", self.user_edit)

        self.pass_edit = QLineEdit("1234")
        self.pass_edit.setEchoMode(QLineEdit.Password)
        self.add_setting_row("Password:", self.pass_edit)

        # --- Блок управления камерой (под таблицей значений, правая колонка) ---
        control_group = QGroupBox("Управление камерой")
        control_layout = QVBoxLayout(control_group)

        # Уставка температуры
        t_row = QHBoxLayout()
        t_row.addWidget(QLabel("Уставка температуры, °C:"))
        self.sp_temp_spin = QDoubleSpinBox()
        self.sp_temp_spin.setRange(-100.0, 300.0)
        self.sp_temp_spin.setDecimals(1)
        self.sp_temp_spin.setValue(20.0)
        t_row.addWidget(self.sp_temp_spin)
        self.sp_temp_btn = QPushButton("Задать")
        self.sp_temp_btn.clicked.connect(lambda: self._set_setpoint("setpoint_temp"))
        t_row.addWidget(self.sp_temp_btn)
        t_row.addStretch()
        control_layout.addLayout(t_row)

        # Уставка влажности
        h_row = QHBoxLayout()
        h_row.addWidget(QLabel("Уставка влажности, %:"))
        self.sp_hum_spin = QDoubleSpinBox()
        self.sp_hum_spin.setRange(0.0, 100.0)
        self.sp_hum_spin.setDecimals(1)
        self.sp_hum_spin.setValue(50.0)
        h_row.addWidget(self.sp_hum_spin)
        self.sp_hum_btn = QPushButton("Задать")
        self.sp_hum_btn.clicked.connect(lambda: self._set_setpoint("setpoint_hum"))
        h_row.addWidget(self.sp_hum_btn)
        h_row.addStretch()
        control_layout.addLayout(h_row)

        # Кнопки режимов
        mode_row = QHBoxLayout()
        self.start_mode_btn = QPushButton("▶ ПУСК")
        self.start_mode_btn.clicked.connect(self._start_mode)
        self.stop_mode_btn = QPushButton("■ СТОП")
        self.stop_mode_btn.clicked.connect(self._stop_mode)
        self.hum_mode_btn = QPushButton("💧 Влажность: ?")
        self.hum_mode_btn.clicked.connect(self._toggle_hum_mode)
        mode_row.addWidget(self.start_mode_btn)
        mode_row.addWidget(self.stop_mode_btn)
        mode_row.addWidget(self.hum_mode_btn)
        mode_row.addStretch()
        control_layout.addLayout(mode_row)

        # Добавляем блок в правую группу «Текущие значения» (под таблицу)
        self.values_group.layout().addWidget(control_group)

        self._set_controls_enabled(False)

    def _init_parameters(self):
        self.set_parameters({
            PARAM_TEMP:    {"unit": "°C"},
            PARAM_HUM:     {"unit": "%"},
            PARAM_SP_TEMP: {"unit": "°C"},
            PARAM_SP_HUM:  {"unit": "%"},
        })

    def _on_auth_toggled(self, checked: bool):
        self.user_edit.setEnabled(checked)
        self.pass_edit.setEnabled(checked)

    def _set_controls_enabled(self, enabled: bool):
        for w in (self.sp_temp_btn, self.sp_hum_btn, self.start_mode_btn,
                  self.stop_mode_btn, self.hum_mode_btn):
            w.setEnabled(enabled)

    def _update_hum_button(self):
        state = "ВКЛ" if self.hum_mode_state else "ВЫКЛ"
        self.hum_mode_btn.setText(f"💧 Влажность: {state}")
        # цвет кнопки: зелёный — включено, серый — выключено
        if self.hum_mode_state:
            self.hum_mode_btn.setStyleSheet("background-color: #3fb950; color: #0d1117;")
        else:
            self.hum_mode_btn.setStyleSheet("")

    # ---------- Подключение ----------
    def connect_device(self) -> bool:
        url = self.url_edit.text().strip()
        auth = self.auth_check.isChecked()
        user = self.user_edit.text() if auth else None
        password = self.pass_edit.text() if auth else None

        self.driver = CameraOPCDriver(url, user, password)
        success, info = self.driver.connect()
        if not success:
            self.log_event(f"Ошибка подключения: {info}")
            self.driver = None
            return False

        self.log_event(f"Подключено к OPC серверу {info}")

        # Подтягиваем текущие уставки в поля ввода
        try:
            self.sp_temp_spin.setValue(float(self.driver.read("setpoint_temp")))
            self.sp_hum_spin.setValue(float(self.driver.read("setpoint_hum")))
        except Exception as e:
            self._log_error("Не удалось прочитать уставки", e)

        # Текущее состояние режима влажности
        try:
            self.hum_mode_state = bool(self.driver.read("f7"))
        except Exception:
            self.hum_mode_state = False
        self._update_hum_button()

        self._set_controls_enabled(True)
        return True   # опрос запустит базовый _on_connect_clicked

    def disconnect_device(self):
        self.stop_polling()
        if self.driver:
            self.driver.disconnect()
            self.driver = None
        self._set_controls_enabled(False)

    # ---------- Опрос ----------
    def read_device_values(self) -> Dict[str, float]:
        # Не используется: опрос идёт в CameraPollingThread (как у Rigol)
        return {}

    def start_polling(self):
        if not self.is_connected or not self.driver:
            return
        if self.poll_thread and self.poll_thread.isRunning():
            return
        # интервал берём у панели при каждом цикле -> изменение спинбокса работает сразу
        self.poll_thread = CameraPollingThread(self.driver, lambda: self.poll_interval)
        self.poll_thread.new_values.connect(self._on_new_values)
        self.poll_thread.error_occurred.connect(self.log_event)
        self.poll_thread.connection_lost.connect(self._on_connection_lost)
        self.poll_thread.start()
        self.is_polling = True
        self.log_event("Опрос запущен")

    def stop_polling(self):
        was_running = self.poll_thread is not None
        if self.poll_thread:
            self.poll_thread.stop()
            self.poll_thread = None
        self.is_polling = False
        if was_running:
            self.log_event("Опрос остановлен")

    def _on_new_values(self, data: dict):
        # Выполняется в GUI-потоке (сигнал из QThread)
        for name, value in data.items():
            self.update_value_display(name, value)
            unit = self.parameters.get(name, {}).get("unit", "")
            self.data_updated.emit(self.device_name, name, value, unit)
        # В лог измерений пишем только измеряемые величины, без уставок
        self.log_measurement({k: v for k, v in data.items() if k in (PARAM_TEMP, PARAM_HUM)})

    def _on_connection_lost(self, err: str):
        self.log_event(f"Связь с камерой потеряна: {err}")
        self._on_disconnect_clicked()    # приводит кнопки и статус в «отключено»
        QMessageBox.warning(self, "Связь потеряна",
                            f"Камера не отвечает, соединение закрыто.\n{err}")

    # ---------- Управление ----------
    def _log_error(self, prefix: str, e: Exception):
        """Пишет ошибку в лог событий вместе с местом, где она произошла
        (файл:строка в последних 3 кадрах стека) — чтобы легче искать причину."""
        frames = traceback.extract_tb(e.__traceback__)[-3:]
        where = " <- ".join(f"{os.path.basename(f.filename)}:{f.lineno} {f.name}"
                            for f in reversed(frames))
        self.log_event(f"{prefix}: {type(e).__name__}: {e}  [{where}]")

    def _check_connected(self) -> bool:
        if not self.is_connected or not self.driver:
            self.log_event("Нет подключения")
            QMessageBox.warning(self, "Внимание", "Устройство не подключено")
            return False
        return True

    def _set_setpoint(self, node_name: str):
        if not self._check_connected():
            return
        try:
            if node_name == "setpoint_temp":
                value = self.sp_temp_spin.value()
                self.driver.write("setpoint_temp", value)
                self.log_event(f"Уставка температуры установлена: {value:.1f} °C")
            else:
                value = self.sp_hum_spin.value()
                self.driver.write("setpoint_hum", value)
                self.log_event(f"Уставка влажности установлена: {value:.1f} %")
        except Exception as e:
            self._log_error("Ошибка установки уставки", e)
            QMessageBox.critical(self, "Ошибка", f"Не удалось записать уставку:\n{e}")

    def _confirm(self, text: str) -> bool:
        answer = QMessageBox.question(self, "Подтверждение", text,
                                      QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        return answer == QMessageBox.Yes

    def _start_mode(self):
        if not self._check_connected():
            return
        if not self._confirm("Отправить команду ПУСК на камеру?"):
            return
        try:
            self.driver.write("f9", 1)
            self.log_event("Команда 'Пуск' отправлена")
        except Exception as e:
            self._log_error("Ошибка запуска", e)
            QMessageBox.critical(self, "Ошибка", f"Не удалось отправить команду:\n{e}")

    def _stop_mode(self):
        if not self._check_connected():
            return
        if not self._confirm("Отправить команду СТОП на камеру?"):
            return
        try:
            self.driver.write("f10", 1)
            self.log_event("Команда 'Стоп' отправлена")
        except Exception as e:
            self._log_error("Ошибка остановки", e)
            QMessageBox.critical(self, "Ошибка", f"Не удалось отправить команду:\n{e}")

    def _toggle_hum_mode(self):
        if not self._check_connected():
            return
        try:
            self.driver.write("f7", 1)
            self.hum_mode_state = not self.hum_mode_state
            self.log_event(f"Режим влажности переключён: {'ВКЛ' if self.hum_mode_state else 'ВЫКЛ'}")
            self._update_hum_button()
        except Exception as e:
            self._log_error("Ошибка переключения влажности", e)
            QMessageBox.critical(self, "Ошибка", f"Не удалось переключить режим:\n{e}")

    # ---------- Конфигурация ----------
    def get_config(self) -> Dict[str, Any]:
        config = super().get_config()      # type, name, poll_interval
        config.update({
            "opc_url": self.url_edit.text(),
            "auth_required": self.auth_check.isChecked(),
            "opc_user": self.user_edit.text(),
            "opc_pass": self.pass_edit.text(),
            "log_measurements_enabled": self.log_meas_check.isChecked(),
        })
        return config

    def apply_config(self, config: Dict[str, Any]):
        super().apply_config(config)       # poll_interval
        self.url_edit.setText(config.get("opc_url", "opc.tcp://localhost:16664"))
        self.auth_check.setChecked(config.get("auth_required", True))
        self.user_edit.setText(config.get("opc_user", "admin"))
        self.pass_edit.setText(config.get("opc_pass", "1234"))
        self.log_meas_check.setChecked(config.get("log_measurements_enabled", True))
        self._on_auth_toggled(self.auth_check.isChecked())