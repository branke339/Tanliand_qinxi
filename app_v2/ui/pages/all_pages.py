"""所有页面类集中在这里"""
import sys

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QFrame,
    QLineEdit, QPushButton, QTableWidget, QTreeWidget, QTreeWidgetItem,
    QListWidget, QComboBox, QMessageBox, QDialog, QHeaderView,
    QTableWidgetItem, QSizePolicy, QListWidgetItem, QAbstractItemView
)
from PyQt6.QtGui import QPixmap
from PyQt6.QtCore import Qt, QDateTime, QTime, QTimer, QThread, pyqtSignal, QRunnable, QThreadPool, QObject
from PyQt6 import QtWidgets
from argon2 import PasswordHasher

from ui.pages.base_page import BasePage
from ui.pages.placeholder_page import PlaceholderPage
from i18n.translator import I18n
from common.safe import safe_slot, logger, set_input_validator, require_permission
from common.state import state, bind_checkable, bind_lineedit

# UI 导入
from ui.pages.crane_status_ui import Ui_Form as CraneStatusUi
from ui.pages.conductivity_ui import Ui_Form as ConductivityUi
from ui.pages.alarm_ui import Ui_Form as AlarmUi
from ui.pages.water_ui import Ui_Form as WaterUi
from ui.pages.heater_ui import Ui_Form as HeaterUi
from ui.pages.drip_time_ui import Ui_Form as DripTimeUi
from ui.pages.process1_ui import Ui_Form as Process1Ui
from common.opc_service import OpcService
from ui.pages.depth_ui import Ui_Form as DepthUi
from ui.pages.temp_ui import Ui_Form as TempUi
from common.safe import safe_slot, logger, debug_thread

# 引入 PLC 字典
from config.plc_tags import PLC_TAGS

try:
    from businessclass_lx import Alarm_event_detail, export_xls, User, RolePermission, MesConfig
except Exception:
    logger.error("businessclass_lx 导入失败", exc_info=True)

_PH = PasswordHasher(time_cost=3, memory_cost=131072, parallelism=1)


# ============================================================
# 自定义控件 & 线程类
# ============================================================
class ClickableLabel(QLabel):
    """支持点击事件的 Label"""
    clicked = pyqtSignal()
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class CarPositionWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(90)
        self.current_slot = 1
        self.num_slots = 7

        self.car_label = QLabel(self)
        self.car_pixmap = QPixmap(r"D:\Taniland_qingxi\icon\diaocheA.png")
        if not self.car_pixmap.isNull():
            self.car_label.setPixmap(self.car_pixmap.scaledToHeight(45, Qt.TransformationMode.SmoothTransformation))
            self.car_label.setFixedSize(self.car_pixmap.width() * 45 // self.car_pixmap.height(), 45)
        else:
            self.car_label.setText("天车")
            self.car_label.setStyleSheet("background: orange; border: 1px solid black; padding: 2px;")
            self.car_label.setFixedSize(40, 30)

    def set_car_position(self, slot_index):
        if 1 <= slot_index <= self.num_slots:
            self.current_slot = slot_index
            self._update_position()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_position()

    def _update_position(self):
        slot_width = self.width() / self.num_slots
        center_x = (self.current_slot - 1) * slot_width + (slot_width / 2)
        car_x = int(center_x - self.car_label.width() / 2)
        self.car_label.move(car_x, 15)

    def paintEvent(self, event):
        from PyQt6.QtGui import QPainter, QPen
        painter = QPainter(self)
        painter.setPen(QPen(Qt.GlobalColor.gray, 1, Qt.PenStyle.DashLine))
        slot_width = self.width() / self.num_slots
        for i in range(self.num_slots):
            x = int(i * slot_width)
            painter.drawRect(x, 10, int(slot_width), 60)
        painter.end()


class AlarmWorkerSignals(QObject):
    finished = pyqtSignal(object)

class AlarmQueryWorker(QRunnable):
    def __init__(self, model, hours=24):
        super().__init__()
        self.model = model
        self.hours = hours
        self.signals = AlarmWorkerSignals()

    @debug_thread
    def run(self):
        try:
            import pydevd
            pydevd.settrace(suspend=False, trace_only_current_thread=True)
            if self.model:
                data = self.model.get_recent_alarms(hours=self.hours)
                self.signals.finished.emit(data)
            else:
                self.signals.finished.emit(None)
        except Exception as e:
            logger.error(f"报警后台查询线程发生异常: {e}")
            self.signals.finished.emit(None)


# ============================================================
# 页面 1：天车状态
# ============================================================
class CraneStatusPage(BasePage):
    title_key = "page.crane_status"
    def setup_ui(self):
        self.ui = CraneStatusUi()
        self.ui.setupUi(self)
        self._rebuild_layout()
        self._bind_state()
        I18n.on_changed(self._refresh)
        self._refresh()

    def _rebuild_layout(self):
        u = self.ui
        widgets = [
            u.label_car_a, u.label_manual, u.label_semi_auto, u.label_auto, u.label_auto_run,
            u.label_step, u.input_step, u.label_pos_now, u.input_pos_now, u.label_pos_next, u.input_pos_next,
            u.label_act_now, u.input_act_now, u.label_act_next, u.input_act_next, u.label_status, u.input_status,
            u.label_car_status, u.indicator1, u.indicator2, u.indicator3, u.indicator4,
            u.label_cycle_time, u.input_cycle_time, u.label_sec1,
            u.label_run_time, u.input_run_time, u.label_sec2,
            u.label_wait_time, u.input_wait_time, u.label_sec3,
            u.btn_cycle_reset, u.btn_auto_allow, u.btn_modify, u.btn_manual_btn, u.btn_auto_start,
        ]
        for w in widgets:
            w.setParent(None)
            w.hide()

        old = self.layout()
        if old is not None:
            QWidget().setLayout(old)
            old.deleteLater()

        main = QVBoxLayout(self)
        main.setContentsMargins(20, 20, 20, 20)
        main.setSpacing(16)

        def make_panel(layout):
            frame = QFrame()
            frame.setObjectName("panel_frame")
            layout.setContentsMargins(15, 15, 15, 15)
            frame.setLayout(layout)
            frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            return frame

        row_a = QHBoxLayout()
        row_a.setSpacing(16)
        for w in (u.label_car_a, u.label_manual, u.label_semi_auto, u.label_auto, u.label_auto_run):
            w.setMinimumSize(100, 40)
            row_a.addWidget(w)
        row_a.addStretch(1)
        main.addWidget(make_panel(row_a))

        row_bc = QHBoxLayout()
        row_bc.setSpacing(20)
        for lbl, inp in [(u.label_step, u.input_step), (u.label_pos_now, u.input_pos_now),
                         (u.label_pos_next, u.input_pos_next), (u.label_act_now, u.input_act_now),
                         (u.label_act_next, u.input_act_next), (u.label_status, u.input_status)]:
            blk = QVBoxLayout()
            blk.setSpacing(6)
            lbl.setMinimumSize(100, 40)
            inp.setMinimumSize(110, 45)
            blk.addWidget(lbl)
            blk.addWidget(inp)
            row_bc.addLayout(blk)
        row_bc.addStretch(1)

        col_c = QVBoxLayout()
        col_c.addWidget(u.label_car_status)
        ind_row = QHBoxLayout()
        for ind in (u.indicator1, u.indicator2, u.indicator3, u.indicator4):
            ind.setFixedSize(50, 50)
            ind_row.addWidget(ind)
        col_c.addLayout(ind_row)
        col_c.addStretch(1)
        row_bc.addLayout(col_c)
        main.addWidget(make_panel(row_bc))

        row_de = QHBoxLayout()
        grid_d = QGridLayout()
        grid_d.addWidget(u.label_cycle_time, 0, 0)
        grid_d.addWidget(u.input_cycle_time, 0, 1)
        grid_d.addWidget(u.label_sec1, 0, 2)
        grid_d.addWidget(u.label_run_time, 1, 0)
        grid_d.addWidget(u.input_run_time, 1, 1)
        grid_d.addWidget(u.label_sec2, 1, 2)
        grid_d.addWidget(u.label_wait_time, 2, 0)
        grid_d.addWidget(u.input_wait_time, 2, 1)
        grid_d.addWidget(u.label_sec3, 2, 2)
        for inp in (u.input_cycle_time, u.input_run_time, u.input_wait_time):
            inp.setFixedHeight(36)
            inp.setMinimumWidth(150)
            inp.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        row_de.addLayout(grid_d)
        row_de.addStretch(1)

        col_e = QVBoxLayout()
        col_e.setSpacing(12)
        for btn in (u.btn_cycle_reset, u.btn_auto_allow, u.btn_modify, u.btn_manual_btn, u.btn_auto_start):
            btn.setMinimumSize(140, 45)
            col_e.addWidget(btn)
        col_e.addStretch(1)
        row_de.addLayout(col_e)
        main.addWidget(make_panel(row_de))
        main.addStretch(1)
        for w in widgets:
            w.show()

    def _bind_state(self):
        u = self.ui
        u.input_cycle_time.setReadOnly(False)
        bind_lineedit(u.input_cycle_time, "crane.cycle_time")
        set_input_validator(u.input_cycle_time, "int", 0, 9999)
        u.input_run_time.setReadOnly(True)
        u.input_wait_time.setReadOnly(False)
        bind_lineedit(u.input_wait_time, "crane.wait_time")
        set_input_validator(u.input_wait_time, "int", 0, 9999)

    @safe_slot
    def _refresh(self, lang=None):
        u = self.ui
        u.label_car_a.setText(I18n.t("crane.car_a", "A车"))
        u.label_manual.setText(I18n.t("crane.manual", "手动"))
        u.label_semi_auto.setText(I18n.t("crane.semi_auto", "半自动"))
        u.label_auto.setText(I18n.t("crane.auto", "自动"))
        u.label_auto_run.setText(I18n.t("crane.auto_run", "自动运行"))
        u.label_step.setText(I18n.t("crane.step", "运行步数"))
        u.label_pos_now.setText(I18n.t("crane.pos_now", "目前位置"))
        u.label_pos_next.setText(I18n.t("crane.pos_next", "下个位置"))
        u.label_act_now.setText(I18n.t("crane.act_now", "目前动作"))
        u.label_act_next.setText(I18n.t("crane.act_next", "下个动作"))
        u.label_status.setText(I18n.t("crane.status", "目前状态"))
        u.label_car_status.setText(I18n.t("crane.car_status", "天车状态"))
        u.label_cycle_time.setText(I18n.t("crane.cycle_time", "周期时间设定"))
        u.label_run_time.setText(I18n.t("crane.run_time", "运行时间"))
        u.label_wait_time.setText(I18n.t("crane.wait_time", "确认上料等待时间"))
        u.label_sec1.setText(I18n.t("crane.sec", "秒"))
        u.label_sec2.setText(I18n.t("crane.sec", "秒"))
        u.label_sec3.setText(I18n.t("crane.sec", "秒"))
        u.btn_cycle_reset.setText(I18n.t("crane.btn_cycle_reset", "周期复位"))
        u.btn_auto_allow.setText(I18n.t("crane.btn_auto_allow", "自动允许"))
        u.btn_modify.setText(I18n.t("crane.btn_modify", "修止设置"))
        u.btn_manual_btn.setText(I18n.t("crane.btn_manual_btn", "手动按钮"))
        u.btn_auto_start.setText(I18n.t("crane.btn_auto_start", "自动开始"))


# ============================================================
# 页面 2：电导率/PH
# ============================================================
class ConductivityPage(BasePage):
    title_key = "page.conductivity/PH"

    def setup_ui(self):
        self.btn_save_slot2 = None
        self.btn_save_slot4 = None
        self.ui = ConductivityUi()
        self.ui.setupUi(self)
        self._rebuild_layout()
        I18n.on_changed(self._refresh)
        self._refresh()

        # ★ 只创建定时器，不在这里启动
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self._update_actual_values)

    def _rebuild_layout(self):
        u = self.ui

        # 1. 收集并隐藏 ConductivityUi 原本生成的控件（保留你的原逻辑）
        widgets = [
            u.label_slot2_title, u.label_slot2_unit_title, u.label_slot2_upper, u.label_slot2_unit_upper,
            u.input_slot2_upper,
            u.label_slot2_actual, u.label_slot2_unit_actual, u.input_slot2_actual, u.label_slot2_lower,
            u.label_slot2_unit_lower, u.input_slot2_lower,
            u.label_slot4_title, u.label_slot4_unit_title, u.label_slot4_upper, u.label_slot4_unit_upper,
            u.input_slot4_upper,
            u.label_slot4_actual, u.label_slot4_unit_actual, u.input_slot4_actual, u.label_slot4_lower,
            u.label_slot4_unit_lower, u.input_slot4_lower,
        ]
        for w in widgets:
            w.setParent(None)
            w.hide()

        old = self.layout()
        if old is not None:
            QWidget().setLayout(old)
            old.deleteLater()

        # 2. 开始构建新的卡片布局
        outer = QVBoxLayout(self)
        outer.setContentsMargins(30, 20, 30, 30)
        outer.setSpacing(20)

        main = QHBoxLayout()
        main.setSpacing(20)

        # 创建左右两个高颜值卡片
        left_card = self._make_card("slot2", "2#槽电导率", "μS/cm")
        right_card = self._make_card("slot4", "4#槽PH", "PH")

        main.addWidget(left_card, 1)
        main.addWidget(right_card, 1)
        outer.addLayout(main)
        outer.addStretch(1)

        # 3. 底部返回按钮（完全恢复你原本的写法）
        bottom_layout = QHBoxLayout()
        bottom_layout.addStretch(1)
        self.btn_back = QPushButton(I18n.t("btn.back", "< 返回生产记录"))
        self.btn_back.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_back.setFixedSize(140, 36)
        self.btn_back.setObjectName("ghostBtn")  # ★ 使用你原版的样式
        self.btn_back.clicked.connect(self._on_back_clicked)
        bottom_layout.addWidget(self.btn_back)
        outer.addLayout(bottom_layout)

        # 重新显示控件
        for w in widgets:
            w.show()

    def _make_card(self, prefix, title_text, unit_text):
        u = self.ui
        frame = QFrame()
        frame.setObjectName("card_frame")

        col_box = QVBoxLayout(frame)
        col_box.setContentsMargins(0, 0, 0, 15)
        col_box.setSpacing(15)

        # 顶部标题栏
        header_frame = QFrame()
        header_frame.setObjectName("card_header")
        header_box = QHBoxLayout(header_frame)
        header_box.setContentsMargins(15, 10, 15, 10)

        title_lbl = getattr(u, f"label_{prefix}_title")
        title_lbl.setText(title_text)
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_lbl.setObjectName("card_title")

        unit_lbl = getattr(u, f"label_{prefix}_unit_title")
        unit_lbl.setText(unit_text)
        unit_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        unit_lbl.setObjectName("card_unit_title")

        header_box.addWidget(title_lbl, 1)
        header_box.addWidget(unit_lbl, 0)
        col_box.addWidget(header_frame)

        inner_layout = QVBoxLayout()
        inner_layout.setContentsMargins(15, 5, 15, 0)
        inner_layout.setSpacing(12)
        col_box.addLayout(inner_layout)

        row_labels = {"upper": "上限值", "actual": "实际值", "lower": "下限值"}

        for rk in ("upper", "actual", "lower"):
            row_layout = QHBoxLayout()
            row_layout.setSpacing(8)

            lbl = getattr(u, f"label_{prefix}_{rk}")
            lbl.setText(row_labels[rk])
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setObjectName("card_row_label")

            unit2 = getattr(u, f"label_{prefix}_unit_{rk}")
            unit2.setText(unit_text)
            unit2.setAlignment(Qt.AlignmentFlag.AlignCenter)
            unit2.setObjectName("card_row_unit")

            inp = getattr(u, f"input_{prefix}_{rk}")
            inp.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            if rk == "actual":
                inp.setReadOnly(True)
                inp.setObjectName("card_input_actual")
            else:
                inp.setObjectName("card_input")

            row_layout.addWidget(lbl)
            row_layout.addWidget(unit2)
            row_layout.addWidget(inp)
            inner_layout.addLayout(row_layout)

        inner_layout.addSpacing(5)

        btn_save = QPushButton(I18n.t("btn.save_changes", "确定修改"))
        btn_save.setObjectName("primaryBtn")
        btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_save.clicked.connect(lambda: self.on_save_modify(prefix))
        setattr(self, f"btn_save_{prefix}", btn_save)

        inner_layout.addWidget(btn_save)
        inner_layout.addStretch(1)

        return frame

    @safe_slot
    def _on_back_clicked(self, *args):
        main_window = self.window()
        if hasattr(main_window, 'router'):
            main_window.router.go("monitor")

    @safe_slot
    def _update_actual_values(self):
        from common.state import state
        if not state.conn:
            return

        def get_val(addr):
            tag_info = PLC_TAGS.get("monitor", {}).get(addr)
            if not tag_info:
                return ""
            v = state.conn.hget("data_realtime", tag_info["redis"])
            if v is None or v in ("None", "NULL", "init", ""):
                return ""
            try:
                return f"{float(v):.2f}"
            except:
                return ""

        self.ui.input_slot2_actual.setText(get_val("PLC1.DD1.D4525"))
        self.ui.input_slot4_actual.setText(get_val("PLC1.DD1.D4530"))

    @safe_slot
    @require_permission("conductivity.edit", "修改电导率参数")
    def on_save_modify(self, prefix):
        if prefix == "slot2":
            upper_val = self.ui.input_slot2_upper.text().strip()
            lower_val = self.ui.input_slot2_lower.text().strip()
            target_name = "2#槽电导率"
            plc_upper_key = "PLC1.DD1.D4526"
            plc_lower_key = "PLC1.DD1.D4527"
        else:
            upper_val = self.ui.input_slot4_upper.text().strip()
            lower_val = self.ui.input_slot4_lower.text().strip()
            target_name = "4#槽PH"
            plc_upper_key = "PLC1.DD1.D4531"
            plc_lower_key = "PLC1.DD1.D4532"

        if not upper_val or not lower_val:
            return QMessageBox.warning(self, "提示", "上限值和下限值不能为空！")

        reply = QMessageBox.question(self, "确认修改",
                                     f"确定要将【{target_name}】的上限设为 {upper_val}，下限设为 {lower_val} 并下发到 PLC 吗？",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.No:
            return

        try:
            opc = OpcService()
            opc.write_tag("monitor", plc_upper_key, float(upper_val), task_name="cond_edit")
            opc.write_tag("monitor", plc_lower_key, float(lower_val), task_name="cond_edit")
            QMessageBox.information(self, "指令已提交", f"【{target_name}】修改指令已下发至后台。")
            logger.info(f"用户修改了 {target_name}: 上限={upper_val}, 下限={lower_val}")
        except Exception as e:
            logger.error(f"下发 PLC 失败: {e}")
            QMessageBox.critical(self, "失败", "下发 PLC 触发异常，请检查 OPC 连接！")

    @safe_slot
    def _refresh(self, lang=None):
        # 更新标题、按钮等文字
        u = self.ui
        u.label_slot2_title.setText(I18n.t("cond.slot2_title", "2#槽电导率"))
        u.label_slot4_title.setText(I18n.t("cond.slot4_title", "4#槽PH"))
        u.label_slot2_upper.setText(I18n.t("cond.upper", "上限值"))
        u.label_slot2_actual.setText(I18n.t("cond.actual", "实际值"))
        u.label_slot2_lower.setText(I18n.t("cond.lower", "下限值"))
        u.label_slot4_upper.setText(I18n.t("cond.upper", "上限值"))
        u.label_slot4_actual.setText(I18n.t("cond.actual", "实际值"))
        u.label_slot4_lower.setText(I18n.t("cond.lower", "下限值"))

        if hasattr(self, 'btn_back'):
            self.btn_back.setText(I18n.t("btn.back", "< 返回生产记录"))
        if hasattr(self, 'btn_save_slot2'):
            self.btn_save_slot2.setText(I18n.t("btn.save_changes", "确定修改"))
        if hasattr(self, 'btn_save_slot4'):
            self.btn_save_slot4.setText(I18n.t("btn.save_changes", "确定修改"))


    def on_leave(self):
        # ★ 离开时停止定时器
        if hasattr(self, 'refresh_timer') and self.refresh_timer.isActive():
            self.refresh_timer.stop()

    def on_enter(self):
        if hasattr(self, 'refresh_timer') and not self.refresh_timer.isActive():
            self.refresh_timer.start(1000)

        self.apply_button_permissions({
            self.btn_save_slot2: "conductivity.edit",
            self.btn_save_slot4: "conductivity.edit",
        })


# ============================================================
# 页面 3：补水
# ============================================================
class WaterPage(BasePage):
    title_key = "page.water"
    SLOTS = ("2", "4", "5", "7")
    ROWS = ("upper", "actual", "lower")

    def setup_ui(self):
        self.ui = WaterUi()
        self.ui.setupUi(self)
        self._build_layout()
        self._bind_events()
        I18n.on_changed(self._refresh)
        self._refresh()

        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self._update_realtime_data)

    def _build_layout(self):
        u = self.ui
        ghost_names = ["label_unit_title", "label_row_upper", "label_row_actual", "label_row_lower",
                       "label_col_slot2", "label_col_slot4", "label_col_slot5", "label_col_slot7"]
        for name in ghost_names:
            w = getattr(u, name, None)
            if w:
                w.hide()
                w.deleteLater()

        old = self.layout()
        if old is not None:
            QWidget().setLayout(old)
            old.deleteLater()

        main = QVBoxLayout(self)
        main.setContentsMargins(30, 20, 30, 30)
        main.setSpacing(20)

        grid_layout = QGridLayout()
        grid_layout.setSpacing(20)

        for i, slot in enumerate(self.SLOTS):
            card = self._make_card(slot)
            row = i // 2
            col = i % 2
            grid_layout.addWidget(card, row, col)

        main.addLayout(grid_layout)
        main.addStretch(1)

        bottom_layout = QHBoxLayout()
        bottom_layout.addStretch(1)
        self.btn_back = QPushButton(I18n.t("btn.back", "< 返回生产记录"))
        self.btn_back.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_back.setFixedSize(140, 36)
        self.btn_back.setObjectName("ghostBtn")
        self.btn_back.clicked.connect(self._on_back_clicked)
        bottom_layout.addWidget(self.btn_back)
        main.addLayout(bottom_layout)

    def _make_card(self, slot):
        u = self.ui
        frame = QFrame()
        frame.setObjectName("card_frame")

        col_box = QVBoxLayout(frame)
        col_box.setContentsMargins(0, 0, 0, 15)
        col_box.setSpacing(15)

        header_frame = QFrame()
        header_frame.setObjectName("card_header")
        header_box = QHBoxLayout(header_frame)
        header_box.setContentsMargins(15, 10, 15, 10)

        title_lbl = getattr(u, f"label_card_slot{slot}_title")
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_lbl.setObjectName("card_title")

        unit_lbl = QLabel("L/min")
        unit_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        unit_lbl.setObjectName("card_unit_title")

        header_box.addWidget(title_lbl, 1)
        header_box.addWidget(unit_lbl, 0)
        col_box.addWidget(header_frame)

        inner_layout = QVBoxLayout()
        inner_layout.setContentsMargins(15, 5, 15, 0)
        inner_layout.setSpacing(12)
        col_box.addLayout(inner_layout)

        btn_m = getattr(u, f"btn_card_slot{slot}_manual")
        btn_c = getattr(u, f"btn_card_slot{slot}_close")
        btn_m.setCheckable(False)
        btn_c.setCheckable(False)
        inner_layout.addWidget(btn_m)
        inner_layout.addWidget(btn_c)

        row_labels = {"upper": I18n.t("cond.upper", "上限值"),
                      "actual": I18n.t("cond.actual", "实际值"),
                      "lower": I18n.t("cond.lower", "下限值")}

        for rk in self.ROWS:
            row_layout = QHBoxLayout()
            row_layout.setSpacing(8)

            lbl = QLabel(row_labels[rk])
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setObjectName("card_row_label")

            unit2 = QLabel("L/min")
            unit2.setAlignment(Qt.AlignmentFlag.AlignCenter)
            unit2.setObjectName("card_row_unit")

            inp = getattr(u, f"input_slot{slot}_{rk}")
            inp.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            if rk == "actual":
                inp.setReadOnly(True)
                inp.setObjectName("card_input_actual")
            else:
                inp.setObjectName("card_input")

            row_layout.addWidget(lbl)
            row_layout.addWidget(unit2)
            row_layout.addWidget(inp)
            inner_layout.addLayout(row_layout)

        inner_layout.addSpacing(5)

        btn_save = QPushButton(I18n.t("btn.save_changes", "确定修改"))
        btn_save.setObjectName("primaryBtn")
        btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_save.clicked.connect(lambda checked, s=slot: self.on_save_modify(s))
        setattr(self, f"btn_save_slot{slot}", btn_save)
        inner_layout.addWidget(btn_save)

        inner_layout.addStretch(1)
        return frame

    def _bind_events(self):
        for slot in self.SLOTS:
            m = getattr(self.ui, f"btn_card_slot{slot}_manual")
            c = getattr(self.ui, f"btn_card_slot{slot}_close")
            m.clicked.connect(lambda checked, s=slot: self.on_manual_clicked(s))
            c.clicked.connect(lambda checked, s=slot: self.on_close_clicked(s))

    @safe_slot
    def _on_back_clicked(self, *args):
        main_window = self.window()
        if hasattr(main_window, 'router'):
            main_window.router.go("monitor")

    @safe_slot
    def _update_realtime_data(self):
        from common.state import state
        if not state.conn:
            return

        def get_float(tag_cat, addr):
            tag_info = PLC_TAGS.get(tag_cat, {}).get(addr)
            if not tag_info:
                return None
            v = state.conn.hget("data_realtime", tag_info["redis"])
            if v is None or v in ("None", "NULL", "init", ""):
                return None
            try:
                return float(v)
            except:
                return None

        manual_map = {"2": "PLC1.DD1.M730", "4": "PLC1.DD1.M732", "5": "PLC1.DD1.M731", "7": "PLC1.DD1.M733"}
        close_map = {"2": "PLC1.DD1.M740", "4": "PLC1.DD1.M742", "5": "PLC1.DD1.M741", "7": "PLC1.DD1.M743"}
        flow_map = {"2": "PLC1.DD1.D4505", "4": "PLC1.DD1.D4510", "5": "PLC1.DD1.D4515", "7": "PLC1.DD1.D4520"}

        for slot in self.SLOTS:
            actual_val = get_float("monitor", flow_map[slot])
            if actual_val is not None:
                actual_str = f"{actual_val:.2f}"
            else:
                actual_str = ""
            getattr(self.ui, f"input_slot{slot}_actual").setText(actual_str)

            val_m = get_float("water", manual_map[slot])
            btn_m = getattr(self.ui, f"btn_card_slot{slot}_manual")
            if val_m is not None:
                if val_m == 1:
                    btn_m.setText(I18n.t("water.btn_auto", "自动"))
                    btn_m.setStyleSheet(
                        "background-color: #3b82f6; color: white; border-radius: 4px; font-weight: bold; border: none;")
                else:
                    btn_m.setText(I18n.t("water.btn_manual", "手动"))
                    btn_m.setStyleSheet("")  # 恢复给你的 QSS 控制

            val_c = get_float("water", close_map[slot])
            btn_c = getattr(self.ui, f"btn_card_slot{slot}_close")
            if val_c is not None:
                if val_c == 1:
                    btn_c.setText(I18n.t("water.btn_open", "开启"))
                    btn_c.setStyleSheet(
                        "background-color: #22c55e; color: white; border-radius: 4px; font-weight: bold; border: none;")
                else:
                    btn_c.setText(I18n.t("water.btn_close", "关闭"))
                    btn_c.setStyleSheet("")

    @safe_slot
    @require_permission("water.control", "控制补水手动/自动")
    def on_manual_clicked(self, slot):
        manual_map = {"2": "PLC1.DD1.M730", "4": "PLC1.DD1.M732", "5": "PLC1.DD1.M731", "7": "PLC1.DD1.M733"}
        addr = manual_map[slot]
        tag_info = PLC_TAGS.get("water", {}).get(addr)
        if not tag_info:
            return
        if state.conn:
            val = state.conn.hget("data_realtime", tag_info["redis"])
        else:
            val = None
        if val and val not in ("None", "NULL"):
            current_val = int(float(val))
        else:
            current_val = 0
        if current_val == 1:
            new_val = 0
        else:
            new_val = 1
        try:
            OpcService().write_tag("water", addr, new_val, task_name="water_manual")
        except Exception as e:
            QMessageBox.critical(self, "失败", f"下发失败: {e}")

    @safe_slot
    @require_permission("water.control", "控制补水开启/关闭")
    def on_close_clicked(self, slot):
        close_map = {"2": "PLC1.DD1.M740", "4": "PLC1.DD1.M742", "5": "PLC1.DD1.M741", "7": "PLC1.DD1.M743"}
        addr = close_map[slot]
        tag_info = PLC_TAGS.get("water", {}).get(addr)
        if not tag_info:
            return
        if state.conn:
            val = state.conn.hget("data_realtime", tag_info["redis"])
        else:
            val = None
        if val and val not in ("None", "NULL"):
            current_val = int(float(val))
        else:
            current_val = 0
        if current_val == 1:
            new_val = 0
        else:
            new_val = 1
        try:
            OpcService().write_tag("water", addr, new_val, task_name="water_close")
        except Exception as e:
            QMessageBox.critical(self, "失败", f"下发失败: {e}")

    @safe_slot
    @require_permission("water.edit", "修改补水参数")
    def on_save_modify(self, slot, *args):
        upper_val = getattr(self.ui, f"input_slot{slot}_upper").text().strip()
        lower_val = getattr(self.ui, f"input_slot{slot}_lower").text().strip()
        if not upper_val or not lower_val:
            return QMessageBox.warning(self, "提示", f"【{slot}#补水】的上限值和下限值不能为空！")

        reply = QMessageBox.question(self, "确认修改", f"确定下发【{slot}#补水】的上下限吗？",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.No:
            return

        upper_map = {"2": "PLC1.DD1.D4506", "4": "PLC1.DD1.D4511", "5": "PLC1.DD1.D4516", "7": "PLC1.DD1.D4521"}
        lower_map = {"2": "PLC1.DD1.D4507", "4": "PLC1.DD1.D4512", "5": "PLC1.DD1.D4517", "7": "PLC1.DD1.D4522"}
        try:
            opc = OpcService()
            opc.write_tag("monitor", upper_map[slot], float(upper_val), task_name="water_edit")
            opc.write_tag("monitor", lower_map[slot], float(lower_val), task_name="water_edit")
            QMessageBox.information(self, "指令已提交", f"【{slot}#补水】修改指令已下发至后台。")
        except Exception as e:
            QMessageBox.critical(self, "失败", f"下发 PLC 失败: {e}")

    @safe_slot
    def _refresh(self, lang=None):
        u = self.ui
        for slot in self.SLOTS:
            getattr(u, f"label_card_slot{slot}_title").setText(I18n.t(f"water.title_slot{slot}", f"{slot}#补水"))
            getattr(self, f"btn_save_slot{slot}").setText(I18n.t("btn.save_changes", "确定修改"))
            getattr(u, f"btn_card_slot{slot}_manual").setText(I18n.t("water.btn_manual", "手动"))
            getattr(u, f"btn_card_slot{slot}_close").setText(I18n.t("water.btn_close", "关闭"))


        if hasattr(self, 'btn_back'):
            self.btn_back.setText(I18n.t("btn.back", "< 返回生产记录"))
        self._update_realtime_data()

    def on_leave(self):
        if hasattr(self, 'refresh_timer') and self.refresh_timer.isActive():
            self.refresh_timer.stop()

    def on_enter(self):
        if hasattr(self, 'refresh_timer') and not self.refresh_timer.isActive():
            self.refresh_timer.start(1000)

        # 添加：补水按钮和修改按钮的权限控制
        self.apply_button_permissions({
            self.btn_save_slot2: "water.edit",
            self.btn_save_slot4: "water.edit",
            self.btn_save_slot5: "water.edit",
            self.btn_save_slot7: "water.edit",})
        for slot in self.SLOTS:
            self.apply_button_permissions({
                getattr(self.ui, f"btn_card_slot{slot}_manual"): "water.control",
                getattr(self.ui, f"btn_card_slot{slot}_close"): "water.control",
            })


# ============================================================
# 页面 4：报警
# ============================================================
class AlarmPage(BasePage):
    title_key = "page.alarm"
    COLUMNS = [("alarm.hdr_no", "No", "fixed", 70), ("alarm.hdr_msg", "报警信息", "stretch", 0), ("alarm.hdr_trigger", "触发时间", "stretch", 0)]

    def setup_ui(self):
        self.ui = AlarmUi()
        self.ui.setupUi(self)
        self._build_layout()
        self._setup_columns()
        self._bind_events()
        if Alarm_event_detail:
            self.model = Alarm_event_detail()
        else:
            self.model = None
        self._raw_alarm_rows = []
        I18n.on_changed(self._refresh)
        self._refresh()
        now = QDateTime.currentDateTime()
        self.ui.date_start.setDateTime(QDateTime(now.date(), QTime(0, 0)))
        self.ui.date_end.setDateTime(now)

    def _build_layout(self):
        u = self.ui
        for w in (u.input_search, u.date_start, u.date_end, u.btn_query, u.btn_export, u.table_alarms):
            w.setParent(None)
        old = self.layout()
        if old is not None:
            QWidget().setLayout(old)
            old.deleteLater()
        main = QVBoxLayout(self)
        main.setContentsMargins(20, 20, 20, 20)
        main.setSpacing(16)

        def make_panel(layout):
            frame = QFrame()
            frame.setObjectName("panel_frame")
            layout.setContentsMargins(15, 15, 15, 15)
            frame.setLayout(layout)
            frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            return frame

        top = QHBoxLayout()
        top.setSpacing(10)
        top.addWidget(u.input_search)
        self.label_from = QLabel("从")
        top.addWidget(self.label_from)
        top.addWidget(u.date_start)
        self.label_to = QLabel("到")
        top.addWidget(self.label_to)
        top.addWidget(u.date_end)
        top.addWidget(u.btn_query)
        top.addWidget(u.btn_export)
        top.addStretch(1)
        main.addWidget(make_panel(top))
        main.addWidget(u.table_alarms, 1)
        for w in (u.input_search, u.date_start, u.date_end, u.btn_query, u.btn_export, u.table_alarms):
            w.show()

    def _setup_columns(self):
        t = self.ui.table_alarms
        t.setColumnCount(len(self.COLUMNS))
        t.verticalHeader().setVisible(False)
        t.verticalHeader().setDefaultSectionSize(32)
        t.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        t.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        t.setAlternatingRowColors(True)
        header = t.horizontalHeader()
        header.setStretchLastSection(False)
        for col, (_, _, mode, width) in enumerate(self.COLUMNS):
            if mode == "fixed":
                header.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
                t.setColumnWidth(col, width)
            else:
                header.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)

    def _bind_events(self):
        self.ui.input_search.returnPressed.connect(self.on_query)
        self.ui.btn_query.clicked.connect(self.on_query)
        self.ui.btn_export.clicked.connect(self.on_export)

    @safe_slot
    def on_enter(self):
        self.apply_button_permissions({
            self.ui.btn_export: "alarm.export",
        })
        self.on_query()

    def on_query(self, *args):
        if not self.model:
            return
        keyword = self.ui.input_search.text().strip()
        start_str = self.ui.date_start.dateTime().toString("yyyy-MM-dd HH:mm:ss")
        end_str = self.ui.date_end.dateTime().toString("yyyy-MM-dd HH:mm:ss")
        rows = self.model.query_alarm_name(keyword, start_str, end_str)
        if not rows:
            self.clear_alarms()
            self._raw_alarm_rows = []
            return
        self._raw_alarm_rows = rows
        self._render_table_data()

    def _render_table_data(self):
        data = []
        for r in self._raw_alarm_rows:
            db_name = r["alarm_name"]
            translated_name = I18n.t(f"alarm.name.{db_name}", default=db_name)
            data.append((r["ID"], translated_name, r["alarm_start_time"]))
        self.set_alarms(data)

    @safe_slot
    def on_export(self, *args):
        if export_xls and self.ui.table_alarms.rowCount() > 0:
            export_xls(self.ui.table_alarms, "报警记录")

    def add_alarm(self, *values):
        t = self.ui.table_alarms
        row = t.rowCount()
        t.insertRow(row)
        for col, val in enumerate(values):
            item = QTableWidgetItem(str(val))
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            t.setItem(row, col, item)

    def clear_alarms(self): self.ui.table_alarms.setRowCount(0)
    def set_alarms(self, rows):
        self.clear_alarms()
        for r in rows:
            self.add_alarm(*r)

    @safe_slot
    def _refresh(self, lang=None):
        u = self.ui
        u.input_search.setPlaceholderText(I18n.t("alarm.search_ph", "搜索"))
        u.btn_query.setText(I18n.t("btn.query", "查询"))
        u.btn_export.setText(I18n.t("btn.export", "导出"))
        self.label_from.setText(I18n.t("alarm.from", "从"))
        self.label_to.setText(I18n.t("alarm.to", "到"))
        u.table_alarms.setHorizontalHeaderLabels([I18n.t(key, default) for key, default, _, _ in self.COLUMNS])
        if getattr(self, '_raw_alarm_rows', None):
            self._render_table_data()


# ============================================================
# 页面 5：加热器
# ============================================================
class HeaterPage(BasePage):
    title_key = "page.heater"
    INPUTS = ("upper", "output", "range1", "range2", "reach", "manual", "lower")

    # ★ 保留你需要的带空格文本
    LABELS_TEXT = {
        "status": "当 前 状 态",
        "upper": "上    限    值",
        "output": "输    出    值",
        "range1": "一段范围设定值",
        "range2": "二段范围设定值",
        "reach": "到达温度设定值",
        "manual": "手 动 设 定 值",
        "lower": "下    限    值"
    }

    HEAT_STATUS_KEY = "heater.slot7.heat"
    AUTO_STATUS_KEY = "heater.slot7.auto"

    def setup_ui(self):
        self.ui = HeaterUi()
        self.ui.setupUi(self)
        self._rebuild_layout()
        self._bind_events()
        self._bind_state()
        I18n.on_changed(self._refresh)
        self._refresh()

        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self._update_heater_status)

    def _rebuild_layout(self):
        u = self.ui
        widgets = [u.heater_title, u.heater_btn_heat, u.heater_btn_auto]
        for key in self.LABELS_TEXT.keys():
            widgets.append(getattr(u, f"heater_{key}"))
        for name in self.INPUTS:
            widgets.append(getattr(u, f"heater_input_{name}"))

        for w in widgets:
            w.setParent(None)

        old = self.layout()
        if old is not None:
            QWidget().setLayout(old)
            old.deleteLater()

        # ================= 页面主布局 =================
        main = QVBoxLayout(self)
        main.setContentsMargins(30, 20, 30, 30)

        # ----- 内部卡片区域 -----
        card = QFrame()
        card.setObjectName("card_frame")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(0, 0, 0, 15)
        card_layout.setSpacing(15)

        # 标题
        header_frame = QFrame()
        header_frame.setObjectName("card_header")
        header_box = QHBoxLayout(header_frame)
        header_box.setContentsMargins(15, 10, 15, 10)
        u.heater_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        u.heater_title.setObjectName("card_title")
        header_box.addWidget(u.heater_title, 1)
        card_layout.addWidget(header_frame)

        # 内容区
        inner = QVBoxLayout()
        inner.setContentsMargins(15, 5, 15, 0)
        card_layout.addLayout(inner)

        grid = QGridLayout()
        grid.setHorizontalSpacing(15)
        grid.setVerticalSpacing(12)

        # ----- 第一行：当前状态 + 按钮 -----
        lbl_status = u.heater_status
        lbl_status.setObjectName("card_row_label")
        lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_status.setFixedWidth(150)

        u.heater_btn_heat.setObjectName("heater_btn_heat")
        u.heater_btn_heat.setCheckable(True)
        u.heater_btn_heat.setFixedHeight(36)
        u.heater_btn_heat.setMinimumWidth(200)

        grid.addWidget(lbl_status, 0, 0)
        grid.addWidget(u.heater_btn_heat, 0, 2)

        # ----- 循环生成输入行 -----
        for i, name in enumerate(self.INPUTS, start=1):
            lbl = getattr(u, f"heater_{name}")
            lbl.setObjectName("card_row_label")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setFixedWidth(150)

            unit_lbl = QLabel("mA")
            unit_lbl.setObjectName("card_row_unit")
            unit_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            unit_lbl.setFixedWidth(40)

            inp = getattr(u, f"heater_input_{name}")
            inp.setObjectName("card_input")
            inp.setMinimumHeight(36)
            inp.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

            grid.addWidget(lbl, i, 0)
            grid.addWidget(unit_lbl, i, 1)
            grid.addWidget(inp, i, 2)

        grid.setColumnStretch(0, 0)
        grid.setColumnStretch(1, 0)
        grid.setColumnStretch(2, 1)
        inner.addLayout(grid)

        # ----- 卡片内部底部按钮（只有 自动 和 确定修改） -----
        btn_layout = QHBoxLayout()
        btn_layout.addStretch(1)

        u.heater_btn_auto.setObjectName("heater_btn_auto")
        u.heater_btn_auto.setCheckable(True)
        u.heater_btn_auto.setMinimumSize(120, 40)

        self.btn_save = QPushButton(I18n.t("btn.save_changes", "确定修改"))
        self.btn_save.setObjectName("primaryBtn")
        self.btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_save.setMinimumSize(120, 40)
        self.btn_save.clicked.connect(self.on_save_modify)

        btn_layout.addWidget(u.heater_btn_auto)
        btn_layout.addSpacing(15)
        btn_layout.addWidget(self.btn_save)

        inner.addSpacing(15)
        inner.addLayout(btn_layout)

        main.addWidget(card)
        main.addStretch(1)

        # ----- 卡片外部：单独一行的返回按钮（完全复刻前三个页面） -----
        bottom_layout = QHBoxLayout()
        bottom_layout.addStretch(1)
        self.btn_back = QPushButton(I18n.t("btn.back", "< 返回生产记录"))
        self.btn_back.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_back.setFixedSize(140, 36)
        self.btn_back.setObjectName("ghostBtn")
        self.btn_back.clicked.connect(self._on_back_clicked)
        bottom_layout.addWidget(self.btn_back)
        main.addLayout(bottom_layout)

        # 显示所有控件
        for w in widgets:
            w.show()

        for name in self.INPUTS:
            set_input_validator(getattr(self.ui, f"heater_input_{name}"), "float", 0.0, 1000.0, 3)

    def _bind_events(self):
        self.ui.heater_btn_heat.toggled.connect(self.on_heat_toggled)
        self.ui.heater_btn_auto.toggled.connect(self.on_auto_toggled)

    def _bind_state(self):
        for name in self.INPUTS:
            bind_lineedit(getattr(self.ui, f"heater_input_{name}"), f"heater.slot7.input.{name}")

    @safe_slot
    def _on_back_clicked(self, *args):
        main_window = self.window()
        if hasattr(main_window, 'router'):
            main_window.router.go("monitor")

    @safe_slot
    def _update_heater_status(self):
        from common.state import state
        if not state.conn:
            return

        heat_val = state.get(self.HEAT_STATUS_KEY, "0")
        is_heating = str(heat_val) == "1"

        btn_heat = self.ui.heater_btn_heat
        btn_heat.blockSignals(True)
        btn_heat.setChecked(is_heating)
        btn_heat.setText(
            I18n.t("heater.btn_heat_stop", "停止加热") if is_heating else I18n.t("heater.btn_heat_start", "开始加热"))
        btn_heat.blockSignals(False)

        auto_val = state.get(self.AUTO_STATUS_KEY, "0")
        is_auto = str(auto_val) == "1"

        btn_auto = self.ui.heater_btn_auto
        btn_auto.blockSignals(True)
        btn_auto.setChecked(is_auto)
        btn_auto.setText(I18n.t("heater.btn_auto", "自动"))
        btn_auto.blockSignals(False)

    @safe_slot
    @require_permission("heater.heat", "控制加热")
    def on_heat_toggled(self, checked):
        from common.state import state
        target_val = 1 if checked else 0
        try:
            if state.conn:
                state.set(self.HEAT_STATUS_KEY, str(target_val))
            self._update_heater_status()
        except Exception as e:
            logger.error(f"加热控制下发失败: {e}")

    @safe_slot
    @require_permission("heater.auto", "控制自动")
    def on_auto_toggled(self, checked):
        from common.state import state
        target_val = 1 if checked else 0
        try:
            if state.conn:
                state.set(self.AUTO_STATUS_KEY, str(target_val))
            self._update_heater_status()
        except Exception as e:
            logger.error(f"自动控制下发失败: {e}")

    @safe_slot
    @require_permission("heater.edit", "修改加热器参数")
    def on_save_modify(self, *args):
        QMessageBox.information(self, "提示", "加热器参数已下发（需补充实际 PLC 地址）")

    @safe_slot
    def _refresh(self, lang=None):
        u = self.ui
        u.heater_title.setText(I18n.t("heater.title", "7#槽温度"))

        # ★ 核心修复：走翻译通道，并去掉 /mA
        status_text = I18n.t("heater.status", self.LABELS_TEXT["status"]).replace("/mA", "").strip()
        u.heater_status.setText(status_text)

        for name in self.INPUTS:
            lbl_text = I18n.t(f"heater.{name}", self.LABELS_TEXT[name]).replace("/mA", "").strip()
            getattr(u, f"heater_{name}").setText(lbl_text)

        u.heater_btn_auto.setText(I18n.t("heater.btn_auto", "自动"))
        self.btn_save.setText(I18n.t("btn.save_changes", "确定修改"))
        if hasattr(self, 'btn_back'):
            self.btn_back.setText(I18n.t("btn.back", "< 返回生产记录"))

        # 刷新按钮的文字状态
        self._update_heater_status()

    def on_leave(self):
        if hasattr(self, 'refresh_timer') and self.refresh_timer.isActive():
            self.refresh_timer.stop()

    def on_enter(self):
        if hasattr(self, 'refresh_timer') and not self.refresh_timer.isActive():
            self.refresh_timer.start(1000)

        self.apply_button_permissions({
            self.ui.heater_btn_heat: "heater.heat",
            self.ui.heater_btn_auto: "heater.auto",
            self.btn_save: "heater.edit"
        })


# ============================================================
# 页面 6：滴水时间
# ============================================================
class DripTimePage(BasePage):
    title_key = "page.drip_time"
    SLOTS = ("2", "3", "4", "5", "6", "7")

    def setup_ui(self):
        self.ui = DripTimeUi()
        self.ui.setupUi(self)
        self._rebuild_layout()
        self._bind_state()
        I18n.on_changed(self._refresh)
        self._refresh()

    def _rebuild_layout(self):
        u = self.ui
        old = self.layout()
        if old is not None:
            QWidget().setLayout(old)
            old.deleteLater()

        main_v = QVBoxLayout(self)
        main_v.setContentsMargins(30, 30, 30, 30)
        main_h = QHBoxLayout()
        main_h.setSpacing(40)
        frame_style = "QFrame { background-color: #f1f5f9; border: 2px solid #94a3b8; border-radius: 8px; }"
        label_style = "QLabel { background: #ffffff; border: 1px solid #cbd5e1; border-radius: 0; padding: 8px; font-size: 15px; color: #2c3e50; }"
        hdr_style = "QLabel { background: #e2e8f0; border: 1px solid #cbd5e1; border-radius: 0; padding: 8px; font-size: 15px; font-weight: bold; color: #1e3a8a; }"
        input_style = "QLineEdit { background: #ffffff; border: 1px solid #cbd5e1; border-radius: 0; padding: 8px; font-size: 15px; font-weight: bold; color: #2563eb; }"

        def build_side(side):
            frame = QFrame()
            frame.setObjectName(f"frame_drip_{side}")
            frame.setStyleSheet(frame_style)
            frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            box = QVBoxLayout(frame)
            box.setContentsMargins(15, 15, 15, 15)
            box.setSpacing(0)
            hdr = QHBoxLayout()
            hdr.setSpacing(0)
            h_no = getattr(u, f"{side}_hdr_no")
            h_name = getattr(u, f"{side}_hdr_name")
            h_value = getattr(u, f"{side}_hdr_value")
            for w in (h_no, h_name, h_value):
                w.setStyleSheet(hdr_style)
                w.setAlignment(Qt.AlignmentFlag.AlignCenter)
                w.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            hdr.addWidget(h_no, 1)
            hdr.addWidget(h_name, 2)
            hdr.addWidget(h_value, 2)
            box.addLayout(hdr)
            for slot in self.SLOTS:
                row = QHBoxLayout()
                row.setSpacing(0)
                no = getattr(u, f"{side}_no_{slot}")
                name = getattr(u, f"{side}_name_{slot}")
                inp = getattr(u, f"{side}_value_{slot}")
                no.setStyleSheet(label_style)
                no.setAlignment(Qt.AlignmentFlag.AlignCenter)
                name.setStyleSheet(label_style)
                name.setAlignment(Qt.AlignmentFlag.AlignCenter)
                inp.setStyleSheet(input_style)
                inp.setAlignment(Qt.AlignmentFlag.AlignCenter)
                for w in (no, name, inp):
                    w.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
                row.addWidget(no, 1)
                row.addWidget(name, 2)
                row.addWidget(inp, 2)
                box.addLayout(row)
            return frame

        main_h.addWidget(build_side("left"), 1)
        main_h.addWidget(build_side("right"), 1)
        main_v.addLayout(main_h)
        main_v.addStretch(1)

        for slot in self.SLOTS:
            set_input_validator(getattr(self.ui, f"left_value_{slot}"), "float", 0.0, 999.0, 1)
            set_input_validator(getattr(self.ui, f"right_value_{slot}"), "float", 0.0, 999.0, 1)

    def _bind_state(self):
        for slot in self.SLOTS:
            bind_lineedit(getattr(self.ui, f"left_value_{slot}"), f"drip.left.{slot}", default="")
            bind_lineedit(getattr(self.ui, f"right_value_{slot}"), f"drip.right.{slot}", default="")

    @safe_slot
    def _refresh(self, lang=None):
        u = self.ui
        u.left_hdr_no.setText(I18n.t("drip.hdr_no", "槽号"))
        u.left_hdr_name.setText(I18n.t("drip.hdr_name", "槽名称"))
        u.left_hdr_value.setText(I18n.t("drip.hdr_drip", "滴水/秒"))
        u.right_hdr_no.setText(I18n.t("drip.hdr_no", "槽号"))
        u.right_hdr_name.setText(I18n.t("drip.hdr_name", "槽名称"))
        u.right_hdr_value.setText(I18n.t("drip.hdr_plat", "镀液/秒"))


# ============================================================
# 页面 7：监控大屏
# ============================================================
class MonitorPage(BasePage):
    title_key = "page.monitor"
    SLOT_NAMES = {1: "上下料", 2: "水洗4", 3: "水洗3", 4: "粗化", 5: "水洗2", 6: "水洗1", 7: "脱脂"}

    def setup_ui(self):
        if Alarm_event_detail:
            self.alarm_model = Alarm_event_detail()
        else:
            self.alarm_model = None
        self.thread_pool = QThreadPool.globalInstance()
        self._last_alarm_ids = []
        self.alarm_fail_count = 0
        self.is_alarm_paused = False

        self._build_layout()
        I18n.on_changed(self._refresh)
        self._refresh()

        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self._update_car_pos_from_redis)

        self.slot_timer = QTimer(self)
        self.slot_timer.timeout.connect(self._update_slot_data_from_redis)

        self.alarm_timer = QTimer(self)
        self.alarm_timer.timeout.connect(self._trigger_alarm_query)

    def _build_layout(self):
        old_layout = self.layout()
        if old_layout is not None:
            QWidget().setLayout(old_layout)
            old_layout.deleteLater()

        main = QVBoxLayout(self)
        main.setContentsMargins(20, 20, 20, 20)
        main.setSpacing(15)

        car_frame = QFrame()
        car_frame.setObjectName("panel_frame")
        car_frame.setStyleSheet("QFrame#panel_frame { background: #ffffff; border: 1px solid #cbd5e1; border-radius: 8px; }")
        car_h_layout = QHBoxLayout(car_frame)
        car_h_layout.setContentsMargins(10, 10, 10, 10)
        car_h_layout.setSpacing(0)

        self.lbl_car_pos_title = QLabel("天车位置")
        self.lbl_car_pos_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_car_pos_title.setStyleSheet("font-weight: bold; color: #1e3a8a; border: none; background: transparent;")
        car_h_layout.addWidget(self.lbl_car_pos_title, 1)

        self.car_widget = CarPositionWidget()
        car_h_layout.addWidget(self.car_widget, 7)
        main.addWidget(car_frame)

        grid = QGridLayout()
        grid.setSpacing(0)
        self.header_labels = []
        headers = [("monitor.seq", "序号"), ("monitor.dev_name", "设备名称"), ("monitor.flow", "流量"), ("monitor.depth", "深度"), ("monitor.temp", "温度"), ("monitor.ph", "电导率|PH")]
        for row, (key, default_text) in enumerate(headers):
            lbl = QLabel(default_text)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            if row == 0:
                lbl.setStyleSheet("border: 1px solid #cbd5e1; background-color: #1e7b3d; color: white; font-weight: bold; font-size: 14px;")
            elif row == 1:
                lbl.setStyleSheet("border: 1px solid #cbd5e1; background-color: #e0f2fe; color: #1e3a8a; font-weight: bold; font-size: 14px;")
            else:
                lbl.setStyleSheet("border: 1px solid #cbd5e1; background: #f8fafc; font-weight: bold; font-size: 14px;")
            lbl.setProperty("i18n_key", key)
            grid.addWidget(lbl, row, 0)
            self.header_labels.append(lbl)

        self.slot_labels = {}
        self.status_labels = {}
        for col in range(1, 8):
            lbl_num = QLabel(str(col))
            lbl_num.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl_num.setStyleSheet("border: 1px solid #cbd5e1; background-color: #1e7b3d; color: white; font-weight: bold;")
            lbl_num.setFixedHeight(30)
            grid.addWidget(lbl_num, 0, col)

            slot_name = self.SLOT_NAMES.get(col, f"{col}#槽")
            lbl_name = QLabel(slot_name)
            lbl_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl_name.setStyleSheet("border: 1px solid #cbd5e1; background-color: #e0f2fe; color: #1e3a8a; font-size: 16px; font-weight: bold;")
            lbl_name.setFixedHeight(110)
            grid.addWidget(lbl_name, 1, col)
            self.slot_labels[col] = lbl_name

            for row_idx in range(2, 6):
                if row_idx == 2:
                    lbl_status = ClickableLabel("")
                    lbl_status.clicked.connect(self._jump_to_water)
                    lbl_status.setCursor(Qt.CursorShape.PointingHandCursor)
                elif row_idx == 3:
                    lbl_status = ClickableLabel("")
                    lbl_status.clicked.connect(self._jump_to_depth)
                    lbl_status.setCursor(Qt.CursorShape.PointingHandCursor)
                elif row_idx == 4:
                    lbl_status = ClickableLabel("")
                    lbl_status.clicked.connect(self._jump_to_temp)
                    lbl_status.setCursor(Qt.CursorShape.PointingHandCursor)
                elif row_idx == 5:
                    lbl_status = ClickableLabel("")
                    lbl_status.clicked.connect(self._jump_to_conductivity)
                    lbl_status.setCursor(Qt.CursorShape.PointingHandCursor)
                else:
                    lbl_status = QLabel("")

                lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
                lbl_status.setStyleSheet("border: 1px solid #cbd5e1; font-size: 14px; color: black;")
                lbl_status.setFixedHeight(45)
                grid.addWidget(lbl_status, row_idx, col)
                self.status_labels[(row_idx, col)] = lbl_status

        for col in range(8):
            grid.setColumnStretch(col, 1)

        grid_frame = QFrame()
        grid_frame.setObjectName("panel_frame")
        grid_frame.setStyleSheet("QFrame#panel_frame { background: #ffffff; border: 1px solid #cbd5e1; border-radius: 8px; }")
        grid_layout_wrapper = QVBoxLayout(grid_frame)
        grid_layout_wrapper.setContentsMargins(10, 10, 10, 10)
        grid_layout_wrapper.addLayout(grid)
        main.addWidget(grid_frame)

        bottom_layout = QHBoxLayout()
        bottom_layout.setSpacing(15)
        left_frame = QFrame()
        left_frame.setObjectName("panel_frame")
        left_frame.setStyleSheet("QFrame#panel_frame { background: #ffffff; border: 1px solid #cbd5e1; border-radius: 8px; }")
        bottom_layout.addWidget(left_frame, 1)
        right_frame = QFrame()
        right_frame.setObjectName("panel_frame")
        right_frame.setStyleSheet("QFrame#panel_frame { background: #ffffff; border: 1px solid #cbd5e1; border-radius: 8px; }")
        right_layout = QVBoxLayout(right_frame)
        right_layout.setContentsMargins(5, 5, 5, 5)

        self.table_alarm = QTableWidget()
        self.table_alarm.setColumnCount(3)
        self.table_alarm.setHorizontalHeaderLabels(["No.", "Time", "Message"])
        self.table_alarm.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table_alarm.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table_alarm.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table_alarm.verticalHeader().setVisible(False)
        self.table_alarm.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table_alarm.setAlternatingRowColors(True)
        self.table_alarm.setShowGrid(False)
        right_layout.addWidget(self.table_alarm)

        bottom_layout.addWidget(right_frame, 1)
        main.addLayout(bottom_layout, 1)

    def _jump_to_conductivity(self):
        main_window = self.window()
        if hasattr(main_window, 'router'):
            main_window.router.go("conductivity")
    def _jump_to_water(self):
        main_window = self.window()
        if hasattr(main_window, 'router'):
            main_window.router.go("water")
    def _jump_to_depth(self):
        main_window = self.window()
        if hasattr(main_window, 'router'):
            main_window.router.go("depth")
    def _jump_to_temp(self):
        main_window = self.window()
        if hasattr(main_window, 'router'):
            main_window.router.go("temp")

    def _trigger_alarm_query(self):
        if self.is_alarm_paused:
            return
        if self.alarm_fail_count >= 5:
            self.is_alarm_paused = True
            self.alarm_timer.stop()
            logger.warning("【后台监控】数据库长时间未响应，报警查询静默暂停 30 秒...")
            QTimer.singleShot(30000, self._resume_alarm_timer)
            return
        worker = AlarmQueryWorker(self.alarm_model, hours=24)
        worker.signals.finished.connect(self._on_alarm_data_received)
        self.thread_pool.start(worker)

    def _resume_alarm_timer(self):
        self.alarm_fail_count = 0
        self.is_alarm_paused = False
        self.alarm_timer.start(2000)
        self._trigger_alarm_query()

    @safe_slot
    def _on_alarm_data_received(self, data):
        # data 是 None 说明真的是 database 异常
        if data is None:
            self.alarm_fail_count += 1
            if self.table_alarm.rowCount() == 0 and not self.is_alarm_paused:
                self.table_alarm.setRowCount(1)
                item = QTableWidgetItem("【系统提示】数据库连接异常，暂无实时报警数据")
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item.setForeground(Qt.GlobalColor.red)
                self.table_alarm.setItem(0, 0, item)
                self.table_alarm.setSpan(0, 0, 1, 3)
            return

        # data 不是 None，说明连接成功，重置失败次数
        self.alarm_fail_count = 0
        self.table_alarm.clearSpans()

        # 如果是空列表 []，说明没数据，清空表格即可
        if not data:
            self.table_alarm.setRowCount(0)
            self._last_alarm_ids = []
            return

        # 正常有数据的情况
        current_ids = [row["ID"] for row in data]
        if current_ids == self._last_alarm_ids:
            return
        self._last_alarm_ids = current_ids
        self._refresh_alarm_table(data)
    def _refresh_alarm_table(self, data):
        t = self.table_alarm
        t.setRowCount(0)
        data.sort(key=lambda x: x["ID"])
        for idx, row in enumerate(data):
            alarm_name = str(row.get("alarm_name") or "")
            time_str = str(row.get("alarm_start_time") or "")
            if len(time_str) > 19:
                formatted_time = time_str[11:19]
            else:
                formatted_time = time_str
            existing_row = -1
            for r in range(t.rowCount()):
                if t.item(r, 2) and t.item(r, 2).text() == alarm_name:
                    existing_row = r
                    break
            if existing_row != -1:
                t.item(existing_row, 1).setText(formatted_time)
                items = [t.takeItem(existing_row, c) for c in range(t.columnCount())]
                t.removeRow(existing_row)
                r = t.rowCount()
                t.insertRow(r)
                for c, item in enumerate(items):
                    t.setItem(r, c, item)
            else:
                r = t.rowCount()
                t.insertRow(r)
                t.setItem(r, 0, QTableWidgetItem(""))
                t.setItem(r, 1, QTableWidgetItem(formatted_time))
                t.setItem(r, 2, QTableWidgetItem(alarm_name))
        for r in range(t.rowCount()):
            for c in range(t.columnCount()):
                item = t.item(r, c)
                if item:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_no = t.item(r, 0)
            if item_no:
                item_no.setText(str(r + 1))
        t.scrollToBottom()

    @safe_slot
    def _update_slot_data_from_redis(self):
        try:
            from common.state import state
            if not state.conn:
                return

            def get_float(addr):
                tag_info = PLC_TAGS.get("monitor", {}).get(addr)
                if not tag_info:
                    return None
                val = state.conn.hget("data_realtime", tag_info["redis"])
                if val is None or val in ("None", "NULL", "init", ""):
                    return None
                try:
                    return float(val)
                except ValueError:
                    return None

            def check_status(act_addr, h_addr, l_addr):
                act_val = get_float(act_addr)
                h_val = get_float(h_addr)
                l_val = get_float(l_addr)
                if act_val is None:
                    return "", "black"
                is_alarm = False
                if h_val is not None and act_val > h_val:
                    is_alarm = True
                if l_val is not None and act_val < l_val:
                    is_alarm = True
                if is_alarm:
                    color = "red"
                else:
                    color = "green"
                return f"{act_val:.2f}", color

            data_dict = {slot: {"flow": ("", "black"), "depth": ("", "black"), "temp": ("", "black"), "ph": ("", "black")} for slot in range(1, 8)}

            if "PLC1.DD1.D4505" in PLC_TAGS.get("monitor", {}):
                data_dict[2]["flow"] = check_status("PLC1.DD1.D4505", "PLC1.DD1.D4506", "PLC1.DD1.D4507")
            if "PLC1.DD1.D4510" in PLC_TAGS.get("monitor", {}):
                data_dict[4]["flow"] = check_status("PLC1.DD1.D4510", "PLC1.DD1.D4511", "PLC1.DD1.D4512")
            if "PLC1.DD1.D4515" in PLC_TAGS.get("monitor", {}):
                data_dict[5]["flow"] = check_status("PLC1.DD1.D4515", "PLC1.DD1.D4516", "PLC1.DD1.D4517")
            if "PLC1.DD1.D4520" in PLC_TAGS.get("monitor", {}):
                data_dict[7]["flow"] = check_status("PLC1.DD1.D4520", "PLC1.DD1.D4521", "PLC1.DD1.D4522")

            if "PLC1.DD1.D6745" in PLC_TAGS.get("monitor", {}):
                data_dict[1]["depth"] = check_status("PLC1.DD1.D6745", "PLC1.DD1.D6746", "PLC1.DD1.D6748")
            if "PLC1.DD1.D6750" in PLC_TAGS.get("monitor", {}):
                data_dict[6]["depth"] = check_status("PLC1.DD1.D6750", "PLC1.DD1.D6751", "PLC1.DD1.D6753")

            if "PLC1.DD1.D4500" in PLC_TAGS.get("monitor", {}):
                data_dict[7]["temp"] = check_status("PLC1.DD1.D4500", "PLC1.DD1.D4501", "PLC1.DD1.D4502")

            if "PLC1.DD1.D4525" in PLC_TAGS.get("monitor", {}):
                cond_str, cond_color = check_status("PLC1.DD1.D4525", "PLC1.DD1.D4526", "PLC1.DD1.D4527")
                if cond_str:
                    data_dict[2]["ph"] = (f"{cond_str}|", cond_color)
            if "PLC1.DD1.D4530" in PLC_TAGS.get("monitor", {}):
                ph_str, ph_color = check_status("PLC1.DD1.D4530", "PLC1.DD1.D4531", "PLC1.DD1.D4532")
                if ph_str:
                    data_dict[4]["ph"] = (f"|{ph_str}", ph_color)

            self.update_data(data_dict)
        except Exception as e:
            logger.error(f"更新槽位数据异常: {e}")

    def update_data(self, data_dict):
        for slot, data in data_dict.items():
            slot = int(slot)
            if slot in self.slot_labels:
                def update_label(row_idx, val, color):
                    lbl = self.status_labels[(row_idx, slot)]
                    lbl.setText(val)
                    lbl.setStyleSheet(f"border: 1px solid #cbd5e1; font-size: 14px; color: {color};")
                flow_val, flow_color = data.get("flow", ("", "black"))
                update_label(2, flow_val, flow_color)
                depth_val, depth_color = data.get("depth", ("", "black"))
                update_label(3, depth_val, depth_color)
                temp_val, temp_color = data.get("temp", ("", "black"))
                update_label(4, temp_val, temp_color)
                ph_val, ph_color = data.get("ph", ("", "black"))
                update_label(5, ph_val, ph_color)

    @safe_slot
    def _update_car_pos_from_redis(self):
        try:
            from common.state import state
            if not state.conn:
                return
            tag_info = PLC_TAGS.get("crane", {}).get("PLC1.DD1.D202")
            if not tag_info:
                return
            val_str = state.conn.hget("data_realtime", tag_info["redis"])
            if val_str is None or val_str in ("init", "None", "NULL", ""):
                return
            try:
                slot_index = int(float(val_str))
                if 1 <= slot_index <= 7:
                    self.car_widget.set_car_position(slot_index)
            except (ValueError, TypeError):
                pass
        except Exception:
            pass

    @safe_slot
    def _refresh(self, lang=None):
        for lbl in self.header_labels:
            lbl.setText(I18n.t(lbl.property("i18n_key"), lbl.text()))
        for col, lbl in self.slot_labels.items():
            lbl.setText(I18n.t(f"slot.{col}", self.SLOT_NAMES.get(col, f"{col}#槽")))
        if hasattr(self, 'lbl_car_pos_title'):
            self.lbl_car_pos_title.setText(I18n.t("monitor.car_pos_title", "天车位置"))
        self.table_alarm.setHorizontalHeaderLabels([I18n.t("monitor.alarm_seq", "No."), I18n.t("monitor.alarm_time", "Time"), I18n.t("monitor.alarm_msg", "Message")])
        self._update_slot_data_from_redis()

    def on_leave(self):
        if hasattr(self, 'update_timer') and self.update_timer.isActive():
            self.update_timer.stop()
        if hasattr(self, 'alarm_timer') and self.alarm_timer.isActive():
            self.alarm_timer.stop()
        if hasattr(self, 'slot_timer') and self.slot_timer.isActive():
            self.slot_timer.stop()
    def on_enter(self):
        if hasattr(self, 'update_timer') and not self.update_timer.isActive():
            self.update_timer.start(500)
        if not getattr(self, 'is_alarm_paused', False):
            if hasattr(self, 'alarm_timer') and not self.alarm_timer.isActive():
                self.alarm_timer.start(2000)
            self._trigger_alarm_query()
        if hasattr(self, 'slot_timer') and not self.slot_timer.isActive():
            self.slot_timer.start(1000)


# ============================================================
# 页面 8：用户管理 / 权限 / MES / 等
# ============================================================
class UserManagePage(BasePage):
    title_key = "page.user_manage"

    def setup_ui(self):
        self.user_manager = User()
        self._build_layout()
        self._bind_events()
        I18n.on_changed(self._refresh)
        self._refresh()


    def _build_layout(self):
        old_layout = self.layout()
        if old_layout is not None:
            QWidget().setLayout(old_layout)
            old_layout.deleteLater()

        main = QVBoxLayout(self)
        main.setContentsMargins(20, 20, 20, 20)
        main.setSpacing(16)
        top = QHBoxLayout()
        self.input_search = QLineEdit()
        self.input_search.setFixedWidth(200)
        self.btn_search = QPushButton()
        self.btn_add = QPushButton()
        self.btn_edit = QPushButton()
        self.btn_del = QPushButton()
        top.addWidget(self.input_search)
        top.addWidget(self.btn_search)
        top.addSpacing(20)
        top.addWidget(self.btn_add)
        top.addWidget(self.btn_edit)
        top.addWidget(self.btn_del)
        top.addStretch(1)
        frame = QFrame()
        frame.setObjectName("panel_frame")
        frame.setLayout(top)
        main.addWidget(frame)

        self.table = QTableWidget()
        self.table.setColumnCount(7)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        header.setStretchLastSection(True)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        main.addWidget(self.table, 1)

    def _bind_events(self):
        self.btn_search.clicked.connect(self.load_data)
        self.input_search.returnPressed.connect(self.load_data)
        self.btn_del.clicked.connect(self._on_delete)
        self.btn_add.clicked.connect(self._on_add)
        self.btn_edit.clicked.connect(self._on_edit)

    @safe_slot
    def load_data(self, *args):
        keyword = self.input_search.text().strip()
        rows = self.user_manager.query_user(Akeyword=keyword)
        if not rows:
            self.table.setRowCount(0)
            return

        current_user = state.get("app.user.current_user", "").strip()
        if current_user.lower() != "admin":
            rows = [r for r in rows if r.get("name", "").lower() != "admin" and r.get("role_name") != "系统管理员"]

        self.table.setRowCount(0)
        if not rows:
            return

        self.table.setRowCount(len(rows))
        for r, user in enumerate(rows):
            self.table.setItem(r, 0, QTableWidgetItem(str(user["ID"])))
            self.table.setItem(r, 1, QTableWidgetItem(user["name"]))
            self.table.setItem(r, 2, QTableWidgetItem(user["work_number"]))
            self.table.setItem(r, 3, QTableWidgetItem(user["department"]))
            self.table.setItem(r, 4, QTableWidgetItem(I18n.t(user["role_name"], user["role_name"])))
            raw_status = user.get("user_status")
            if str(raw_status) == "1" or raw_status is True:
                status_str = I18n.t("user.status_ok", "正常")
            else:
                status_str = I18n.t("user.status_off", "禁用")
            self.table.setItem(r, 5, QTableWidgetItem(status_str))
            c_time = user.get("create_time")
            if isinstance(c_time, str):
                time_str = c_time[:19]
            elif c_time:
                time_str = c_time.strftime("%Y-%m-%d %H:%M:%S")
            else:
                time_str = ""
            self.table.setItem(r, 6, QTableWidgetItem(time_str))

    @safe_slot
    def _on_delete(self, *args):
        selected_items = self.table.selectionModel().selectedRows()
        if not selected_items:
            return QMessageBox.warning(self, "提示", "请先选中数据！")
        ids = [int(self.table.item(idx.row(), 0).text()) for idx in selected_items]
        if QMessageBox.question(self, "确认", f"确定删除 {len(ids)} 个用户吗？") == QMessageBox.StandardButton.Yes:
            success = sum(1 for uid in ids if self.user_manager.delete_user(uid))
            QMessageBox.information(self, "完成", f"删除 {success} 条数据！")
            self.load_data()

    @safe_slot
    def _on_add(self, *args):
        if UserEditDialog(self).exec() == QDialog.DialogCode.Accepted:
            self.load_data()

    @safe_slot
    def _on_edit(self, *args):
        row = self.table.currentRow()
        if row < 0:
            return QMessageBox.warning(self, "提示", "请先选中一行！")
        if UserEditDialog(self, user_id=int(self.table.item(row, 0).text())).exec() == QDialog.DialogCode.Accepted:
            self.load_data()

    @safe_slot
    def _refresh(self, lang=None):
        self.input_search.setPlaceholderText(I18n.t("user.search_ph", "按姓名模糊搜索..."))
        self.btn_search.setText(I18n.t("user.btn_search", "查询"))
        self.btn_add.setText(I18n.t("user.btn_add", "新增"))
        self.btn_edit.setText(I18n.t("user.btn_edit", "修改"))
        self.btn_del.setText(I18n.t("user.btn_del", "删除"))
        self.table.setHorizontalHeaderLabels([I18n.t("user.col_id", "ID"), I18n.t("user.col_name", "姓名"), I18n.t("user.col_workno", "工号"), I18n.t("user.col_dept", "部门"), I18n.t("user.col_role", "角色"), I18n.t("user.col_status", "状态"), I18n.t("user.col_time", "创建时间")])
        # self.load_data()


class PermissionPage(BasePage):
    title_key = "page.perm_manage"

    def setup_ui(self):
        self.perm_manager = RolePermission()
        self._build_layout()
        I18n.on_changed(self._refresh)
        self._refresh()

    def _build_layout(self):
        old_layout = self.layout()
        if old_layout is not None:
            QWidget().setLayout(old_layout)
            old_layout.deleteLater()

        main = QHBoxLayout(self)
        main.setContentsMargins(20, 20, 20, 20)
        main.setSpacing(20)
        left_layout = QVBoxLayout()
        self.lbl_role = QLabel()
        self.lbl_role.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.list_roles = QListWidget()
        self.list_roles.currentItemChanged.connect(self._on_role_changed)
        left_layout.addWidget(self.lbl_role)
        left_layout.addWidget(self.list_roles)

        right_layout = QVBoxLayout()
        self.tree_perms = QTreeWidget()
        self.tree_perms.setHeaderHidden(True)
        self.tree_perms.itemChanged.connect(self._on_item_changed)
        self.btn_save = QPushButton()
        self.btn_save.setObjectName("primaryBtn")
        self.btn_save.clicked.connect(self._save_permissions)
        right_layout.addWidget(self.tree_perms)
        right_layout.addWidget(self.btn_save)
        main.addLayout(left_layout, 1)
        main.addLayout(right_layout, 3)

    def _build_tree(self):
        from config.menu import MENU_TREE, PAGE_PERMISSIONS
        self.tree_perms.clear()

        for group_key, children in MENU_TREE:
            group_item = QTreeWidgetItem(self.tree_perms)
            group_item.setText(0, I18n.t(group_key, group_key))
            group_item.setFlags(group_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            group_item.setCheckState(0, Qt.CheckState.Unchecked)

            for child in children:
                child_key, page_key = child[0], child[1]
                page_item = QTreeWidgetItem(group_item)
                page_item.setText(0, I18n.t(child_key, child_key))
                page_item.setData(0, Qt.ItemDataRole.UserRole, page_key)
                page_item.setFlags(page_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                page_item.setCheckState(0, Qt.CheckState.Unchecked)

                # ★ 核心修改：从配置字典里读取子权限，自动生成复选框
                if page_key in PAGE_PERMISSIONS:
                    for btn_name_key, btn_key in PAGE_PERMISSIONS[page_key]:
                        btn_item = QTreeWidgetItem(page_item)
                        # 这里的 btn_name_key 是去翻译字典里找文字，如果没有就显示 btn_key
                        btn_item.setText(0, I18n.t(btn_name_key, btn_name_key))
                        btn_item.setData(0, Qt.ItemDataRole.UserRole, btn_key)
                        btn_item.setFlags(btn_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                        btn_item.setCheckState(0, Qt.CheckState.Unchecked)

    @safe_slot
    def _refresh(self, lang=None):
        if not hasattr(self, 'list_roles'):
            return
        if self.list_roles.currentItem():
            current_role_data = self.list_roles.currentItem().data(Qt.ItemDataRole.UserRole)
        else:
            current_role_data = None
        self.list_roles.blockSignals(True)
        self.tree_perms.blockSignals(True)
        self.lbl_role.setText(I18n.t("perm.role_list", "角色列表"))
        self.btn_save.setText(I18n.t("perm.btn_save", "保存配置"))
        self.list_roles.clear()
        from config.menu import ROLES
        for role in ROLES:
            item = QListWidgetItem(I18n.t(role, role))
            item.setData(Qt.ItemDataRole.UserRole, role)
            self.list_roles.addItem(item)
            if role == current_role_data:
                self.list_roles.setCurrentItem(item)
        self._build_tree()
        self.list_roles.blockSignals(False)
        self.tree_perms.blockSignals(False)
        if self.list_roles.currentItem():
            self._on_role_changed(self.list_roles.currentItem(), None)

    def _on_item_changed(self, item, column):
        self.tree_perms.blockSignals(True)
        if item.childCount() == 0:
            parent = item.parent()
            while parent:
                all_checked = all(parent.child(i).checkState(0) == Qt.CheckState.Checked for i in range(parent.childCount()))
                any_checked = any(parent.child(i).checkState(0) != Qt.CheckState.Unchecked for i in range(parent.childCount()))
                if all_checked:
                    check_state = Qt.CheckState.Checked
                elif any_checked:
                    check_state = Qt.CheckState.PartiallyChecked
                else:
                    check_state = Qt.CheckState.Unchecked
                parent.setCheckState(0, check_state)
                parent = parent.parent()
        self.tree_perms.blockSignals(False)

    def _on_role_changed(self, current, previous):
        from config.menu import SUPER_ROLES
        if not current:
            return
        role_name = current.data(Qt.ItemDataRole.UserRole)
        self.tree_perms.blockSignals(True)
        allowed_keys = self.perm_manager.get_permissions(role_name)

        for i in range(self.tree_perms.topLevelItemCount()):
            group = self.tree_perms.topLevelItem(i)
            group.setCheckState(0, Qt.CheckState.Unchecked)
            def clear_all(item):
                for j in range(item.childCount()):
                    child = item.child(j)
                    child.setCheckState(0, Qt.CheckState.Unchecked)
                    clear_all(child)
            clear_all(group)

        if "admin" in role_name.lower() or role_name in SUPER_ROLES:
            for i in range(self.tree_perms.topLevelItemCount()):
                group = self.tree_perms.topLevelItem(i)
                group.setCheckState(0, Qt.CheckState.Checked)
                def check_all(item):
                    for j in range(item.childCount()):
                        child = item.child(j)
                        child.setCheckState(0, Qt.CheckState.Checked)
                        check_all(child)
                check_all(group)
        else:
            def apply_state(item):
                for i in range(item.childCount()):
                    child = item.child(i)
                    key = child.data(0, Qt.ItemDataRole.UserRole)
                    if key and key in allowed_keys:
                        check_state = Qt.CheckState.Checked
                    else:
                        check_state = Qt.CheckState.Unchecked
                    child.setCheckState(0, check_state)
                    apply_state(child)
            for i in range(self.tree_perms.topLevelItemCount()):
                apply_state(self.tree_perms.topLevelItem(i))
            for i in range(self.tree_perms.topLevelItemCount()):
                for j in range(self.tree_perms.topLevelItem(i).childCount()):
                    self._on_item_changed(self.tree_perms.topLevelItem(i).child(j), 0)
        self.tree_perms.blockSignals(False)

    @safe_slot
    def _save_permissions(self, *args):
        from config.menu import SUPER_ROLES
        current_item = self.list_roles.currentItem()
        if not current_item:
            return
        role_name = current_item.data(Qt.ItemDataRole.UserRole)
        if "admin" in role_name.lower() or role_name in SUPER_ROLES:
            return QMessageBox.information(self, "提示", "该角色拥有所有权限，无需保存。")

        allowed_keys = []
        def collect_checked(item):
            for i in range(item.childCount()):
                child = item.child(i)
                if child.checkState(0) != Qt.CheckState.Unchecked:
                    key = child.data(0, Qt.ItemDataRole.UserRole)
                    if key and key not in allowed_keys:
                        allowed_keys.append(key)
                collect_checked(child)

        for i in range(self.tree_perms.topLevelItemCount()):
            collect_checked(self.tree_perms.topLevelItem(i))
        if not allowed_keys:
            return QMessageBox.warning(self, "警告", "不能保存空权限！")
        if self.perm_manager.save_permissions(role_name, allowed_keys):
            QMessageBox.information(self, "成功", f"角色【{role_name}】权限已保存！")


class UserEditDialog(QDialog):
    def __init__(self, parent=None, user_id=None, is_register=False):
        super().__init__(parent)
        self.user_id = user_id
        self.is_register = is_register
        self.user_manager = User()
        from config.menu import ROLE_LEVELS
        self.curr_user = state.get("app.user.current_user", "").strip()
        self.curr_role = state.get("app.user.current_role", "").strip()
        if self.curr_user.lower() == "admin":
            self.curr_level = 1
        else:
            self.curr_level = ROLE_LEVELS.get(self.curr_role, 99)
        self.setFixedSize(360, 450)
        self.setStyleSheet("QDialog { background-color: #ffffff; }")
        self._build_ui()
        if self.user_id:
            self._load_user_data()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)
        if self.is_register:
            title = "用户注册"
        elif self.user_id:
            title = I18n.t("user.edit_mod", "修改用户")
        else:
            title = I18n.t("user.edit_add", "新增用户")
        self.setWindowTitle(title)

        self.input_name = QLineEdit()
        self.input_name.setPlaceholderText(I18n.t("user.ph_name", "请输入姓名"))
        self.input_name.setFixedHeight(35)
        self.input_workno = QLineEdit()
        self.input_workno.setPlaceholderText(I18n.t("user.ph_workno", "请输入工号 (唯一)"))
        self.input_workno.setFixedHeight(35)
        if self.user_id:
            self.input_workno.setReadOnly(True)
        self.input_dept = QLineEdit()
        self.input_dept.setPlaceholderText(I18n.t("user.ph_dept", "请输入部门"))
        self.input_dept.setFixedHeight(35)
        self.combo_role = QComboBox()
        from config.menu import ROLES, ROLE_LEVELS
        for role in ROLES:
            role_level = ROLE_LEVELS.get(role, 99)
            if self.curr_user.lower() == "admin" or role_level >= self.curr_level:
                self.combo_role.addItem(I18n.t(role, role), role)
        self.combo_role.setFixedHeight(35)
        if self.is_register:
            self.combo_role.setCurrentText(I18n.t("操作员", "操作员"))
            self.combo_role.setEnabled(False)

        self.input_pwd = QLineEdit()
        if not self.user_id:
            self.input_pwd.setPlaceholderText("请输入密码")
        else:
            self.input_pwd.setPlaceholderText("不填则不修改密码")
        self.input_pwd.setEchoMode(QLineEdit.EchoMode.Password)
        self.input_pwd.setFixedHeight(35)

        for lbl_txt, w in [("user.label_name", self.input_name), ("user.label_workno", self.input_workno), ("user.label_dept", self.input_dept), ("user.label_role", self.combo_role), ("user.label_pwd", self.input_pwd)]:
            layout.addWidget(QLabel(I18n.t(lbl_txt, "")))
            layout.addWidget(w)

        layout.addSpacing(10)
        btns = QHBoxLayout()
        if self.is_register:
            btn_ok_text = "注 册"
        else:
            btn_ok_text = I18n.t("user.btm_save", "保 存")
        btn_ok = QPushButton(btn_ok_text)
        btn_ok.setObjectName("primaryBtn")
        btn_ok.setFixedHeight(38)
        btn_ok.clicked.connect(self._save)
        btn_cancel = QPushButton(I18n.t("user.btm_cancel", "取 消"))
        btn_cancel.setFixedHeight(38)
        btn_cancel.clicked.connect(self.reject)
        btns.addWidget(btn_cancel)
        btns.addWidget(btn_ok)
        layout.addLayout(btns)

    def _load_user_data(self):
        for u in self.user_manager.query_user():
            if u["ID"] == self.user_id:
                self.input_name.setText(u["name"])
                self.input_workno.setText(u["work_number"])
                self.input_dept.setText(u["department"])
                for i in range(self.combo_role.count()):
                    if self.combo_role.itemData(i) == u["role_name"]:
                        self.combo_role.setCurrentIndex(i)
                        break
                break

    def _save(self):
        from config.menu import ROLE_LEVELS
        name = self.input_name.text().strip()
        workno = self.input_workno.text().strip()
        dept = self.input_dept.text().strip()
        pwd = self.input_pwd.text().strip()
        if self.is_register:
            role = "操作员"
        else:
            role = self.combo_role.currentData()

        if not name or not workno:
            return QMessageBox.warning(self, "提示", "姓名和工号不能为空！")
        if self.is_register and (name.lower() == "admin" or workno.lower() == "admin"):
            return QMessageBox.warning(self, "禁止注册", "不能注册 admin 账号！")

        target_level = ROLE_LEVELS.get(role, 99)
        if not self.is_register and self.curr_user.lower() != "admin" and target_level < self.curr_level:
            return QMessageBox.critical(self, "拒绝操作", f"您的角色级别不足以分配【{role}】！")

        if role == "系统管理员" and any(u["role_name"] == "系统管理员" and u["ID"] != self.user_id for u in self.user_manager.query_user()):
            return QMessageBox.warning(self, "拒绝操作", "系统中已存在系统管理员，系统管理员全系统唯一！")

        if self.user_id:
            update_data = {"name": name, "department": dept, "role_name": role}
            if pwd:
                update_data["password"] = _PH.hash(pwd)
            if self.user_manager.update_user(self.user_id, update_data):
                self.accept()
            else:
                QMessageBox.critical(self, "失败", "修改失败！")
        else:
            if not pwd:
                return QMessageBox.warning(self, "提示", "密码不能为空！")
            if self.user_manager.add_user(name, workno, dept, role, _PH.hash(pwd), user_status=1):
                self.accept()
            else:
                QMessageBox.critical(self, "失败", "注册失败，工号或姓名可能已存在！")


class MesConnectThread(QThread):
    sig_finished = pyqtSignal(bool, str, str, str)
    def __init__(self, url, eqpno):
        super().__init__()
        self.url = url
        self.eqpno = eqpno

    @debug_thread
    def run(self):
        from businessclass_lx import MesService
        success, res, code, msg = MesService.connect_mes(self.url, self.eqpno)
        self.sig_finished.emit(success, res, code, msg)


class MesConfigPage(BasePage):
    title_key = "page.mes_config"
    def setup_ui(self):
        self.mes_thread = None
        self._build_layout()
        I18n.on_changed(self._refresh)
        self._refresh()

    def _build_layout(self):
        old_layout = self.layout()
        if old_layout is not None:
            QWidget().setLayout(old_layout)
            old_layout.deleteLater()

        main = QVBoxLayout(self)
        main.setContentsMargins(30, 30, 30, 30)
        main.setSpacing(20)
        top_layout = QHBoxLayout()
        top_layout.setSpacing(20)
        self.btn_mes_online = QPushButton()
        self.btn_mes_online.setFixedSize(120, 35)
        self.btn_mes_online.setObjectName("primaryBtn")
        self.btn_mes_online.clicked.connect(self._on_mes_online)
        self.btn_mes_offline = QPushButton()
        self.btn_mes_offline.setFixedSize(120, 35)
        self.btn_mes_offline.setObjectName("dangerBtn")
        self.btn_mes_offline.clicked.connect(self._on_mes_offline)
        top_layout.addStretch(1)
        top_layout.addWidget(self.btn_mes_online)
        top_layout.addWidget(self.btn_mes_offline)
        top_layout.addStretch(1)
        main.addLayout(top_layout)

        form_layout = QGridLayout()
        form_layout.setSpacing(15)
        form_layout.setColumnStretch(1, 1)
        self.inputs = {}
        fields = [("label_url", "input_url"), ("label_eqpno", "input_eqpno"), ("label_logpath", "input_logpath"), ("label_interval", "input_interval"), ("label_recipe", "input_recipe"), ("label_use", "input_use")]
        self.form_labels = []
        for row_idx, (lbl_key, inp_name) in enumerate(fields):
            lbl = QLabel()
            lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            lbl.setFixedWidth(160)
            inp = QLineEdit()
            inp.setFixedHeight(35)
            form_layout.addWidget(lbl, row_idx, 0)
            form_layout.addWidget(inp, row_idx, 1, 1, 2)
            lbl.setProperty("i18n_key", f"mes.{lbl_key}")
            self.inputs[inp_name] = inp
            self.form_labels.append(lbl)

        btn_layout = QHBoxLayout()
        self.btn_save = QPushButton()
        self.btn_save.setFixedSize(100, 35)
        self.btn_save.setObjectName("primaryBtn")
        self.btn_save.clicked.connect(self._on_save)
        btn_layout.addStretch(1)
        btn_layout.addWidget(self.btn_save)
        form_layout.addLayout(btn_layout, len(fields), 1, 1, 2)
        main.addLayout(form_layout)
        main.addSpacing(20)

        result_layout = QGridLayout()
        result_layout.setSpacing(15)
        self.lbl_result = QLabel()
        self.lbl_result.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.lbl_result.setFixedWidth(160)
        self.input_result = QLineEdit()
        self.input_result.setReadOnly(True)
        self.input_result.setFixedHeight(35)
        self.input_result.setStyleSheet("background-color: #f8fafc; color: #333;")
        self.lbl_result.setProperty("i18n_key", "mes.label_result")
        self.lbl_resultcode = QLabel()
        self.lbl_resultcode.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.lbl_resultcode.setFixedWidth(160)
        self.input_resultcode = QLineEdit()
        self.input_resultcode.setReadOnly(True)
        self.input_resultcode.setFixedHeight(35)
        self.input_resultcode.setStyleSheet("background-color: #f8fafc; color: #333;")
        self.lbl_resultcode.setProperty("i18n_key", "mes.label_resultcode")
        self.lbl_resultmsg = QLabel()
        self.lbl_resultmsg.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.lbl_resultmsg.setFixedWidth(160)
        self.input_resultmsg = QLineEdit()
        self.input_resultmsg.setReadOnly(True)
        self.input_resultmsg.setFixedHeight(35)
        self.input_resultmsg.setStyleSheet("background-color: #f8fafc; color: #333;")
        self.lbl_resultmsg.setProperty("i18n_key", "mes.label_resultmsg")

        result_layout.addWidget(self.lbl_result, 0, 0)
        result_layout.addWidget(self.input_result, 0, 1)
        result_layout.addWidget(self.lbl_resultcode, 1, 0)
        result_layout.addWidget(self.input_resultcode, 1, 1)
        result_layout.addWidget(self.lbl_resultmsg, 2, 0)
        result_layout.addWidget(self.input_resultmsg, 2, 1)
        main.addLayout(result_layout)
        main.addStretch(1)

    def _set_online_color(self, is_online: bool):
        if is_online:
            self.btn_mes_online.setStyleSheet("background-color: #22c55e; color: white; font-weight: bold; border-radius: 4px;")
            self.btn_mes_online.setText(I18n.t("mes.status_online", "MES：在线"))
        else:
            self.btn_mes_online.setStyleSheet("")
            self.btn_mes_online.setText(I18n.t("mes.btn_online", "MES上线"))

    @safe_slot
    def _refresh(self, lang=None):
        self.btn_mes_offline.setText(I18n.t("mes.btn_offline", "MES离线"))
        self.btn_save.setText(I18n.t("mes.btn_save", "保存"))
        if not self.mes_thread or not self.mes_thread.isRunning():
            if state.get("mes.status") == "online":
                self._set_online_color(True)
            else:
                self.btn_mes_online.setText(I18n.t("mes.btn_online", "MES上线"))
        for lbl in self.form_labels:
            lbl.setText(I18n.t(lbl.property("i18n_key"), lbl.text()))
        self.lbl_result.setText(I18n.t("mes.label_result", "Result:"))
        self.lbl_resultcode.setText(I18n.t("mes.label_resultcode", "ResultCode:"))
        self.lbl_resultmsg.setText(I18n.t("mes.label_resultmsg", "ResultMessage:"))

    @safe_slot
    def on_enter(self):
        self.apply_button_permissions({
            self.btn_mes_online: "mes.online",    # ★ 删掉 self.ui.
            self.btn_mes_offline: "mes.offline",  # ★ 删掉 self.ui.
            self.btn_save: "mes.save"
        })
        self._load_config()
        if str(state.get("mes.status", "offline")).strip().lower() == "online":
            self._set_online_color(True)
            self.input_result.setText("success")
            self.input_resultcode.setText("200")
            self.input_resultmsg.setText(I18n.t("mes.status_online", "MES：在线"))
        else:
            self._set_online_color(False)
            self.input_result.setText("Offline")
            self.input_resultcode.setText("200")
            self.input_resultmsg.setText(I18n.t("mes.msg_offline", "MES 已离线"))

    def _load_config(self):
        data = MesConfig().get_config()
        if data:
            self.inputs["input_url"].setText(str(data.get("mes_url") or ""))
            self.inputs["input_eqpno"].setText(str(data.get("eqpno") or ""))
            self.inputs["input_logpath"].setText(str(data.get("log_path") or ""))
            self.inputs["input_interval"].setText(str(data.get("eqp_refresh_interval") or ""))
            self.inputs["input_recipe"].setText(str(data.get("recipeid_codename") or ""))
            self.inputs["input_use"].setText(str(data.get("mes_use") or ""))

    @safe_slot
    @require_permission("mes.save", "保存MES配置")
    def _on_save(self, *args):
        update_data = {"mes_url": self.inputs["input_url"].text().strip(), "eqpno": self.inputs["input_eqpno"].text().strip(), "log_path": self.inputs["input_logpath"].text().strip(), "eqp_refresh_interval": self.inputs["input_interval"].text().strip(), "recipeid_codename": self.inputs["input_recipe"].text().strip(), "mes_use": self.inputs["input_use"].text().strip()}
        if MesConfig().update_config(update_data):
            if state.get("mes.status") == "online":
                state.set("mes.url", update_data["mes_url"])
                state.set("mes.eqpno", update_data["eqpno"])
                state.set("mes.interval", update_data["eqp_refresh_interval"])
            QMessageBox.information(self, I18n.t("msg.success", "成功"), "MES配置已成功保存到数据库！")
        else:
            QMessageBox.critical(self, I18n.t("msg.error", "失败"), "MES配置保存失败，请查看日志！")

    @safe_slot
    @require_permission("mes.online", "MES上线")
    def _on_mes_online(self, *args):
        if str(state.get("mes.status", "offline")).strip().lower() == "online":
            return QMessageBox.information(self, "提示", "MES 当前已在线，无需重复上线。")
        if self.mes_thread and self.mes_thread.isRunning():
            return
        url = self.inputs["input_url"].text().strip()
        eqpno = self.inputs["input_eqpno"].text().strip()
        if not url or not eqpno:
            return QMessageBox.warning(self, I18n.t("msg.warning", "提示"), I18n.t("mes.msg_empty", "URL 或 EQPNO 不能为空"))
        state.set("mes.auto_reconnect", "true")
        self.btn_mes_online.setEnabled(False)
        self.btn_mes_online.setStyleSheet("")
        self.btn_mes_online.setText(I18n.t("mes.btn_connecting", "连接中..."))
        self.input_result.setText("")
        self.input_resultcode.setText("")
        self.input_resultmsg.setText("")
        self.mes_thread = MesConnectThread(url, eqpno)
        self.mes_thread.sig_finished.connect(self._on_mes_connect_finished)
        self.mes_thread.start()

    @safe_slot
    def _on_mes_connect_finished(self, success, result, code, msg):
        self.btn_mes_online.setEnabled(True)
        self.input_result.setText(result)
        self.input_resultcode.setText(code)
        self.input_resultmsg.setText(msg)
        if success:
            self._set_online_color(True)
            state.set("mes.status", "online")
            state.set("mes.offline_reason", "")
            state.set("mes.url", self.inputs["input_url"].text().strip())
            state.set("mes.eqpno", self.inputs["input_eqpno"].text().strip())
            state.set("mes.interval", self.inputs["input_interval"].text().strip())
            QMessageBox.information(self, I18n.t("msg.success", "成功"), I18n.t("mes.msg_online_ok", "MES 上线成功！"))
        else:
            self._set_online_color(False)
            state.set("mes.status", "offline")
            QMessageBox.critical(self, I18n.t("msg.error", "失败"), f"{I18n.t('mes.msg_online_fail', 'MES 上线失败')}\n{msg}")
        if self.mes_thread:
            self.mes_thread.deleteLater()
            self.mes_thread = None

    @safe_slot
    @require_permission("mes.offline", "MES离线")
    def _on_mes_offline(self, *args):
        state.set("mes.status", "offline")
        state.set("mes.auto_reconnect", "false")
        state.set("mes.offline_reason", "manual")
        self._set_online_color(False)
        self.input_result.setText("Offline")
        self.input_resultcode.setText("200")
        self.input_resultmsg.setText(I18n.t("mes.msg_offline", "MES 已离线"))


class DepthPage(BasePage):
    title_key = "page.depth"
    SLOTS = ("4", "7")
    ROWS = ("upper", "actual", "lower")

    # ★ 配置真实的 PLC 地址（请去 plc_tags.py 核对并修改这里的地址）
    ACTUAL_MAP = {"4": "PLC1.DD1.D6745", "7": "PLC1.DD1.D6750"}
    UPPER_MAP = {"4": "PLC1.DD1.D6746", "7": "PLC1.DD1.D6751"}
    LOWER_MAP = {"4": "PLC1.DD1.D6748", "7": "PLC1.DD1.D6753"}

    def setup_ui(self):
        self.ui = DepthUi()
        self.ui.setupUi(self)
        self._build_layout()
        self._bind_events()

        I18n.on_changed(self._refresh)
        self._refresh()

        # 只创建定时器，不启动
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self._update_realtime_data)

    def _build_layout(self):
        u = self.ui

        # 清理幽灵控件
        ghost_names = ["label_unit_title", "label_row_upper", "label_row_actual", "label_row_lower",
                       "label_col_slot4", "label_col_slot7"]
        for name in ghost_names:
            w = getattr(u, name, None)
            if w:
                w.hide()
                w.deleteLater()

        old = self.layout()
        if old is not None:
            QWidget().setLayout(old)
            old.deleteLater()

        main = QVBoxLayout(self)
        main.setContentsMargins(30, 20, 30, 30)
        main.setSpacing(20)

        grid_layout = QGridLayout()
        grid_layout.setSpacing(20)

        for i, slot in enumerate(self.SLOTS):
            card = self._make_card(slot)
            row = i // 2
            col = i % 2
            grid_layout.addWidget(card, row, col)

        main.addLayout(grid_layout)
        main.addStretch(1)

        # ★ 底部返回按钮（补齐了这个，它就出来了）
        bottom_layout = QHBoxLayout()
        bottom_layout.addStretch(1)
        self.btn_back = QPushButton(I18n.t("btn.back", "< 返回生产记录"))
        self.btn_back.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_back.setFixedSize(140, 36)
        self.btn_back.setObjectName("ghostBtn")  # ★ 改回全局样式
        self.btn_back.clicked.connect(self._on_back_clicked)
        bottom_layout.addWidget(self.btn_back)
        main.addLayout(bottom_layout)

    def _make_card(self, slot):
        u = self.ui
        frame = QFrame()
        frame.setObjectName("card_frame")

        col_box = QVBoxLayout(frame)
        col_box.setContentsMargins(0, 0, 0, 15)
        col_box.setSpacing(15)

        header_frame = QFrame()
        header_frame.setObjectName("card_header")
        header_box = QHBoxLayout(header_frame)
        header_box.setContentsMargins(15, 10, 15, 10)

        title_lbl = getattr(u, f"label_card_slot{slot}_title")
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_lbl.setObjectName("card_title")

        unit_lbl = QLabel("mm")
        unit_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        unit_lbl.setObjectName("card_unit_title")

        header_box.addWidget(title_lbl, 1)
        header_box.addWidget(unit_lbl, 0)
        col_box.addWidget(header_frame)

        inner_layout = QVBoxLayout()
        inner_layout.setContentsMargins(15, 5, 15, 0)
        inner_layout.setSpacing(12)
        col_box.addLayout(inner_layout)

        for rk in self.ROWS:
            row_layout = QHBoxLayout()
            row_layout.setSpacing(8)

            # ★ 核心修改：初始化时给一个空字符串，保存到 self 字典里
            lbl = QLabel("")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setObjectName("card_row_label")
            if not hasattr(self, "_row_labels_dict"):
                self._row_labels_dict = {}
            self._row_labels_dict[f"{slot}_{rk}"] = lbl  # 存下来供刷新使用

            unit2 = QLabel("mm")
            unit2.setAlignment(Qt.AlignmentFlag.AlignCenter)
            unit2.setObjectName("card_row_unit")

            inp = getattr(u, f"input_slot{slot}_{rk}")
            inp.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            if rk == "actual":
                inp.setReadOnly(True)
                inp.setObjectName("card_input_actual")
            else:
                inp.setObjectName("card_input")

            row_layout.addWidget(lbl)
            row_layout.addWidget(unit2)
            row_layout.addWidget(inp)
            inner_layout.addLayout(row_layout)

        inner_layout.addSpacing(5)

        btn_save = QPushButton("")
        btn_save.setObjectName("primaryBtn")
        btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_save.clicked.connect(lambda checked, s=slot: self.on_save_modify(s))

        setattr(self, f"btn_save_slot{slot}", btn_save)
        inner_layout.addWidget(btn_save)
        inner_layout.addStretch(1)
        return frame


    @safe_slot
    def _on_back_clicked(self, *args):
        main_window = self.window()
        if hasattr(main_window, 'router'):
            main_window.router.go("monitor")

    @safe_slot
    def _update_realtime_data(self):
        from common.state import state
        if not state.conn:
            return

        def get_float(tag_cat, addr):
            tag_info = PLC_TAGS.get(tag_cat, {}).get(addr)
            if not tag_info:
                return None
            v = state.conn.hget("data_realtime", tag_info["redis"])
            if v is None or v in ("None", "NULL", "init", ""):
                return None
            try:
                return float(v)
            except:
                return None

        for slot in self.SLOTS:
            actual_val = get_float("monitor", self.ACTUAL_MAP[slot])
            if actual_val is not None:
                actual_str = f"{actual_val:.1f}"
            else:
                actual_str = ""
            getattr(self.ui, f"input_slot{slot}_actual").setText(actual_str)

    @safe_slot
    @require_permission("depth.edit", "修改深度参数")
    def on_save_modify(self, slot, *args):
        upper_val = getattr(self.ui, f"input_slot{slot}_upper").text().strip()
        lower_val = getattr(self.ui, f"input_slot{slot}_lower").text().strip()
        if not upper_val or not lower_val:
            return QMessageBox.warning(self, "提示", f"【{slot}#槽深度】的上限值和下限值不能为空！")

        reply = QMessageBox.question(self, "确认修改", f"确定下发【{slot}#槽深度】的上下限吗？",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.No:
            return

        try:
            opc = OpcService()
            opc.write_tag("monitor", self.UPPER_MAP[slot], float(upper_val), task_name="depth_edit")
            opc.write_tag("monitor", self.LOWER_MAP[slot], float(lower_val), task_name="depth_edit")
            QMessageBox.information(self, "指令已提交", f"【{slot}#槽深度】修改指令已下发至后台。")
        except Exception as e:
            QMessageBox.critical(self, "失败", f"下发 PLC 失败: {e}")

    @safe_slot
    def _refresh(self, lang=None):
        u = self.ui
        for slot in self.SLOTS:
            # 刷新标题
            title_text = I18n.t(f"depth.title_slot{slot}", f"{slot}#槽深度")
            getattr(u, f"label_card_slot{slot}_title").setText(title_text)

            # 刷新保存按钮
            save_text = I18n.t("btn.save_changes", "确定修改")
            getattr(self, f"btn_save_slot{slot}").setText(save_text)

            # ★ 核心修改：遍历并刷新刚才存下来的每一行标签
            for rk in self.ROWS:
                lbl = self._row_labels_dict.get(f"{slot}_{rk}")
                if lbl:
                    if rk == "upper":
                        lbl.setText(I18n.t("cond.upper", "上限值"))
                    elif rk == "actual":
                        lbl.setText(I18n.t("cond.actual", "实际值"))
                    elif rk == "lower":
                        lbl.setText(I18n.t("cond.lower", "下限值"))

        # 刷新返回按钮
        if hasattr(self, 'btn_back'):
            self.btn_back.setText(I18n.t("btn.back", "< 返回生产记录"))

        self._update_realtime_data()

    def _bind_events(self):
        for slot in self.SLOTS:
            set_input_validator(getattr(self.ui, f"input_slot{slot}_upper"), "float", 0.0, 9999.0, 1)
            set_input_validator(getattr(self.ui, f"input_slot{slot}_lower"), "float", 0.0, 9999.0, 1)


    def on_leave(self):
        if hasattr(self, 'refresh_timer') and self.refresh_timer.isActive():
            self.refresh_timer.stop()

    def on_enter(self):
        if hasattr(self, 'refresh_timer') and not self.refresh_timer.isActive():
            self.refresh_timer.start(1000)

        self.apply_button_permissions({
            self.btn_save_slot4: "depth.edit",
            self.btn_save_slot7: "depth.edit",
        })


class TemperaturePage(BasePage):
    title_key = "page.temp"

    # ★ 这里配置真实的 Redis/PLC 键名
    STATUS_KEY = "temp.slot7.heating"  # 假设加热状态存在这里 (1=加热中, 0=停止)
    TEMP_CTRL_KEY = "temp.slot7.auto"  # 假设温控(自动/手动)状态存在这里

    # 输入框对应的 PLC 地址（请根据实际情况修改）
    UPPER_MAP = "PLC1.DD1.D4600"
    SET_MAP = "PLC1.DD1.D4601"
    ACTUAL_MAP = "PLC1.DD1.D4602"
    LOWER_MAP = "PLC1.DD1.D4603"

    def setup_ui(self):
        self.ui = TempUi()
        self.ui.setupUi(self)

        self._bind_events()
        I18n.on_changed(self._refresh)
        self._refresh()

        # 定时器，用于每秒刷新实际值和按钮状态
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self._update_realtime_data)

    def _bind_events(self):
        self.ui.btn_heat.toggled.connect(self.on_heat_toggled)
        self.ui.btn_temp_control.toggled.connect(self.on_temp_control_toggled)
        self.ui.btn_save.clicked.connect(self.on_save_modify)
        self.ui.btn_back.clicked.connect(self._on_back_clicked)

    @safe_slot
    def _update_realtime_data(self):
        """每秒刷新状态和实际值"""
        from common.state import state
        if not state.conn:
            return

        # 1. 刷新按钮状态
        # 假设 1 为加热中，0 为停止
        heat_val = state.get(self.STATUS_KEY, "0")
        is_heating = str(heat_val) == "1"

        btn_heat = self.ui.btn_heat
        btn_heat.blockSignals(True)
        btn_heat.setChecked(is_heating)
        btn_heat.setText(
            I18n.t("temp.btn_heat_stop", "停止加热") if is_heating else I18n.t("temp.btn_heat_start", "开始加热"))
        btn_heat.blockSignals(False)

        # 温控按钮状态 (1=自动, 0=手动)
        ctrl_val = state.get(self.TEMP_CTRL_KEY, "0")
        is_auto = str(ctrl_val) == "1"

        btn_ctrl = self.ui.btn_temp_control
        btn_ctrl.blockSignals(True)
        btn_ctrl.setChecked(is_auto)
        btn_ctrl.setText(I18n.t("temp.btn_auto", "自动") if is_auto else I18n.t("temp.btn_manual", "手动"))
        btn_ctrl.blockSignals(False)

        # 2. 刷新实际值
        actual_val = state.conn.hget("data_realtime", self.ACTUAL_MAP)
        # 如果找不到数据或者数据无效，不更新（避免闪烁），或者你可以选择显示空字符串
        if actual_val and str(actual_val) not in ("None", "NULL", "init", ""):
            try:
                self.ui.inputs["actual"].setText(f"{float(actual_val):.2f}")
            except ValueError:
                pass

    @safe_slot
    def on_heat_toggled(self, checked):
        from common.state import state
        target_val = 1 if checked else 0
        try:
            # 这里写你的 PLC 写入逻辑
            # OpcService().write_tag("temp", "PLC_ADDR", target_val, task_name="temp_heat")

            # 为了让你直接看到效果，这里先用 Redis 模拟下发
            if state.conn:
                state.set(self.STATUS_KEY, str(target_val))
            self._update_realtime_data()  # 立即更新 UI
        except Exception as e:
            logger.error(f"加热控制失败: {e}")

    @safe_slot
    def on_temp_control_toggled(self, checked):
        from common.state import state
        target_val = 1 if checked else 0
        try:
            # 为了让你直接看到效果，这里先用 Redis 模拟下发
            if state.conn:
                state.set(self.TEMP_CTRL_KEY, str(target_val))
            self._update_realtime_data()
        except Exception as e:
            logger.error(f"温控切换失败: {e}")

    @safe_slot
    @require_permission("temp.edit", "修改温度参数")
    def on_save_modify(self, *args):
        upper_val = self.ui.inputs["upper"].text().strip()
        set_val = self.ui.inputs["set"].text().strip()
        lower_val = self.ui.inputs["lower"].text().strip()

        if not upper_val or not set_val or not lower_val:
            return QMessageBox.warning(self, "提示", "参数不能为空！")

        reply = QMessageBox.question(self, "确认修改", "确定下发【7#槽温度】参数吗？",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.No:
            return

        try:
            opc = OpcService()
            opc.write_tag("temp", self.UPPER_MAP, float(upper_val), task_name="temp_edit")
            opc.write_tag("temp", self.SET_MAP, float(set_val), task_name="temp_edit")
            opc.write_tag("temp", self.LOWER_MAP, float(lower_val), task_name="temp_edit")
            QMessageBox.information(self, "成功", "温度参数已下发至 PLC。")
        except Exception as e:
            logger.error(f"温度参数下发失败: {e}")
            QMessageBox.critical(self, "失败", f"下发失败: {e}")

    @safe_slot
    def _on_back_clicked(self, *args):
        main_window = self.window()
        if hasattr(main_window, 'router'):
            main_window.router.go("monitor")

    @safe_slot
    def _refresh(self, lang=None):
        # 刷新文字翻译
        self.ui.label_title.setText(I18n.t("temp.title", "7#槽温度"))
        self.ui.label_status.setText(I18n.t("temp.status", "当前状态"))
        self.ui.labels["upper"].setText(I18n.t("temp.upper", "上限值"))
        self.ui.labels["set"].setText(I18n.t("temp.set", "设定值"))
        self.ui.labels["actual"].setText(I18n.t("temp.actual", "实际值"))
        self.ui.labels["lower"].setText(I18n.t("temp.lower", "下限值"))
        self.ui.btn_save.setText(I18n.t("btn.save_changes", "确定修改"))
        self.ui.btn_back.setText(I18n.t("btn.back", "< 返回"))
        # self._update_realtime_data()
        is_heating = self.ui.btn_heat.isChecked()
        self.ui.btn_heat.setText(
            I18n.t("temp.btn_heat_stop", "停止加热") if is_heating else I18n.t("temp.btn_heat_start", "开始加热")
        )

        # 刷新温控按钮
        is_auto = self.ui.btn_temp_control.isChecked()
        self.ui.btn_temp_control.setText(
            I18n.t("temp.btn_auto", "自动") if is_auto else I18n.t("temp.btn_manual", "手动")
        )

    def on_enter(self):
        if hasattr(self, 'refresh_timer') and not self.refresh_timer.isActive():
            self.refresh_timer.start(1000)

        self.apply_button_permissions({
            self.ui.btn_heat: "heater.heat",
            self.ui.btn_temp_control: "heater.auto",
            self.ui.btn_save: "temp.edit"
        })


    def on_leave(self):
        if hasattr(self, 'refresh_timer') and self.refresh_timer.isActive():
            self.refresh_timer.stop()


def _ph(key):
    return lambda: PlaceholderPage(key)

PAGE_REGISTRY = {
    "crane_status": CraneStatusPage,
    "monitor": MonitorPage,
    "alarm": AlarmPage,
    "water": WaterPage,
    "conductivity/PH": ConductivityPage,
    "depth": DepthPage,
    "temp": TemperaturePage,
    "heater": HeaterPage,
    "user_manage": UserManagePage,
    "perm_manage": PermissionPage,
    "mes_config": MesConfigPage,
    "manual": _ph("page.manual"),
    "io_info": _ph("page.io_info"),
    "dip_time": _ph("page.dip_time"),
    "password": _ph("page.password"),
}