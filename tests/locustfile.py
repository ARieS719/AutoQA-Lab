# tests/locustfile.py — 高并发压测脚本（Locust）
#
# 本版增强：
#   1. 数据库连接配置统一从 config.py 读取；
#   2. test_start 钩子新增「秒杀库存预热」：通过靶场工具接口重置秒杀库存，
#      避免压测一开始库存就耗尽、秒杀链路全部走 Fast-Fail 分支失去锁竞争意义；
#   3. 重复压测时先清空内存队列，避免旧订单残留导致支付任务命中已支付订单；
#   4. test_stop 钩子输出压测结论指引。
import queue
import random

import pymysql
import requests

from locust import HttpUser, between, events, task
from locust.exception import StopUser

from config import settings

DB_HOST = settings.DB_HOST
DB_PORT = settings.DB_PORT
DB_USER = settings.DB_USER
DB_PASS = settings.DB_PASS
DB_NAME = settings.DB_NAME
BASE_URL = settings.BASE_URL

# 秒杀压测预热参数：目标 SKU 与初始库存（保证锁竞争场景有货可抢）
SECKILL_SKU = "sku_1001"
SECKILL_INIT_STOCK = 500

# ========================================================
# 压测全局基建：线程安全内存数据队列
# ========================================================
pending_orders = queue.Queue()


def _reset_seckill_stock():
    """压测前通过靶场工具接口重置秒杀库存（Fast-Fail 前置拦截不影响该工具接口）"""
    try:
        requests.post(
            f"{BASE_URL}/api/v1/tools/init-test-stock/{SECKILL_SKU}/{SECKILL_INIT_STOCK}",
            timeout=5,
        )
        print(f"[压测预热] 秒杀库存已重置: {SECKILL_SKU} x {SECKILL_INIT_STOCK}")
    except Exception as e:
        print(f"[压测预热] 秒杀库存重置失败（靶场未启动？）: {e}")


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """
    压测前置钩子：
    1. 重置秒杀库存，确保锁竞争链路真实压到；
    2. 直连数据库预加载待支付订单 ID 进入内存队列，不足 5000 条自动批量造数，
       规避压测过程中的读库瓶颈与无数据空跑。
    """
    _reset_seckill_stock()

    print("\n[压测引擎初始化] 正在连接底层数据库，检查压测弹药储备...")
    try:
        # 重复压测时先排空旧队列，防止残留订单干扰本次压测
        while not pending_orders.empty():
            pending_orders.get_nowait()

        conn = pymysql.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASS,
            database=DB_NAME,
            cursorclass=pymysql.cursors.DictCursor,
        )
        with conn.cursor() as cursor:
            cursor.execute("SELECT id FROM orders WHERE status = 'PENDING' LIMIT 50000")
            records = cursor.fetchall()

            # 若待支付订单不足 5000 条，直接在数据库预热灌入 10000 条数据
            if len(records) < 5000:
                print(f"[压测预热] 当前待支付订单仅 {len(records)} 条，正在自动批量造数 10000 条...")
                batch_data = [
                    (f"预热压测商品_SKU{random.randint(1000, 9999)}", random.randint(1, 20), "PENDING")
                    for _ in range(10000)
                ]
                cursor.executemany("INSERT INTO orders (item_name, qty, status) VALUES (%s, %s, %s)", batch_data)
                conn.commit()
                cursor.execute("SELECT id FROM orders WHERE status = 'PENDING' LIMIT 50000")
                records = cursor.fetchall()

            for row in records:
                pending_orders.put(row["id"])
        conn.close()
        print(f"✅ [压测引擎就绪] 成功装载 {pending_orders.qsize()} 条待支付订单进入内存队列！\n")
    except Exception as e:
        print(f"❌ 致命错误：压测数据装载失败: {e}")


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """压测结束钩子：输出结论性指引，方便对照 README 性能表格复现实验"""
    print("\n[压测结束] 请查看 Locust 汇总指标：")
    print("  - 吞吐量 (RPS) / TP99 时延 -> 与 README 性能对比表对照")
    print("  - 失败率 -> 慢模式 (PERF_MODE=slow) 应能观测到锁等待超时异常")


# ========================================================
# 并发模型 1：核心订单交易链路混合压测 (下单 30% + 支付 70%)
# ========================================================
class OrderTestUser(HttpUser):
    weight = 3
    wait_time = between(0.2, 0.8)

    def on_start(self):
        """虚拟用户初始化：获取鉴权 Token"""
        try:
            resp = self.client.post(
                "/api/v1/login",
                params={"username": "admin", "password": "123456"},
            )
            if resp.status_code == 200:
                token = resp.json().get("token")
                self.client.headers.update({"Authorization": f"Bearer {token}"})
            else:
                raise StopUser()
        except Exception:
            raise StopUser()

    @task(3)
    def create_order_task(self):
        payload = {
            "item_name": f"大促压测商品_SKU_{random.randint(100, 999)}",
            "qty": random.randint(1, 100),
        }
        with self.client.post(
            "/api/v1/orders", json=payload, name="[写请求] 创建订单", catch_response=True
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"下单异常: {response.status_code}")

    @task(7)
    def pay_order_task(self):
        try:
            order_id = pending_orders.get(block=False)
        except queue.Empty:
            return

        with self.client.post(
            f"/api/v1/orders/{order_id}/pay", name="[写请求] 订单状态流转 (支付)", catch_response=True
        ) as response:
            if response.status_code in (200, 409):
                response.success()
            else:
                response.failure(f"支付链路异常，状态码: {response.status_code}")


# ========================================================
# 并发模型 2：高并发秒杀与分布式锁争抢压测
# ========================================================
class SecKillUser(HttpUser):
    weight = 2
    wait_time = between(0.05, 0.15)

    @task
    def rush_buy(self):
        with self.client.post(
            f"/api/v1/seckill/{SECKILL_SKU}", name="[秒杀洪峰] 分布式锁抢购", catch_response=True
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"秒杀接口异常: {response.status_code}")
