import time
import pytest
import requests

BASE_URL = "http://127.0.0.1:8000/api/v1"

def test_mq_eventual_consistency():
    """
    深度灰盒测试：验证基于 MQ 的异步积分发放是否能达到最终一致性
    """
    user_id = "user_888"
    test_order_id = f"ORDER_MQ_{int(time.time())}"
    
    # 1. 记录发起支付前的初始积分
    init_res = requests.get(f"{BASE_URL}/user/points/{user_id}")
    init_points = init_res.json()["points"]
    
    # 2. 发起支付（触发 MQ 生产者）
    pay_res = requests.post(f"{BASE_URL}/order/{test_order_id}/pay_and_notify")
    assert pay_res.status_code == 200
    
    # 3. 极速断言：证明接口是异步的！如果瞬间去查，积分一定还没到账
    fast_check_res = requests.get(f"{BASE_URL}/user/points/{user_id}")
    assert fast_check_res.json()["points"] == init_points, "系统变成了同步处理，没有走 MQ 异步脱壳！"
    
    # 4. 核心逻辑：轮询等待“最终一致性”达成
    print("\n[开始轮询] 等待后台 MQ 消费者处理积分...")
    max_retries = 15  # 最多等 3 秒 (15 * 0.2s)
    poll_interval = 0.2
    
    is_consistent = False
    for i in range(max_retries):
        current_res = requests.get(f"{BASE_URL}/user/points/{user_id}")
        current_points = current_res.json()["points"]
        
        # 验证积分是否增加了 100
        if current_points == init_points + 100:
            is_consistent = True
            print(f"最终一致性达成！在第 {i+1} 次查询时积分成功入账。")
            break
            
        print(f"第 {i+1} 次查询，积分未到账，当前积分: {current_points}...")
        time.sleep(poll_interval)
        
    # 5. 终极断言：如果等了 3 秒还没变化，说明 MQ 链路断了（消息堆积或消费者宕机）
    assert is_consistent, "严重 Bug: 超过最大等待时间，MQ 最终一致性被破坏！"