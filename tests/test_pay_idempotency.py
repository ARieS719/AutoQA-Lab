# tests/test_pay_idempotency.py — 并发重复支付幂等防护测试（CAS 原子条件更新验证）
#
# 面试点：支付接口的防重设计。多个并发请求对同一订单发起支付，
# 必须恰好 1 个成功、其余全部 409，且数据库最终状态唯一确定（PAID）。
import concurrent.futures

import pytest
import requests

from config import settings

BASE_URL = f"{settings.BASE_URL}/api/v1"


@pytest.fixture
def pending_order(auth_session):
    """创建一个待支付订单，返回 order_id"""
    resp = auth_session.post(
        f"{BASE_URL}/orders",
        json={"item_name": "QA_AUTO_并发支付幂等测试单", "qty": 1},
        timeout=3,
    )
    assert resp.status_code == 200
    return resp.json()["data"]["order_id"]


def test_concurrent_pay_only_one_success(auth_session, db_connection, pending_order, cleanup_order_db):
    """
    核心防重用例：10 线程同时对同一订单发起支付，
    断言恰有 1 个 200、9 个 409，且数据库状态为 PAID（杜绝重复扣款）。
    """
    order_id = pending_order
    concurrent_users = 10
    # requests.Session 官方不保证线程安全：并发线程各自发起独立请求，仅共享只读 headers
    auth_headers = dict(auth_session.headers)

    def send_pay_request():
        return requests.post(f"{BASE_URL}/orders/{order_id}/pay", headers=auth_headers, timeout=10)

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrent_users) as executor:
        futures = [executor.submit(send_pay_request) for _ in range(concurrent_users)]
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result().status_code)

    success_count = sum(1 for code in results if code == 200)
    conflict_count = sum(1 for code in results if code == 409)

    # 1. 幂等铁律：并发支付恰好 1 人成功
    assert success_count == 1, f"严重 Bug！并发支付成功 {success_count} 次，存在重复扣款风险！"

    # 2. 其余请求必须被状态机拦下（409 冲突），不允许出现 500
    assert conflict_count == concurrent_users - 1, f"状态码分布异常: {results}"
    assert all(code in (200, 409) for code in results), f"出现异常状态码: {results}"

    # 3. 白盒核验：数据库最终状态唯一确定为 PAID
    with db_connection.cursor() as cursor:
        cursor.execute("SELECT status FROM orders WHERE id=%s", (order_id,))
        row = cursor.fetchone()
    assert row is not None and row["status"] == "PAID", "数据库状态与幂等语义不一致！"
    print(f"\n[幂等验证通过] 并发 10 发支付: 1 成功 + {conflict_count} 冲突，落库状态唯一 (PAID)")
