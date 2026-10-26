# tests/locustfile.py — 高并发压测脚本（Locust，支持阶梯压测与分链路压测）
#
# 压测方法论（面向专业压测报告）：
#   1. 分链路压测：PERF_PROFILE=mixed（混合）| seckill（纯秒杀）| trade（纯交易），
#      分别度量「扣减链路」「拦截链路」「DB 写链路」的真实能力，互不干扰；
#   2. 阶梯加压：固定时长，并发从 50 起步逐档抬升，绘制 并发-吞吐-时延 曲线；
#   3. 库存预热：默认 50000 库存，保证整个压测窗口内 Lua 扣减链路持续受压，
#      而非几秒耗尽后全走 Fast-Fail 拦截分支（那是另一条链路的度量场景）。
import os
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

# 压测模式：mixed（默认混合链路）| seckill（纯秒杀）| trade（纯下单+支付）
# 用法：$env:PERF_PROFILE="seckill" 后再启动 locust
PERF_PROFILE = os.getenv("PERF_PROFILE", "mixed")

# 秒杀压测预热参数：目标 SKU 与初始库存（可用 $env:SECKILL_STOCK 覆盖）
SECKILL_SKU = "sku_1001"
SECKILL_INIT_STOCK = int(os.getenv("SECKILL_STOCK", 50000))

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
# 并发模型 1：核心订单交易链路（下单 30% + 支付 70%）
# ========================================================
class OrderTestUser(HttpUser):
    # PERF_PROFILE=seckill 时关闭交易用户，专注度量秒杀链路
    weight = 0 if PERF_PROFILE == "seckill" else 3
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
# 并发模型 2：高并发秒杀链路（Lua 原子扣减 / 前置快速拦截）
# ========================================================
class SecKillUser(HttpUser):
    # PERF_PROFILE=trade 时关闭秒杀用户，专注度量交易链路
    weight = 0 if PERF_PROFILE == "trade" else 2
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
