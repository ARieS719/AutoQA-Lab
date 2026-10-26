# ============================================================================
# mock_server/vehicle.py — 车联网第二业务域被测靶场
#
# 业务背景：面向车企的车辆云平台核心链路（面试可讲「为什么测开要懂业务域建模」）：
#   1. 车辆事件上报：VIN 强校验 + 功能域/风险等级 Literal 约束，落库 vehicle_events；
#   2. 远程指令下发：vehicle_commands 状态机 PENDING -> EXECUTED，CAS 防重复执行；
#   3. BMS 电池告警：高温告警上报与按 VIN 检索；
#   4. 座舱偏好同步：cabin_settings 按 VIN UPSERT（幂等写入）。
# 表结构见 db_init/02_vehicle_tables.sql（charging_orders / ota_tasks 两张表已预留，
# 作为「充电订单」「OTA 升级」链路的扩展位，README 有规划说明）。
# ============================================================================
import json

from typing import Dict, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger
from pydantic import BaseModel, Field

from mock_server.deps import get_db_connection, verify_token

vehicle_router = APIRouter()

# 17 位 VIN 校验（不含 I/O/Q，GB 16735 规则），在入口拦截非法车架号
VIN_PATTERN = r"^[A-HJ-NPR-Z0-9]{17}$"

DomainType = Literal["powertrain", "bms", "adas", "cockpit", "charging", "ota"]
RiskLevelType = Literal["LOW", "MEDIUM", "HIGH"]


# ============================================================================
# 1. Pydantic 契约模型（请求侧）
# ============================================================================
class VehicleEventRequest(BaseModel):
    vin: str = Field(..., pattern=VIN_PATTERN, description="17位车架号")
    domain: DomainType = Field(..., description="功能域: powertrain/bms/adas/cockpit/charging/ota")
    event_type: str = Field(..., min_length=1, max_length=50, description="事件类型")
    risk_level: RiskLevelType = Field("LOW", description="风险等级: LOW/MEDIUM/HIGH")
    payload: Optional[Dict] = Field(None, description="事件附加载荷 JSON")


class VehicleCommandRequest(BaseModel):
    vin: str = Field(..., pattern=VIN_PATTERN, description="17位车架号")
    command_type: str = Field(..., min_length=1, max_length=50, description="指令类型: 如 REMOTE_UNLOCK")
    params: Optional[Dict] = Field(None, description="指令参数 JSON")


class BmsAlertRequest(BaseModel):
    vin: str = Field(..., pattern=VIN_PATTERN, description="17位车架号")
    alert_level: RiskLevelType = Field(..., description="告警等级: LOW/MEDIUM/HIGH")
    highest_temp: float = Field(..., ge=-40, le=120, description="电池最高温度(℃)")


class CabinSettingsRequest(BaseModel):
    vin: str = Field(..., pattern=VIN_PATTERN, description="17位车架号")
    profile_data: Dict = Field(..., description="座舱偏好配置 JSON")


def _to_json_str(value: Optional[Dict]) -> Optional[str]:
    """Dict -> 合法 JSON 字符串（MySQL JSON 列要求合法 JSON，不能直接 str() 序列化）"""
    return json.dumps(value, ensure_ascii=False) if value is not None else None


# ============================================================================
# 2. 车辆事件上报链路
# ============================================================================
@vehicle_router.post("/api/v1/vehicle/events", tags=["6. 车联网：车辆事件"])
def report_vehicle_event(event: VehicleEventRequest, token: str = Depends(verify_token)):
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO vehicle_events (vin, domain, event_type, risk_level, payload) "
                "VALUES (%s, %s, %s, %s, %s)",
                (event.vin, event.domain, event.event_type, event.risk_level, _to_json_str(event.payload)),
            )
            event_id = cursor.lastrowid
        conn.commit()
    finally:
        conn.close()
    logger.info(f"[车联网] VIN={event.vin} 上报事件 {event.event_type} (风险等级 {event.risk_level})")
    return {
        "code": 200,
        "message": "success",
        "data": {"event_id": event_id, "vin": event.vin, "risk_level": event.risk_level},
    }


# ============================================================================
# 3. 远程指令状态机 (PENDING -> EXECUTED)
# ============================================================================
@vehicle_router.post("/api/v1/vehicle/commands", tags=["7. 车联网：远程指令"])
def create_vehicle_command(cmd: VehicleCommandRequest, token: str = Depends(verify_token)):
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO vehicle_commands (vin, command_type, params, status) "
                "VALUES (%s, %s, %s, 'PENDING')",
                (cmd.vin, cmd.command_type, _to_json_str(cmd.params)),
            )
            command_id = cursor.lastrowid
        conn.commit()
    finally:
        conn.close()
    return {
        "code": 200,
        "message": "success",
        "data": {"command_id": command_id, "vin": cmd.vin, "status": "PENDING"},
    }


@vehicle_router.post("/api/v1/vehicle/commands/{command_id}/execute", tags=["7. 车联网：远程指令"])
def execute_vehicle_command(command_id: int, token: str = Depends(verify_token)):
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT status FROM vehicle_commands WHERE id=%s", (command_id,))
            row = cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="指令不存在")
            if row["status"] != "PENDING":
                raise HTTPException(status_code=409, detail="指令已执行或已取消，禁止重复执行")
            # CAS：只允许 PENDING -> EXECUTED 的一次性流转
            cursor.execute(
                "UPDATE vehicle_commands SET status='EXECUTED' WHERE id=%s AND status='PENDING'",
                (command_id,),
            )
            if cursor.rowcount == 0:
                raise HTTPException(status_code=409, detail="指令状态已被并发修改")
        conn.commit()
    finally:
        conn.close()
    return {"code": 0, "msg": "指令执行成功", "data": {"command_id": command_id, "status": "EXECUTED"}}


@vehicle_router.get("/api/v1/vehicle/commands/{command_id}", tags=["7. 车联网：远程指令"])
def query_vehicle_command(command_id: int, token: str = Depends(verify_token)):
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT id, vin, command_type, status FROM vehicle_commands WHERE id=%s",
                (command_id,),
            )
            row = cursor.fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="指令不存在")
    return {"code": 200, "data": row}


# ============================================================================
# 4. BMS 电池告警链路
# ============================================================================
@vehicle_router.post("/api/v1/vehicle/bms/alerts", tags=["8. 车联网：BMS告警"])
def report_bms_alert(alert: BmsAlertRequest, token: str = Depends(verify_token)):
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO bms_alerts (vin, alert_level, highest_temp) VALUES (%s, %s, %s)",
                (alert.vin, alert.alert_level, alert.highest_temp),
            )
            alert_id = cursor.lastrowid
        conn.commit()
    finally:
        conn.close()
    logger.warning(f"[BMS告警] VIN={alert.vin} 最高温度 {alert.highest_temp}℃ (等级 {alert.alert_level})")
    return {"code": 200, "message": "success", "data": {"alert_id": alert_id}}


@vehicle_router.get("/api/v1/vehicle/bms/alerts", tags=["8. 车联网：BMS告警"])
def list_bms_alerts(
    vin: str = Query(..., pattern=VIN_PATTERN),
    limit: int = Query(10, ge=1, le=100),
    token: str = Depends(verify_token),
):
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT id, vin, alert_level, highest_temp, created_at FROM bms_alerts "
                "WHERE vin=%s ORDER BY id DESC LIMIT %s",
                (vin, limit),
            )
            rows = cursor.fetchall()
    finally:
        conn.close()
    return {"code": 200, "data": rows}


# ============================================================================
# 5. 座舱偏好 UPSERT（幂等写入）
# ============================================================================
@vehicle_router.post("/api/v1/vehicle/cabin/settings", tags=["9. 车联网：座舱偏好"])
def upsert_cabin_settings(req: CabinSettingsRequest, token: str = Depends(verify_token)):
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO cabin_settings (vin, profile_data) VALUES (%s, %s) "
                "ON DUPLICATE KEY UPDATE profile_data=VALUES(profile_data), updated_at=NOW()",
                (req.vin, _to_json_str(req.profile_data)),
            )
        conn.commit()
    finally:
        conn.close()
    return {"code": 200, "msg": "座舱偏好已同步（UPSERT 幂等写入）"}


@vehicle_router.get("/api/v1/vehicle/cabin/settings/{vin}", tags=["9. 车联网：座舱偏好"])
def get_cabin_settings(vin: str, token: str = Depends(verify_token)):
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT vin, profile_data, updated_at FROM cabin_settings WHERE vin=%s",
                (vin,),
            )
            row = cursor.fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="该 VIN 无座舱偏好记录")
    # MySQL JSON 列经 PyMySQL 返回字符串，反序列化回对象方便断言
    if row["profile_data"] and isinstance(row["profile_data"], str):
        row["profile_data"] = json.loads(row["profile_data"])
    return {"code": 200, "data": row}
