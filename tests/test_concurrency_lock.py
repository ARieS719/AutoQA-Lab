# tests/test_concurrency_lock.py
import pytest
import requests
import concurrent.futures
from config import settings as app_config

BASE_URL = f"{app_config.BASE_URL}/api/v1"

@pytest.fixture
def setup_test_inventory():
    """测试前置准备：将测试商品 sku_test_lock 的初始库存重置为 1 件"""
    requests.post(f"{BASE_URL}/tools/init-test-stock/sku_test_lock/1", timeout=3)
    yield

def test_redis_lock_prevents_overselling(setup_test_inventory):
    """
    高并发防超卖核心用例：验证 Redis 分布式锁在瞬时并发冲击下确保 0 超卖
    """
    target_sku = "sku_test_lock"
    concurrent_users = 10

    def send_seckill_request():
        return requests.post(f"{BASE_URL}/seckill/{target_sku}", timeout=5)

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrent_users) as executor:
        futures = [executor.submit(send_seckill_request) for _ in range(concurrent_users)]
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result().json())

    success_count = sum(1 for res in results if "抢购成功" in res.get("msg", ""))
    fail_count = sum(1 for res in results if "抢购失败" in res.get("msg", ""))

    # 1. 验证：10 人并发疯抢 1 件库存，绝对只能有 1 人成功！
    assert success_count == 1, f"严重超卖漏洞！预期 1 人成功，实际成功了 {success_count} 人"
    
    # 2. 验证：其余 9 人必须全部被锁或前置拦截
    assert fail_count == concurrent_users - 1, f"并发拦截异常！预期 {concurrent_users - 1} 人失败，实际 {fail_count} 人"
    
    # 3. 验证：最终库存必须精准归零，且累计售出数为 1
    final_stock_res = requests.get(f"{BASE_URL}/inventory/{target_sku}", timeout=3).json()
    assert final_stock_res["remain_stock"] == 0, "库存扣减异常，未归零"
    assert final_stock_res["total_sold"] == 1, "累计售出计数与库存扣减不一致"