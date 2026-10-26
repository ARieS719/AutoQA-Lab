# tests/test_order.py
import pytest

from config import settings as app_config
from tests.schemas import OrderResponseSchema
from tests.utils import read_yaml_data


class TestOrderLifecycle:
    """
    企业级订单状态机全链路测试套件：
    融合【Pydantic 契约断言】+【MySQL 落库白盒断言】+【YAML 异常数据驱动】+【Teardown 自动清库】
    """

    def test_full_order_lifecycle(self, auth_session, db_connection, cleanup_order_db):
        print("\n--- [Test START] 开始执行多链路订单生命周期与状态机防御测试 ---")

        # ==========================================
        # 阶段一：正向链路 - 创建订单 (使用 QA_AUTO_ 前缀触发测试后自动清库)
        # ==========================================
        create_url = f"{app_config.BASE_URL}/api/v1/orders"
        payload = {"item_name": "QA_AUTO_工业级机械臂", "qty": 2}

        resp_create = auth_session.post(create_url, json=payload)
        assert resp_create.status_code == 200, f"下单失败: {resp_create.text}"

        # 【第一层断言：Pydantic 接口响应 Schema 契约强校验】
        validated_resp = OrderResponseSchema(**resp_create.json())
        order_id = validated_resp.data.order_id
        print(f"-> [步骤1] 下单成功且通过 Pydantic Schema 契约校验，订单 ID: {order_id}")

        # 【第二层断言：MySQL 数据库落库状态白盒校验】
        with db_connection.cursor() as cursor:
            cursor.execute("SELECT item_name, status, qty FROM orders WHERE id=%s", (order_id,))
            db_record = cursor.fetchone()

        assert db_record is not None, "白盒断言失败：数据库中未找到该订单记录"
        assert db_record["status"] == "PENDING", "订单初始状态异常"
        assert db_record["qty"] == 2, "订单落库数量不一致"
        print("-> [断言1] 数据库落库白盒校验通过 (PENDING)！")

        # ==========================================
        # 阶段二：正向链路 - 支付订单与状态流转
        # ==========================================
        pay_url = f"{app_config.BASE_URL}/api/v1/orders/{order_id}/pay"
        resp_pay = auth_session.post(pay_url)
        assert resp_pay.status_code == 200, "支付接口调用失败"

        with db_connection.cursor() as cursor:
            cursor.execute("SELECT status FROM orders WHERE id=%s", (order_id,))
            db_record = cursor.fetchone()

        assert db_record["status"] == "PAID", "订单支付后，数据库状态未流转为 PAID"
        print("-> [断言2] 数据库状态流转校验通过 (PENDING -> PAID)！")

        # ==========================================
        # 阶段三：逆向防线 - 状态机幂等与防篡改校验 (已支付订单禁止取消)
        # ==========================================
        cancel_url = f"{app_config.BASE_URL}/api/v1/orders/{order_id}/cancel"
        resp_cancel = auth_session.post(cancel_url)
        assert resp_cancel.status_code == 409, "状态机防御失效：已支付订单不应允许被取消！"

        with db_connection.cursor() as cursor:
            cursor.execute("SELECT status FROM orders WHERE id=%s", (order_id,))
            db_record = cursor.fetchone()

        assert db_record["status"] == "PAID", "严重Bug：接口虽返回409，但底层数据库状态被意外篡改！"
        print("-> [断言3] 状态机逆向防御与底层数据防篡改校验通过！")

    @pytest.mark.parametrize("case", read_yaml_data("order_abnormal_cases.yaml"))
    def test_create_order_abnormal_ddt(self, auth_session, case):
        """
        数据驱动测试 (DDT)：读取 YAML 中的异常边界值用例，验证网关参数拦截能力。
        断言升级：精确匹配期望状态码（原先 400/422 混放会掩盖网关行为漂移），
        并校验响应体携带参数校验失败文案。
        """
        create_url = f"{app_config.BASE_URL}/api/v1/orders"
        payload = case.get("payload", {"item_name": case.get("item_name", ""), "qty": case.get("qty", 0)})
        expected_status = case.get("expected_status", 422)
        expected_msg = case.get("expected_msg", "参数校验失败")

        resp = auth_session.post(create_url, json=payload)
        assert resp.status_code == expected_status, (
            f"异常用例未按预期拦截: {case.get('case_title')}, "
            f"期望 {expected_status}, 实际 {resp.status_code}"
        )
        # 响应体语义断言：错误详情存在且包含约定文案
        resp_body = resp.json()
        assert expected_msg in resp_body.get("detail", ""), (
            f"拦截响应文案缺失: {case.get('case_title')}, 响应: {resp_body}"
        )
