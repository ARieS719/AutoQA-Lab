import pytest
import requests
from hypothesis import given, settings, strategies as st

# 核心修改 1：引入在 config.py 中写的配置对象，并起个别名防止和 hypothesis 打架
from config import settings as app_config

# 核心修改 2：彻底告别硬编码，从全局配置中读取 BASE_URL
BASE_URL = app_config.BASE_URL

# 先拿一个全局 Token，供轰炸机使用
def get_auth_token():
    try:
        # 核心修改 3：使用动态的 BASE_URL，并且必须加上 timeout=3 防止网络死锁卡住！
        resp = requests.post(f"{BASE_URL}/api/v1/login", params={"username": "admin", "password": "123456"}, timeout=3)
        return resp.json().get("token")
    except Exception as e:
        print(f"\n[警告] 获取Token失败，正在使用假Token进行测试。原因: {e}")
        return "fake_token"

HEADERS = {"Authorization": f"Bearer {get_auth_token()}"}

class TestOrderFuzzing:
    
    # 用 @given 让引擎自动造数据
    @given(
        # 疯狂生成商品名：包含各种 Unicode、表情包、超长文本、空字符
        item_name=st.text(), 
        # 疯狂生成订单数：极大数、极小数、0、负数
        qty=st.integers() 
    )
    # 设定一次性射出 200 发随机请求，模拟极端场景
    @settings(max_examples=200)
    def test_fuzz_create_order(self, item_name, qty):
        payload = {
            "item_name": item_name,
            "qty": qty
        }
        
        # 向的服务器发送 (这里已经自动使用了配置里的域名)
        resp = requests.post(f"{BASE_URL}/api/v1/orders", json=payload, headers=HEADERS)
        
        # 测开的高级断言逻辑：
        # 不关心这单到底成没成（因为数据是乱造的）
        # 只关心一条底线：服务器绝对不能死机（HTTP 500 Internal Server Error）！
        assert resp.status_code != 500, f"发现严重 Bug！服务器崩溃！致死 Payload: {payload}"
        
        # 正常的业务状态应该是：
        # 要么数据碰巧合法，订单创建成功 (200)
        # 要么数据不合法，被 Pydantic 校验拦截，返回参数错误 (422) 或 业务拒绝 (400)
        assert resp.status_code in [200, 400, 422], f"未预期的状态码: {resp.status_code}"