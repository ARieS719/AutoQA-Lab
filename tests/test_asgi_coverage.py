# tests/test_asgi_coverage.py
import pytest
from fastapi.testclient import TestClient
from mock_server.app import app

def test_in_process_full_branches_coverage(db_connection):
    """
    进程内 ASGI 全分支白盒契约测试：
    通过 TestClient 直接挂载 FastAPI 实例，覆盖鉴权拦截、订单异常分支、
    秒杀 slow/fast 双模式、Webhook 验签及批量造数工厂，采集真实服务端代码覆盖率。
    """
    with TestClient(app) as client:
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
        res_create = client.post("/api/v1/orders", json={"item_name": "QA_AUTO_覆盖率测试单", "qty": 1}, headers=headers)
        assert res_create.status_code == 200
        order_id = res_create.json()["data"]["order_id"]

        assert client.post(f"/api/v1/orders/{order_id}/cancel").status_code == 200
        assert client.post(f"/api/v1/orders/{order_id}/cancel").status_code == 409
        assert client.post(f"/api/v1/orders/{order_id}/pay").status_code == 409
        assert client.post("/api/v1/orders/999999999/pay").status_code == 404
        assert client.post("/api/v1/orders/999999999/cancel").status_code == 404

        # 4. 订单支付与重复支付拦截分支 (409)
        res_create2 = client.post("/api/v1/orders", json={"item_name": "QA_AUTO_支付分支单", "qty": 1}, headers=headers)
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

        # 6. Webhook 非法签名拦截 (403) 与造数工厂边界验证
        res_bad_sign = client.post(
            "/api/v1/webhook/pay_callback",
            params={"order_id": "ORD_BAD", "amount": 10.0, "signature": "invalid_md5"}
        )
        assert res_bad_sign.json()["code"] == 403

        res_batch = client.post("/api/v1/tools/batch-orders", params={"count": 5})
        assert res_batch.json()["inserted_count"] == 5
        assert "error" in client.post("/api/v1/tools/batch-orders", params={"count": 0}).json()

    # 清理本次生成的测试数据
    with db_connection.cursor() as cursor:
        cursor.execute("DELETE FROM orders WHERE item_name LIKE 'QA_AUTO_%' OR item_name LIKE '批量测试商品_%'")
    db_connection.commit()