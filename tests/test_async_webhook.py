import time
import pytest
import requests
import hashlib

BASE_URL = "http://127.0.0.1:8000/api/v1"
SECRET = "super_secret_key_from_alipay"

def test_async_payment_webhook():
    """
    高级自动化测试：验证异步 Webhook 回调能够正确流转订单状态
    """
    test_order_id = f"ORDER_WB_{int(time.time())}"
    
    # 1. 验证初始状态必须是 PENDING
    initial_res = requests.get(f"{BASE_URL}/order/status/{test_order_id}")
    assert initial_res.json()["status"] == "PENDING"
    
    # 2. 伪装成第三方支付平台，主动向我们的系统发送一条带签名的回调
    # (这一步往往通过独立的 Mock Server 触发，这里在测试脚本里直接发起)
    sign_str = f"{test_order_id}|99.9|{SECRET}"
    valid_signature = hashlib.md5(sign_str.encode()).hexdigest()
    
    callback_payload = {
        "order_id": test_order_id,
        "amount": 99.9,
        "signature": valid_signature
    }
    
    # 发送回调请求（不要阻塞后续代码，模拟异步发生）
    cb_response = requests.post(
        f"{BASE_URL}/webhook/pay_callback", 
        params=callback_payload
    )
    assert cb_response.status_code == 200
    
    # 3. 核心挑战：轮询断言 (Polling Assertion)
    # 因为系统处理回调需要时间（代码里 sleep 了 0.5 秒），如果立刻查状态，大概率是 PENDING。
    # 写一个轮询器，每隔 0.2 秒去查一次，最多查 10 次（即最多等 2 秒）。
    max_retries = 10
    poll_interval = 0.2
    
    final_status = "UNKNOWN"
    for i in range(max_retries):
        poll_res = requests.get(f"{BASE_URL}/order/status/{test_order_id}")
        current_status = poll_res.json()["status"]
        
        if current_status == "PAID":
            final_status = "PAID"
            print(f"\n 轮询成功！在第 {i+1} 次查询时发现状态扭转为 PAID")
            break
            
        print(f"第 {i+1} 次查询状态为 {current_status}，继续等待...")
        time.sleep(poll_interval)
        
    # 4. 终极断言：如果在规定时间内状态依然没有变成 PAID，说明系统的异步回调链路断了！
    assert final_status == "PAID", "严重故障：等待 2 秒后订单状态依然未流转！"