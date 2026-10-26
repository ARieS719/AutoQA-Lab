# tests/test_mq_consistency.py — MQ 削峰与积分发放最终一致性测试
#
# 本版修复：
#   1. BASE_URL 从统一配置中心读取（原硬编码 127.0.0.1:8000）；
#   2. 用例开头调用 reset-test-state 清空队列与积分账本，消除历史残留消息
#      导致的「积分多加了 100」型偶发误报（测试间耦合）；
#   3. 复用 conftest.poll_until 轮询断言基座。
import time

import pytest
import requests

from config import settings
from tests.utils import poll_until

BASE_URL = f"{settings.BASE_URL}/api/v1"


@pytest.fixture(scope="module", autouse=True)
def reset_async_state():
    """用例前归零 MQ 队列与积分账本，保证断言确定性"""
    requests.post(f"{BASE_URL}/tools/reset-test-state", timeout=3)
    yield


def test_mq_eventual_consistency():
    """
    深度灰盒测试：验证基于 MQ 的异步积分发放是否能达到最终一致性
    """
    user_id = "user_888"
    test_order_id = f"ORDER_MQ_{int(time.time())}"

    # 1. 记录发起支付前的初始积分
    init_res = requests.get(f"{BASE_URL}/user/points/{user_id}", timeout=3)
    init_points = init_res.json()["points"]

    # 2. 发起支付（触发 MQ 生产者）
    pay_res = requests.post(f"{BASE_URL}/order/{test_order_id}/pay_and_notify", timeout=3)
    assert pay_res.status_code == 200

    # 3. 极速断言：证明接口是异步的！如果瞬间去查，积分一定还没到账
    fast_check_res = requests.get(f"{BASE_URL}/user/points/{user_id}", timeout=3)
    assert fast_check_res.json()["points"] == init_points, "系统变成了同步处理，没有走 MQ 异步脱壳！"

    # 4. 核心逻辑：轮询等待「最终一致性」达成（消费者有 0.5s 处理耗时）
    print("\n[开始轮询] 等待后台 MQ 消费者处理积分...")

    def points_arrived():
        current = requests.get(f"{BASE_URL}/user/points/{user_id}", timeout=3).json()["points"]
        print(f"当前积分: {current} (目标: {init_points + 100})...")
        return current == init_points + 100

    poll_until(points_arrived, timeout=4.0, interval=0.2, description="MQ 积分 +100 最终一致性达成")

    # 5. 终极断言（poll_until 已保证，此处再次显式验证账本最终值）
    final_points = requests.get(f"{BASE_URL}/user/points/{user_id}", timeout=3).json()["points"]
    assert final_points == init_points + 100, "严重 Bug: MQ 最终一致性被破坏！"
