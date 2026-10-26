# tests/test_async_webhook.py — 异步 Webhook 签名回调链路测试
#
# 本版修复：
#   1. 签名算法同步升级为 HMAC-SHA256 + 时间戳防重放（原 MD5 为不安全算法）；
#   2. 服务端已改为「先 ACK 回执、后台异步流转状态」，本用例的轮询断言
#      现在是真实的异步等待（此前 requests.post 同步阻塞 0.5s，回调早已完成，
#      轮询逻辑形同虚设）；
#   3. 用例开头调用 reset-test-state 保证队列/状态确定性隔离。
import hashlib
import hmac
import time

import pytest
import requests

from config import settings

BASE_URL = f"{settings.BASE_URL}/api/v1"


def build_signature(order_id: str, amount: float, timestamp: str) -> str:
    """与 mock_server 保持一致的 HMAC-SHA256 签名生成器"""
    message = f"{order_id}|{amount}|{timestamp}".encode("utf-8")
    return hmac.new(settings.WEBHOOK_SECRET.encode("utf-8"), message, hashlib.sha256).hexdigest()


@pytest.fixture(scope="module", autouse=True)
def reset_async_state():
    """用例前归零 Webhook 队列与状态，避免历史消息干扰"""
    requests.post(f"{BASE_URL}/tools/reset-test-state", timeout=3)
    yield


def test_async_payment_webhook():
    """
    高级自动化测试：验证异步 Webhook 回调能够正确流转订单状态
    """
    test_order_id = f"ORDER_WB_{int(time.time())}"

    # 1. 验证初始状态必须是 PENDING
    initial_res = requests.get(f"{BASE_URL}/order/status/{test_order_id}", timeout=3)
    assert initial_res.json()["status"] == "PENDING"

    # 2. 伪装成第三方支付平台，主动向我们的系统发送一条带 HMAC-SHA256 签名的回调
    # (这一步往往通过独立的 Mock Server 触发，这里在测试脚本里直接发起)
    timestamp = str(int(time.time()))
    valid_signature = build_signature(test_order_id, 99.9, timestamp)

    callback_payload = {
        "order_id": test_order_id,
        "amount": 99.9,
        "signature": valid_signature,
        "timestamp": timestamp,
    }

    # 3. 核心：服务端「先 ACK 回执」，此时状态大概率仍是 PENDING（异步处理中）
    cb_response = requests.post(f"{BASE_URL}/webhook/pay_callback", params=callback_payload, timeout=3)
    assert cb_response.status_code == 200
    assert cb_response.json()["code"] == 200

    # 立刻查一次：由于后台消费者有 0.5s 处理耗时，这里必须容忍 PENDING
    instant_res = requests.get(f"{BASE_URL}/order/status/{test_order_id}", timeout=3).json()
    print(f"[异步验证] 回执后立刻查询状态: {instant_res['status']} (PENDING 才是真正的异步链路)")

    # 4. 轮询断言 (Polling Assertion)：最多等 3 秒，后台消费者把状态扭转为 PAID
    max_retries = 15
    poll_interval = 0.2

    final_status = "UNKNOWN"
    for i in range(max_retries):
        poll_res = requests.get(f"{BASE_URL}/order/status/{test_order_id}", timeout=3)
        current_status = poll_res.json()["status"]

        if current_status == "PAID":
            final_status = "PAID"
            print(f"\n 轮询成功！在第 {i+1} 次查询时发现状态扭转为 PAID")
            break

        print(f"第 {i+1} 次查询状态为 {current_status}，继续等待...")
        time.sleep(poll_interval)

    # 5. 终极断言：如果在规定时间内状态依然没有变成 PAID，说明系统的异步回调链路断了！
    assert final_status == "PAID", "严重故障：等待 3 秒后订单状态依然未流转！"
