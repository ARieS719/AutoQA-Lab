import requests

def test_broken_order():
    """
    测试订单创建参数校验失败场景
    """
    url = "http://127.0.0.1:8000/api/v1/orders"
    headers = {"Authorization": "Bearer mock_token_888"}
    
    # 错误的 Payload
    bad_payload = {
        "quantity": -5  # 字段名错了，值也错了
    }
    
    resp = requests.post(url, json=bad_payload, headers=headers)
    
    # 修正断言为期望的422状态码
    assert resp.status_code == 422
    assert "detail" in resp.json()