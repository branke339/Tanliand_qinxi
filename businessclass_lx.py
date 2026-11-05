import os
from openpyxl import load_workbook, Workbook
import datetime
import time
import re
import json
import requests
import pymysql

from PyQt6.QtWidgets import QFileDialog, QMessageBox
import datetime
import os
from openpyxl import Workbook
from PyQt6.QtWidgets import QTableWidgetItem
from common.QMessageBoxTool import QMessageBoxDemo
import tkinter as tk
from log import operator_log

# ==================== SQLAlchemy ORM 相关导入 ====================
from sqlalchemy import create_engine, Column, Integer, String, DateTime, or_
from sqlalchemy.orm import declarative_base, sessionmaker
from constant.constant import logger
from database.connect_db import HOST, PORT, USERNAME, PASSWORD, DB_NAME

ooperator = operator_log("dd1")

# ==================== ORM 模型定义与数据库连接 ====================
Base = declarative_base()


class AlarmEventDetailModel(Base):
    """映射数据库表 alarm_event_detail"""
    __tablename__ = 'alarm_event_detail'

    ID = Column(Integer, primary_key=True, autoincrement=True)
    alarm_uuid = Column(String)
    equipment_id = Column(String)
    equipment_name = Column(String)
    alarm_type = Column(String)
    alarm_name = Column(String)
    alarm_start_time = Column(DateTime)
    alarm_end_time = Column(DateTime)
    alarm_duration_time = Column(String)
    alarm_class_code = Column(String)
    alarm_grade_code = Column(String)
    alarm_group_code = Column(String)
    alarm_status = Column(String)


class UserModel(Base):
    """映射数据库表 user"""
    __tablename__ = 'user'

    ID = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(50), unique=True)
    work_number = Column(String(50), unique=True)
    department = Column(String(100))
    role_name = Column(String(50))
    password = Column(String(255))
    user_status = Column(Integer, default=1)
    create_time = Column(DateTime, default=datetime.datetime.now)


class RolePermissionModel(Base):
    """映射数据库表 sys_role_permission"""
    __tablename__ = 'sys_role_permission'
    id = Column(Integer, primary_key=True, autoincrement=True)
    role_name = Column(String(50), index=True)
    permission_code = Column(String(100), index=True)


class MesConfigModel(Base):
    """映射数据库表 mes_config (★ 你刚才误删的，我补回来了)"""
    __tablename__ = 'mes_config'

    ID = Column(Integer, primary_key=True, autoincrement=True)
    mes_url = Column(String(255))
    eqpno = Column(String(50))
    log_path = Column(String(255))
    eqp_refresh_interval = Column(String(50))
    recipeid_codename = Column(String(100))
    mes_use = Column(String(10))


# 构建数据库连接 URL
DB_URL = f"mysql+pymysql://{USERNAME}:{PASSWORD}@{HOST}:{PORT}/{DB_NAME}?charset=utf8mb4"
engine = create_engine(
    DB_URL,
    echo=False,
    pool_pre_ping=True,  # ★ 自动探测死连接，防止假死
    pool_recycle=3600
)
SessionLocal = sessionmaker(bind=engine)


# =====================================================================

def to_dict(obj):
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}


def export_xls(Atablewidget, Afilename):
    try:
        ss = datetime.datetime.now().strftime('%Y-%m-%d')
        default_name = f"{Afilename}_{ss}.xlsx"

        # 【修改点】：使用 PyQt6 原生的保存对话框，和你的主程序更配
        ofilename, _ = QFileDialog.getSaveFileName(
            None,
            "保存Excel文件",
            os.path.join(os.path.expanduser("~"), default_name),
            "Excel Files (*.xlsx)"
        )

        if ofilename:
            wb = Workbook()
            ws = wb.active
            for col in range(Atablewidget.columnCount()):
                cell = ws.cell(row=1, column=col + 1)
                cell.value = Atablewidget.horizontalHeaderItem(col).text()
            for row in range(Atablewidget.rowCount()):
                for col in range(Atablewidget.columnCount()):
                    cell = ws.cell(row=row + 2, column=col + 1)
                    item = Atablewidget.item(row, col)
                    if item:
                        cell.value = item.text()
                    else:
                        cell.value = ""

            if not ofilename.endswith(".xlsx"):
                ofilename += ".xlsx"

            wb.save(ofilename)
            wb.close()

            # 使用 PyQt6 的原生弹窗
            QMessageBox.information(None, "成功", "导出xls文件成功！")
    except Exception as err:
        print("导出Excel出错:", err)


class Alarm_event_detail(object):

    def query_alarm_name(self, Akeyword, Astart_time, Aend_time):
        """使用 SQLAlchemy ORM 查询告警事件"""
        try:
            start_dt = datetime.datetime.strptime(Astart_time, '%Y-%m-%d %H:%M:%S')
            end_dt = datetime.datetime.strptime(Aend_time, '%Y-%m-%d %H:%M:%S')
        except ValueError:
            logger.error("时间格式不正确，请使用 YYYY-MM-DD HH:MM:SS 格式")
            return []

        with SessionLocal() as session:
            try:
                query = session.query(AlarmEventDetailModel)
                if Akeyword:
                    query = query.filter(AlarmEventDetailModel.alarm_name.like(f"%{Akeyword}%"))

                query = query.filter(
                    AlarmEventDetailModel.alarm_start_time >= start_dt,
                    AlarmEventDetailModel.alarm_start_time <= end_dt
                )
                query = query.order_by(AlarmEventDetailModel.ID.desc())
                results = query.all()
                return [to_dict(row) for row in results]

            except Exception as err:
                logger.warning(f"【告警查询】数据库异常: {str(err).split(chr(10))[0]}")
                session.rollback()
                return []  # ★ 安全返回空列表，UI 不会崩

    def get_recent_alarms(self, hours=24, limit=20):
        """获取最近 N 小时内，且未处理的报警（用于生产看板）"""
        start_dt = datetime.datetime.now() - datetime.timedelta(hours=hours)

        with SessionLocal() as session:
            try:
                query = session.query(AlarmEventDetailModel).filter(
                    AlarmEventDetailModel.alarm_start_time >= start_dt,
                    AlarmEventDetailModel.alarm_status != "已处理"
                )
                results = query.order_by(AlarmEventDetailModel.ID.desc()).limit(limit).all()
                return [to_dict(row) for row in results]
            except Exception as err:
                err_msg = str(err).split('\n')[0]
                logger.warning(f"数据库连接异常，跳过本次报警查询: {err_msg}")
                session.rollback()
                return None


class User(object):
    def query_user(self, Akeyword=None, Awork_number=None):
        """查询用户"""
        with SessionLocal() as session:
            try:
                query = session.query(UserModel)
                if Awork_number:
                    query = query.filter(UserModel.work_number == Awork_number)
                if Akeyword:
                    query = query.filter(UserModel.name.like(f"%{Akeyword}%"))
                query = query.order_by(UserModel.ID.desc())
                results = query.all()
                return [to_dict(row) for row in results]
            except Exception as err:
                logger.warning(f"【用户查询】数据库异常（可能已断开）: {str(err).split(chr(10))[0]}")
                session.rollback()
                return []

    def add_user(self, name, work_number, department, role_name, password, user_status=1):
        with SessionLocal() as session:
            try:
                new_user = UserModel(name=name, work_number=work_number, department=department, role_name=role_name,
                                     password=password, user_status=user_status)
                session.add(new_user)
                session.commit()
                return True
            except Exception as err:
                session.rollback()
                logger.error(f"新增用户失败: {err}")
                return False

    def update_user(self, Auser_id, Aupdate_data):
        with SessionLocal() as session:
            try:
                user = session.query(UserModel).filter(UserModel.ID == Auser_id).first()
                if not user:
                    return False
                for key, value in Aupdate_data.items():
                    if hasattr(user, key):
                        setattr(user, key, value)
                session.commit()
                return True
            except Exception as err:
                session.rollback()
                logger.error(f"更新用户失败: {err}")
                return False

    def delete_user(self, Auser_id):
        with SessionLocal() as session:
            try:
                user = session.query(UserModel).filter(UserModel.ID == Auser_id).first()
                if not user:
                    return False
                session.delete(user)
                session.commit()
                return True
            except Exception as err:
                session.rollback()
                logger.error(f"删除用户失败: {err}")
                return False


class RolePermission(object):
    def get_permissions(self, role_name):
        with SessionLocal() as session:
            try:
                records = session.query(RolePermissionModel).filter(RolePermissionModel.role_name == role_name).all()
                return [r.permission_code for r in records]
            except Exception as err:
                logger.warning(f"【权限查询】数据库异常: {str(err).split(chr(10))[0]}")
                session.rollback()
                return []

    def save_permissions(self, role_name, code_list):
        with SessionLocal() as session:
            try:
                session.query(RolePermissionModel).filter(RolePermissionModel.role_name == role_name).delete()
                for code in code_list:
                    session.add(RolePermissionModel(role_name=role_name, permission_code=code))
                session.commit()
                return True
            except Exception as err:
                session.rollback()
                logger.error(f"保存权限失败: {err}")
                return False


class MesConfig(object):
    def get_config(self):
        with SessionLocal() as session:
            try:
                res = session.query(MesConfigModel).first()
                if res:
                    return to_dict(res)
                else:
                    return None
            except Exception as err:
                logger.warning(f"【MES配置读取】数据库异常: {str(err).split(chr(10))[0]}")
                session.rollback()
                return None

    def update_config(self, update_data):
        with SessionLocal() as session:
            try:
                res = session.query(MesConfigModel).first()
                if not res:
                    res = MesConfigModel()
                    session.add(res)
                for k, v in update_data.items():
                    if hasattr(res, k):
                        setattr(res, k, v)
                session.commit()
                return True
            except Exception as err:
                session.rollback()
                logger.error(f"保存MES配置失败: {err}")
                return False


class MesService:
    """处理 MES 通讯（纯粹的网络请求，不涉及 UI）"""

    @staticmethod
    def connect_mes(url: str, eqpno: str) -> tuple[bool, str, str, str]:
        if not url or not eqpno:
            logger.error("【MES上线失败】URL 或 EQPNO 为空")
            return False, "Error", "-1", "URL or EQPNO is empty"

        api_url = f"{url.rstrip('/')}/EqpTransaction"
        payload = {
            "pParameter": json.dumps({
                "TransactionName": "CONNECTION",
                "EqpNo": eqpno
            })
        }

        try:
            resp = requests.get(api_url, params=payload, timeout=5)
            resp.raise_for_status()
            text = resp.text.strip()

            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                json_str = match.group(0).replace('{{', '{').replace('}}', '}')
                data = json.loads(json_str)
            else:
                data = resp.json()

            res_result = data.get("Result", "fail")
            if str(res_result).lower() == "success":
                return True, res_result, data.get("ResultCode", "200"), data.get("ResultMessage", "Success")
            else:
                res_msg = data.get("ResultMessage", "Failed")
                logger.warning(f"【MES上线失败】URL: {url}, 业务返回: {res_msg}")
                return False, res_result, data.get("ResultCode", "-1"), res_msg

        except requests.Timeout:
            logger.error(f"【MES上线超时】URL: {url}, 连接超过 5 秒无响应")
            return False, "Timeout", "-1", "连接超时，请检查网线或服务器是否开启"
        except requests.ConnectionError:
            logger.error(f"【MES上线连接异常】URL: {url}, 无法连接到服务器")
            return False, "Error", "-1", "网络连接失败，请检查 IP 是否正确"
        except Exception as e:
            logger.error(f"【MES上线未知异常】URL: {url}, 错误: {str(e)}")
            return False, "Error", "-1", f"Exception: {str(e)}"

    @staticmethod
    def upload_equipment_status(url: str, payload_dict: dict) -> tuple[bool, str, str, str]:
        if not url:
            return False, "Error", "-1", "URL is empty"

        api_url = f"{url.rstrip('/')}/EqpTransaction"

        try:
            resp = requests.post(
                api_url,
                data={"pParameter": json.dumps(payload_dict)},
                timeout=5
            )
            resp.raise_for_status()
            text = resp.text.strip()

            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                json_str = match.group(0).replace('{{', '{').replace('}}', '}')
                data = json.loads(json_str)
            else:
                data = resp.json()

            res_result = data.get("Result", "fail")
            if str(res_result).lower() == "success":
                return True, res_result, data.get("ResultCode", "200"), data.get("ResultMessage", "OK")
            else:
                return False, res_result, data.get("ResultCode", "-1"), data.get("ResultMessage", "Failed")

        except requests.Timeout:
            logger.error(f"【MES上报超时】URL: {url}, 连接超过 5 秒无响应")
            return False, "Timeout", "-1", "上报超时"
        except Exception as e:
            logger.error(f"【MES上报异常】URL: {url}, 错误: {str(e)}")
            return False, "Error", "-1", f"Exception: {str(e)}"