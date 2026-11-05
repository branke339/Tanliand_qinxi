import sys
import os
import time
import json
import logging
from pathlib import Path
from filelock import FileLock, Timeout
from PyQt6.QtWidgets import QApplication, QMessageBox
from PyQt6.QtGui import QFont
from PyQt6.QtCore import QTimer, QThread, QObject, pyqtSignal

from common.safe import init_logger, install_global_hooks, logger, safe_slot
from common.state import state
from i18n.translator import I18n

from businessclass_lx import MesService
from ui.main_window import MainWindow
from common.safe import safe_slot, logger, debug_thread

init_logger()

# ★ 让底层通讯日志闭嘴
logging.getLogger("opcua").setLevel(logging.WARNING)
logging.getLogger("opcua.client").setLevel(logging.WARNING)

logger.info("========== 程序启动 ==========")


def resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)


def check_single_instance(lock_file_path: str = None) -> bool:
    if not lock_file_path:
        app_name = "plating_line_ms_v2"
        lock_file_path = str(Path.home() / f"{app_name}.lock")
    lock = FileLock(lock_file_path, timeout=0)
    try:
        lock.acquire()
        sys.single_instance_lock = lock
        return True
    except Timeout:
        return False


def load_style(app: QApplication):
    qss_path = resource_path("resources/qss/main.qss")
    if os.path.exists(qss_path):
        with open(qss_path, "r", encoding="utf-8") as f:
            app.setStyleSheet(f.read())


# ==========================================================
# ★ 全局 MES 后台心跳服务（带断线检测 + 自动重连）
# ==========================================================
class MesUploadThread(QThread):
    """后台上报设备状态的线程"""
    sig_result = pyqtSignal(bool, str)

    @debug_thread
    def __init__(self, url, payload_dict):
        super().__init__()
        self.url = url
        self.payload_dict = payload_dict

    def run(self):
        success, res, code, msg = MesService.upload_equipment_status(self.url, self.payload_dict)
        self.sig_result.emit(success, msg)


class MesReconnectThread(QThread):
    """后台自动重连的线程"""
    sig_result = pyqtSignal(bool, str)

    def __init__(self, url, eqpno):
        super().__init__()
        self.url = url
        self.eqpno = eqpno

    @debug_thread
    def run(self):
        success, res, code, msg = MesService.connect_mes(self.url, self.eqpno)
        self.sig_result.emit(success, msg)


class GlobalMesHeartbeat(QObject):
    """全局 MES 心跳控制器"""

    def __init__(self):
        super().__init__()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._check_heartbeat)
        self.last_upload_time = 0
        self.last_reconnect_time = 0  # 重连的时间控制
        self.upload_thread = None
        self.reconnect_thread = None
        self.fail_count = 0

    def start(self):
        self.timer.start(1000)  # 每秒检查一次
        logger.info("【全局 MES 心跳】服务已启动。")

    def _check_heartbeat(self):
        status = state.get("mes.status", "offline")

        if status == "online":
            self._handle_online_heartbeat()
        elif status == "offline" and state.get("mes.auto_reconnect") == "true":
            self._handle_auto_reconnect()

    def _handle_online_heartbeat(self):
        """处理在线状态下的心跳上报"""
        url = state.get("mes.url", "")
        eqpno = state.get("mes.eqpno", "")
        try:
            interval = int(state.get("mes.interval", "300"))
        except (ValueError, TypeError):
            interval = 300

        if not url or not eqpno:
            return

        now = time.time()

        if self.last_upload_time == 0:
            logger.info(f"【全局 MES 心跳】上线，触发首次上报！(周期: {interval} 秒)")
            self.last_upload_time = now
            self._do_sample_and_upload(url, eqpno, interval)
            return

        if (now - self.last_upload_time) >= interval:
            self.last_upload_time = now
            self._do_sample_and_upload(url, eqpno, interval)

    def _handle_auto_reconnect(self):
        """处理掉线状态下的自动重连"""
        url = state.get("mes.url", "")
        eqpno = state.get("mes.eqpno", "")
        if not url or not eqpno:
            return

        now = time.time()
        # 每 15 秒尝试重连一次，不要太频繁
        if now - self.last_reconnect_time >= 15:
            self.last_reconnect_time = now
            if self.reconnect_thread and self.reconnect_thread.isRunning():
                return

            logger.info("【全局 MES 心跳】检测到掉线，正在尝试自动重连...")
            self.reconnect_thread = MesReconnectThread(url, eqpno)
            self.reconnect_thread.sig_result.connect(self._on_reconnect_result)
            self.reconnect_thread.start()

    @safe_slot
    def _on_reconnect_result(self, success, msg):
        if success:
            logger.info("【全局 MES 心跳】✅ 自动重连成功！恢复在线状态。")
            state.set("mes.status", "online")
            self.fail_count = 0
            self.last_upload_time = 0  # 重置时间，重连成功后立刻发一条状态
        else:
            logger.warning(f"【全局 MES 心跳】❌ 自动重连失败: {msg}，15秒后重试。")

    def _do_sample_and_upload(self, url, eqpno, interval):
        if self.upload_thread and self.upload_thread.isRunning():
            return

        config_file = r"D:\Taniland_qingxi\equip_2.json"
        if not os.path.exists(config_file):
            logger.error(f"【全局 MES 心跳】找不到配置文件: {config_file}")
            return

        param_list = []
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                raw = f.read().strip()
                cleaned_raw = raw.replace('}\n{', '},{').replace('}\r\n{', '},{')
                valid_json = f"[{cleaned_raw}]"
                configs = json.loads(valid_json)

            for cfg in configs:
                p_val = state.get(cfg["P_Value"]) or ""
                p_lsl = state.get(cfg["P_LSL"]) or ""
                p_usl = state.get(cfg["P_USL"]) or ""

                if p_val == "init":
                    return

                param_list.append({
                    "P_Name": cfg["P_Name"],
                    "P_Value": str(p_val),
                    "P_LSL": str(p_lsl),
                    "P_USL": str(p_usl)
                })
        except Exception as e:
            logger.error(f"【全局 MES 心跳】解析配置文件失败: {e}")
            return

        payload = {
            "TransactionName": "EQPSTATUS",
            "EqpNo": eqpno,
            "UserID": state.get("app.user.current_workno", "Operator"),
            "CycleTime": "",
            "StatusCode": "0",
            "OutPutQty": "",
            "TotalOutPutQty": "",
            "Target_OutPutQty": "",
            "WONO": "",
            "ErrorCode": "0",
            "Availability": "100.0",
            "ParameterList": param_list
        }

        self.upload_thread = MesUploadThread(url, payload)
        self.upload_thread.sig_result.connect(self._on_upload_result)
        self.upload_thread.start()

    @safe_slot
    def _on_upload_result(self, success, msg):
        if success:
            self.fail_count = 0
        else:
            self.fail_count += 1
            logger.warning(f"【全局 MES 心跳】上报失败({self.fail_count}/3): {msg}")
            if self.fail_count >= 3:
                logger.error("【全局 MES 心跳】连续3次失败，判定为掉线！切换为离线状态并等待自动重连。")
                state.set("mes.status", "offline")
                state.set("mes.offline_reason", "auto")
                self.fail_count = 0
                self.last_upload_time = 0


def main():
    try:
        app = QApplication(sys.argv)
        app.setFont(QFont("Microsoft YaHei UI", 10))
        install_global_hooks()

        I18n.load_from_storage()

        if not check_single_instance():
            QMessageBox.warning(None, "提示", "程序已在运行中，无法重复打开！")
            return 0

        load_style(app)

        global_mes_heartbeat = GlobalMesHeartbeat()
        global_mes_heartbeat.start()

        try:
            win = MainWindow()
            win.global_mes_heartbeat = global_mes_heartbeat
            win.show()
        except Exception as e:
            logger.critical("主窗口创建失败", exc_info=True)
            QMessageBox.critical(None, "启动失败", f"主窗口创建失败：\n{e}")
            return 1

        code = app.exec()
        logger.info(f"========== 程序退出 code={code} ==========")
        return code
    except Exception as e:
        logger.critical("main() 异常", exc_info=True)
        try:
            QMessageBox.critical(None, "启动失败", f"{e}")
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())