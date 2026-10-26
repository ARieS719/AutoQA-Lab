# tests/test_fuzz.py — Hypothesis 属性驱动模糊测试 (Fuzzing)
#
# 设计要点：
#   1. BASE_URL 从全局配置 config.py 读取，杜绝硬编码；
#   2. HEADERS 改为 module 级 fixture（原先在模块导入期即发起登录请求，靶场未启动时
#      拿到 fake_token，导致 200 个用例集体误报失败）；
#   3. 策略边界收窄（文本长度/整数范围可控），200 发极端 Payload 更快更稳；
#   4. deadline 关闭：CI 慢机器上避免 Hypothesis 时间预算引发的偶发失败。
import pytest
import requests

from hypothesis import given, settings
from hypothesis import strategies as st

from config import settings as app_config

BASE_URL = app_config.BASE_URL


@pytest.fixture(scope="module")
def auth_headers():
    """模块级登录 Token：只在测试执行期调用一次，靶场不可用时明确 skip"""
    try:
        resp = requests.post(
            f"{BASE_URL}/api/v1/login",
            params={"username": "admin", "password": "123456"},
            timeout=3,
        )
        token = resp.json().get("token")
        assert token, "登录成功但未返回 Token"
        return {"Authorization": f"Bearer {token}"}
    except Exception as e:
        pytest.skip(f"靶场服务不可用，跳过模糊测试（{e}）")


class TestOrderFuzzing:
    # 用 @given 让引擎自动造数据
    @given(
        # 疯狂生成商品名：包含各种 Unicode、表情包、超长文本、空字符
        item_name=st.text(max_size=500),
        # 疯狂生成订单数：极大数、极小数、0、负数
        qty=st.integers(min_value=-10**6, max_value=10**6),
    )
    # 设定一次性射出 200 发随机请求，模拟极端场景
    @settings(max_examples=200, deadline=None)
    def test_fuzz_create_order(self, auth_headers, item_name, qty):
        payload = {
            "item_name": item_name,
            "qty": qty,
        }

        # 向服务器发送 (这里已经自动使用了配置里的域名)
        resp = requests.post(f"{BASE_URL}/api/v1/orders", json=payload, headers=auth_headers, timeout=5)

        # 测开的高级断言逻辑：
        # 不关心这单到底成没成（因为数据是乱造的）
        # 只关心一条底线：服务器绝对不能死机（HTTP 500 Internal Server Error）！
        assert resp.status_code != 500, f"发现严重 Bug！服务器崩溃！致死 Payload: {payload}"

        # 正常的业务状态应该是：
        # 要么数据碰巧合法，订单创建成功 (200)
        # 要么数据不合法，被 Pydantic 校验拦截，返回参数错误 (422) 或业务拒绝 (400)
        assert resp.status_code in [200, 400, 422], f"未预期的状态码: {resp.status_code}"
