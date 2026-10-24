import os
import queue
import random
import pymysql
import requests
from locust import HttpUser, task, between, events
from locust.exception import StopUser

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", 3306))
DB_USER = os.getenv("DB_USER", "qa_user")
DB_PASS = os.getenv("DB_PASS", "qa_pass")
DB_NAME = os.getenv("DB_NAME", "automation_shop")

# ========================================================
# 压测全局基建：线程安全内存数据队列
# ========================================================
pending_orders = queue.Queue()

@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """
    压测前置钩子：直连数据库预加载待支付订单 ID 进入内存队列，
    若存量订单不足 5000 条，自动触发批量造数，规避压测过程中的读库瓶颈与无数据空跑。
    """
    print("\n[压测引擎初始化] 正在连接底层数据库，检查压测弹药储备...")
    try:
        conn = pymysql.connect(
            host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASS,
            database=DB_NAME, cursorclass=pymysql.cursors.DictCursor
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

# ========================================================
# 并发模型 1：核心订单交易链路混合压测 (下单 30% + 支付 70%)
# ========================================================
class OrderTestUser(HttpUser):
    weight = 3
    wait_time = between(0.2, 0.8)

    def on_start(self):
        """虚拟用户初始化：获取鉴权 Token"""
        try:
            resp = self.client.post("/api/v1/login", params={"username": "admin", "password": "123456"})
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
            "qty": random.randint(1, 100)
        }
        with self.client.post("/api/v1/orders", json=payload, name="[写请求] 创建订单", catch_response=True) as response:
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

        with self.client.post(f"/api/v1/orders/{order_id}/pay", name="[写请求] 订单状态流转 (支付)", catch_response=True) as response:
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
        with self.client.post("/api/v1/seckill/sku_1001", name="[秒杀洪峰] 分布式锁抢购", catch_response=True) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"秒杀接口异常: {response.status_code}")