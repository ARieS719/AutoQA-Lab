# tests/test_asgi_coverage.py — 进程内 ASGI 全分支白盒契约与覆盖率测试
#
# 通过 TestClient 直接挂载 FastAPI 实例（进程内执行），覆盖：
#   鉴权拦截 / 订单状态机全分支 / 秒杀 slow-fast 双模式 / Webhook 验签与异步流转 /
#   MQ 积分最终一致性 / 造数工厂边界，为 CI 覆盖率门禁 (--cov-fail-under=80)
#   提供确定性的覆盖率底座（不依赖偶发的外部进程行为）。
import hashlib
import hmac
import time

from fastapi.testclient import TestClient

from config import settings
from mock_server.app import app

BASE_PATH = "/api/v1"


def _build_signature(order_id: str, amount: float, timestamp: str) -> str:
    message = f"{order_id}|{amount}|{timestamp}".encode("utf-8")
    return hmac.new(settings.WEBHOOK_SECRET.encode("utf-8"), message, hashlib.sha256).hexdigest()


def _wait_for(client: TestClient, predicate, timeout: float = 4.0, interval: float = 0.2, description: str = "条件"):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise AssertionError(f"轮询超时: {description}")


def test_in_process_full_branches_coverage(db_connection):
    """
    进程内 ASGI 全分支白盒契约测试：
    采集真实服务端代码覆盖率（CI 覆盖率门禁的核心支撑）。
    """
    with TestClient(app) as client:
        # 0. 健康探针
        assert client.get("/health").status_code == 200

        # 1. 鉴权成功与失败分支 (401)
        res_login_ok = client.post("/api/v1/login", params={"username": "admin", "password": "123456"})
        assert res_login_ok.status_code == 200
        token = res_login_ok.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        res_login_fail = client.post("/api/v1/login", params={"username": "admin", "password": "wrong"})
        assert res_login_fail.status_code == 401

        res_unauth = client.post("/api/v1/orders", json={"item_name": "QA_AUTO_无Token测试", "qty": 1})
        assert res_unauth.status_code == 401

        # 2. 参数校验拦截器 422 分支
        res_422 = client.post("/api/v1/orders", json={"item_name": "", "qty": -1}, headers=headers)
        assert res_422.status_code == 422

        # 3. 订单全状态分支：创建 -> 取消 -> 取消后禁止支付(409) & 重复取消(409) & 不存在订单(404)
        res_create = client.post(
            "/api/v1/orders", json={"item_name": "QA_AUTO_覆盖率测试单", "qty": 1}, headers=headers
        )
        assert res_create.status_code == 200
        order_id = res_create.json()["data"]["order_id"]

        assert client.post(f"/api/v1/orders/{order_id}/cancel").status_code == 200
        assert client.post(f"/api/v1/orders/{order_id}/cancel").status_code == 409
        assert client.post(f"/api/v1/orders/{order_id}/pay").status_code == 409
        assert client.post("/api/v1/orders/999999999/pay").status_code == 404
        assert client.post("/api/v1/orders/999999999/cancel").status_code == 404

        # 4. 订单支付与重复支付拦截分支 (409, CAS 防重)
        res_create2 = client.post(
            "/api/v1/orders", json={"item_name": "QA_AUTO_支付分支单", "qty": 1}, headers=headers
        )
        order_id2 = res_create2.json()["data"]["order_id"]
        assert client.post(f"/api/v1/orders/{order_id2}/pay").status_code == 200
        assert client.post(f"/api/v1/orders/{order_id2}/pay").status_code == 409
        assert client.post(f"/api/v1/orders/{order_id2}/cancel").status_code == 409

        # 5. 秒杀分布式锁 slow 模式与 fast 模式全分支覆盖
        client.post("/api/v1/tools/init-test-stock/sku_cov/1")
        assert client.post("/api/v1/seckill/sku_cov?mode=slow").status_code == 200
        assert client.post("/api/v1/seckill/sku_cov?mode=slow").status_code == 200  # 触发 slow 库存不足分支

        client.post("/api/v1/tools/init-test-stock/sku_cov/1")
        assert client.post("/api/v1/seckill/sku_cov?mode=fast").status_code == 200
        assert client.post("/api/v1/seckill/sku_cov?mode=fast").status_code == 200  # 触发 fast 前置快速失败分支
        assert client.get("/api/v1/inventory/sku_cov").status_code == 200

        # 6. Webhook 验签分支：非法签名(403) / 过期时间戳(403) / 合法签名异步流转 / 重复回调(403)
        res_bad_sign = client.post(
            "/api/v1/webhook/pay_callback",
            params={"order_id": "ORD_BAD", "amount": 10.0, "signature": "invalid_md5", "timestamp": str(int(time.time()))},
        )
        assert res_bad_sign.json()["code"] == 403

        res_expired = client.post(
            "/api/v1/webhook/pay_callback",
            params={"order_id": "ORD_OLD", "amount": 10.0, "signature": "x", "timestamp": str(int(time.time()) - 3600)},
        )
        assert res_expired.json()["code"] == 403

        client.post(f"{BASE_PATH}/tools/reset-test-state")
        wb_order = "ORDER_WB_ASGI"
        wb_ts = str(int(time.time()))
        res_ok_sign = client.post(
            "/api/v1/webhook/pay_callback",
            params={
                "order_id": wb_order,
                "amount": 88.8,
                "signature": _build_signature(wb_order, 88.8, wb_ts),
                "timestamp": wb_ts,
            },
        )
        assert res_ok_sign.json()["code"] == 200
        # 异步链路：轮询直到消费者把状态流转为 PAID（状态存 Redis，跨进程一致）
        _wait_for(
            client,
            lambda: client.get(f"/api/v1/order/status/{wb_order}").json()["status"] == "PAID",
            description="Webhook 异步状态流转为 PAID",
        )
        # 幂等去重：同签名同时间戳重复回调被拦截 (403)
        res_replay = client.post(
            "/api/v1/webhook/pay_callback",
            params={
                "order_id": wb_order,
                "amount": 88.8,
                "signature": _build_signature(wb_order, 88.8, wb_ts),
                "timestamp": wb_ts,
            },
        )
        assert res_replay.json()["code"] == 403

        # 7. MQ 积分链路：先归零账本 -> 生产消息 -> 轮询 +100 最终一致性
        client.post(f"{BASE_PATH}/tools/reset-test-state")
        assert client.post(f"{BASE_PATH}/order/ORDER_MQ_ASGI/pay_and_notify").json()["code"] == 200
        _wait_for(
            client,
            lambda: client.get(f"{BASE_PATH}/user/points/user_888").json()["points"] >= 100,
            description="MQ 积分最终一致性达成",
        )
        assert client.get(f"{BASE_PATH}/user/points/user_unknown").json()["points"] == 0

        # 8. 造数工厂边界验证
        res_batch = client.post("/api/v1/tools/batch-orders", params={"count": 5})
        assert res_batch.json()["inserted_count"] == 5
        assert "error" in client.post("/api/v1/tools/batch-orders", params={"count": 0}).json()
        assert "error" in client.post("/api/v1/tools/batch-orders", params={"count": 100001}).json()

    # 清理本次生成的测试数据
    with db_connection.cursor() as cursor:
        cursor.execute("DELETE FROM orders WHERE item_name LIKE 'QA_AUTO_%' OR item_name LIKE '批量测试商品_%'")
    db_connection.commit()
