# tests/test_vehicle.py — 车联网第二业务域全链路测试（契约 + 落库白盒 + 状态机防御）
#
# 垂直覆盖车企车云平台四类核心链路：
#   车辆事件上报 / 远程指令状态机 / BMS 告警 / 座舱偏好 UPSERT，
# 断言风格与电商域保持一致：Pydantic 契约层 + MySQL 落库层双保险。
import pytest

from fastapi.testclient import TestClient

from mock_server.app import app

# 合法测试 VIN（17 位，符合 GB 16735）
TEST_VIN = "LSVAM4187C2184847"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def auth_headers(client):
    token = client.post("/api/v1/login", params={"username": "admin", "password": "123456"}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


class TestVehicleEvents:
    """车辆事件上报链路"""

    def test_report_vehicle_event_success(self, client, auth_headers, db_connection):
        payload = {
            "vin": TEST_VIN,
            "domain": "bms",
            "event_type": "BATTERY_TEMP_SAMPLE",
            "risk_level": "HIGH",
            "payload": {"cell_voltage": [3.7, 3.8], "soc": 62},
        }
        resp = client.post("/api/v1/vehicle/events", json=payload, headers=auth_headers)
        assert resp.status_code == 200
        event_id = resp.json()["data"]["event_id"]

        # 白盒落库核验
        with db_connection.cursor() as cursor:
            cursor.execute("SELECT vin, domain, risk_level FROM vehicle_events WHERE id=%s", (event_id,))
            row = cursor.fetchone()
        assert row is not None
        assert row["vin"] == TEST_VIN and row["domain"] == "bms" and row["risk_level"] == "HIGH"

    @pytest.mark.parametrize(
        "bad_payload",
        [
            {"vin": "SHORT_VIN", "domain": "bms", "event_type": "X"},                      # VIN 位数非法
            {"vin": TEST_VIN, "domain": "infotainment", "event_type": "X"},                # 非法功能域
            {"vin": TEST_VIN, "domain": "bms", "event_type": "X", "risk_level": "CRITICAL"},  # 非法风险等级
            {"vin": TEST_VIN, "domain": "bms"},                                            # 缺少必填字段
        ],
    )
    def test_report_vehicle_event_validation(self, client, auth_headers, bad_payload):
        resp = client.post("/api/v1/vehicle/events", json=bad_payload, headers=auth_headers)
        assert resp.status_code == 422, f"非法 Payload 应被 422 拦截: {bad_payload}"


class TestVehicleCommands:
    """远程指令状态机链路"""

    def test_command_lifecycle_with_state_machine_defense(self, client, auth_headers, db_connection):
        # 1. 下发指令
        create_resp = client.post(
            "/api/v1/vehicle/commands",
            json={"vin": TEST_VIN, "command_type": "REMOTE_UNLOCK", "params": {"reason": "owner_request"}},
            headers=auth_headers,
        )
        assert create_resp.status_code == 200
        command_id = create_resp.json()["data"]["command_id"]

        # 2. 执行指令: PENDING -> EXECUTED
        exec_resp = client.post(f"/api/v1/vehicle/commands/{command_id}/execute", headers=auth_headers)
        assert exec_resp.status_code == 200
        assert exec_resp.json()["data"]["status"] == "EXECUTED"

        # 3. 重复执行被状态机拦截 (409)
        repeat_resp = client.post(f"/api/v1/vehicle/commands/{command_id}/execute", headers=auth_headers)
        assert repeat_resp.status_code == 409

        # 4. 白盒落库状态核验
        with db_connection.cursor() as cursor:
            cursor.execute("SELECT status FROM vehicle_commands WHERE id=%s", (command_id,))
            row = cursor.fetchone()
        assert row["status"] == "EXECUTED"

    def test_execute_nonexistent_command_404(self, client, auth_headers):
        resp = client.post("/api/v1/vehicle/commands/99999999/execute", headers=auth_headers)
        assert resp.status_code == 404


class TestBmsAlerts:
    """BMS 电池告警链路"""

    def test_report_and_query_bms_alerts(self, client, auth_headers, db_connection):
        # 高温告警上报
        resp = client.post(
            "/api/v1/vehicle/bms/alerts",
            json={"vin": TEST_VIN, "alert_level": "HIGH", "highest_temp": 52.5},
            headers=auth_headers,
        )
        assert resp.status_code == 200

        # 按 VIN 检索告警列表
        list_resp = client.get(f"/api/v1/vehicle/bms/alerts?vin={TEST_VIN}&limit=5", headers=auth_headers)
        assert list_resp.status_code == 200
        alerts = list_resp.json()["data"]
        assert any(a["highest_temp"] == 52.5 for a in alerts)

        # 白盒佐证落库
        with db_connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS cnt FROM bms_alerts WHERE vin=%s", (TEST_VIN,))
            assert cursor.fetchone()["cnt"] >= 1

    def test_bms_alert_temperature_boundary(self, client, auth_headers):
        # 温度越界（-40 ~ 120℃ 之外）应被契约层拦截
        resp = client.post(
            "/api/v1/vehicle/bms/alerts",
            json={"vin": TEST_VIN, "alert_level": "HIGH", "highest_temp": 999},
            headers=auth_headers,
        )
        assert resp.status_code == 422


class TestCabinSettings:
    """座舱偏好 UPSERT 链路"""

    def test_cabin_settings_upsert_idempotent(self, client, auth_headers, db_connection):
        vin = "WVWZZZ1JZXW000001"
        payload = {"vin": vin, "profile_data": {"seat_memory": 1, "climate_temp": 23, "ambient_color": "blue"}}

        # 首次写入
        first = client.post("/api/v1/vehicle/cabin/settings", json=payload, headers=auth_headers)
        assert first.status_code == 200

        # 二次覆盖（UPSERT 幂等）
        payload["profile_data"]["climate_temp"] = 25
        second = client.post("/api/v1/vehicle/cabin/settings", json=payload, headers=auth_headers)
        assert second.status_code == 200

        # 查询核验：只有一条记录且为最新值
        query = client.get(f"/api/v1/vehicle/cabin/settings/{vin}", headers=auth_headers)
        assert query.status_code == 200
        profile = query.json()["data"]["profile_data"]
        assert profile["climate_temp"] == 25, "UPSERT 应返回最新覆盖值"

        with db_connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS cnt FROM cabin_settings WHERE vin=%s", (vin,))
            assert cursor.fetchone()["cnt"] == 1, "UPSERT 应保持单记录，出现重复行！"

    def test_cabin_settings_missing_record_404(self, client, auth_headers):
        resp = client.get("/api/v1/vehicle/cabin/settings/WVWZZZ1JZXW000002", headers=auth_headers)
        assert resp.status_code == 404
