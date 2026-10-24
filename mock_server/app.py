import os
import time
import random
import asyncio
import hashlib
import pymysql
import redis.asyncio as redis
from typing import Optional

from fastapi import FastAPI, HTTPException, Header, Depends, Request, Query
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field
from loguru import logger
from prometheus_fastapi_instrumentator import Instrumentator

# ========================================================
# 1. 全局环境变量与监控基建 (支持 Docker 与本地双环境)
# ========================================================
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", 3306))
DB_USER = os.getenv("DB_USER", "qa_user")
DB_PASS = os.getenv("DB_PASS", "qa_pass")
DB_NAME = os.getenv("DB_NAME", "automation_shop")

REDIS_HOST = os.getenv("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_PASS = os.getenv("REDIS_PASS", "qa_redis_pass")

# 性能调优实验开关: "slow" (优化前: 锁粒度过大+慢查询) | "fast" (优化后: 细粒度锁+索引加速)
PERF_MODE = os.getenv("PERF_MODE", "fast")

app = FastAPI(
    title="Enterprise E-Commerce & Async QA Target Server",
    description="企业级高并发交易微服务靶场：涵盖订单状态机、Redis分布式锁秒杀防超卖、Webhook签名回调及MQ异步最终一致性",
    version="3.1.0"
)

Instrumentator().instrument(app).expose(app, endpoint="/metrics")

if not os.path.exists("logs"):
    os.makedirs("logs")

logger.add("logs/server_{time:YYYY-MM-DD}.log", rotation="00:00", retention="7 days", level="INFO", encoding="utf-8")

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    client_ip = request.client.host if request.client else "Unknown"
    logger.warning(f"[安全拦截 422] | 源IP: {client_ip} | 路径: {request.url.path} | 非法Payload: {exc.body}")
    return JSONResponse(
        status_code=422,
        content={"detail": "参数校验失败，非法请求已拦截并记录", "errors": exc.errors()}
    )

redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, password=REDIS_PASS, decode_responses=True)

# ========================================================
# 2. 数据库连接与表结构/索引自愈初始化
# ========================================================
def get_db_connection():
    return pymysql.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASS,
        database=DB_NAME, cursorclass=pymysql.cursors.DictCursor
    )

@app.on_event("startup")
async def ensure_database_schema():
    logger.info(f"[启动自检] 正在初始化交易数据库表结构 (当前性能模式 PERF_MODE={PERF_MODE})...")
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    item_name VARCHAR(100) NOT NULL,
                    qty INT NOT NULL,
                    status VARCHAR(20) DEFAULT 'PENDING',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            # 1. 兼容旧表结构：若旧 orders 表缺少 created_at 列，自动热更新补齐该列
            cursor.execute("SHOW COLUMNS FROM orders LIKE 'created_at'")
            if not cursor.fetchone():
                cursor.execute("ALTER TABLE orders ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
                logger.info("[数据库调优] 已自动为旧 orders 表补齐 created_at 时间戳字段")

            # 2. 检查并创建 (status, created_at) 联合索引，消除全表扫描慢查询
            cursor.execute("SHOW INDEX FROM orders WHERE Key_name = 'idx_orders_status_created'")
            if not cursor.fetchone():
                cursor.execute("CREATE INDEX idx_orders_status_created ON orders (status, created_at)")
                logger.info("[数据库调优] 已自动创建联合索引 idx_orders_status_created")
        conn.commit()
        conn.close()
        logger.info("[启动自检] 数据库表结构与索引初始化成功！")
    except Exception as e:
        logger.error(f"[启动自检] 数据库初始化失败，请检查 MySQL 连接: {e}")

# ========================================================
# 3. 核心交易链路：鉴权与订单状态机 (PENDING -> PAID / CANCELLED)
# ========================================================
class OrderRequest(BaseModel):
    item_name: str = Field(..., min_length=1, max_length=100, description="商品名称不能为空")
    qty: int = Field(..., gt=0, le=10000, description="订单数量必须在 1 到 10000 之间")

@app.post("/api/v1/login", tags=["1. 全局鉴权"])
def login(username: str = "admin", password: str = "123456"):
    if username == "admin" and password == "123456":
        return {"code": 200, "message": "success", "token": "mock_token_888"}
    raise HTTPException(status_code=401, detail="账号或密码错误")

def verify_token(authorization: str = Header(None)):
    if not authorization or authorization != "Bearer mock_token_888":
        raise HTTPException(status_code=401, detail="无效或缺失的 Token，禁止访问！")
    return authorization

@app.post("/api/v1/orders", tags=["2. 订单状态机链路"])
def create_order(order: OrderRequest, token: str = Depends(verify_token)):
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO orders (item_name, qty, status) VALUES (%s, %s, %s)",
                (order.item_name, order.qty, "PENDING")
            )
            order_id = cursor.lastrowid
        conn.commit()
    finally:
        conn.close()
    return {
        "code": 200,
        "message": "success",
        "data": {"order_id": order_id, "item_name": order.item_name, "current_status": "PENDING"}
    }

@app.post("/api/v1/orders/{order_id}/pay", tags=["2. 订单状态机链路"])
def pay_order(order_id: int):
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            # 优化前模拟慢查询开销；优化后直接走主键与行级排他锁保护
            if PERF_MODE == "slow":
                time.sleep(0.03)
            cursor.execute("SELECT status FROM orders WHERE id=%s", (order_id,))
            row = cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="订单不存在")
            if row["status"] == "PAID":
                raise HTTPException(status_code=409, detail="订单已支付，请勿重复支付")
            if row["status"] == "CANCELLED":
                raise HTTPException(status_code=409, detail="订单已取消，无法支付")
            cursor.execute("UPDATE orders SET status='PAID' WHERE id=%s", (order_id,))
        conn.commit()
    finally:
        conn.close()
    return {"code": 0, "msg": "支付成功", "data": {"order_id": order_id, "status": "PAID"}}

@app.post("/api/v1/orders/{order_id}/cancel", tags=["2. 订单状态机链路"])
def cancel_order(order_id: int):
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT status FROM orders WHERE id=%s", (order_id,))
            row = cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="订单不存在")
            if row["status"] == "PAID":
                raise HTTPException(status_code=409, detail="已支付订单无法取消")
            if row["status"] == "CANCELLED":
                raise HTTPException(status_code=409, detail="订单已处于取消状态")
            cursor.execute("UPDATE orders SET status='CANCELLED' WHERE id=%s", (order_id,))
        conn.commit()
    finally:
        conn.close()
    return {"code": 0, "msg": "取消成功", "data": {"order_id": order_id, "status": "CANCELLED"}}

# ========================================================
# 4. 高并发分布式锁与秒杀防超卖 (含锁粒度调优对比实现)
# ========================================================
db_inventory = {"sku_1001": 10}
successful_orders = 0

@app.post("/api/v1/seckill/{sku_id}", tags=["3. 高并发秒杀与分布式锁"])
async def seckill_item(sku_id: str, mode: Optional[str] = Query(None, description="可选覆盖模式: slow 或 fast")):
    """
    核心压测靶点：演示【粗粒度分布式锁 (slow)】与【细粒度锁+前置快速失败 (fast)】的吞吐量差异
    """
    global successful_orders
    active_mode = mode or PERF_MODE
    lock_key = f"lock:seckill:{sku_id}"

    if active_mode == "slow":
        # 【反面教材 / 优化前缺陷态】：
        # 未做前置库存判断，所有请求无脑排队抢锁；且将耗时的非核心 I/O (0.05s) 包裹在锁内部，导致吞吐量暴跌
        async with redis_client.lock(lock_key, timeout=5, blocking_timeout=3):
            current_stock = db_inventory.get(sku_id, 0)
            await asyncio.sleep(0.05)  # 模拟风控校验与审计日志耗时阻塞在锁内
            if current_stock > 0:
                db_inventory[sku_id] = current_stock - 1
                successful_orders += 1
                return {"code": 200, "msg": "抢购成功！", "remain": db_inventory[sku_id], "mode": "slow"}
            return {"code": 200, "msg": "库存不足，抢购失败", "remain": 0, "mode": "slow"}
    else:
        # 【正面教材 / 优化后高并发态】：
        # 1. 前置无锁快速失败 (Fast-Fail)：库存售罄后直接内存拦截，避免99%无效流量冲击 Redis 锁
        if db_inventory.get(sku_id, 0) <= 0:
            return {"code": 200, "msg": "库存不足，抢购失败(前置快速拦截)", "remain": 0, "mode": "fast"}

        # 2. 将非核心风控耗时移出分布式锁外部，仅对极速原子扣减加细粒度锁
        await asyncio.sleep(0.002)
        async with redis_client.lock(lock_key, timeout=5, blocking_timeout=3):
            current_stock = db_inventory.get(sku_id, 0)
            if current_stock > 0:
                db_inventory[sku_id] = current_stock - 1
                successful_orders += 1
                return {"code": 200, "msg": "抢购成功！", "remain": db_inventory[sku_id], "mode": "fast"}
            return {"code": 200, "msg": "库存不足，抢购失败", "remain": 0, "mode": "fast"}

@app.get("/api/v1/inventory/{sku_id}", tags=["3. 高并发秒杀与分布式锁"])
def get_inventory(sku_id: str):
    return {"remain_stock": db_inventory.get(sku_id, 0), "total_sold": successful_orders}

# ========================================================
# 5. 异步架构模拟：Webhook 签名验签与 MQ 削峰最终一致性
# ========================================================
WEBHOOK_SECRET = "super_secret_key_from_alipay"

@app.post("/api/v1/webhook/pay_callback", tags=["4. 异步架构：Webhook与MQ"])
async def pay_callback(order_id: str, amount: float, signature: str):
    expected_sign_str = f"{order_id}|{amount}|{WEBHOOK_SECRET}"
    expected_signature = hashlib.md5(expected_sign_str.encode()).hexdigest()
    if signature != expected_signature:
        return {"code": 403, "msg": "非法回调签名"}
    await asyncio.sleep(0.5)
    if not hasattr(app.state, "webhook_orders"):
        app.state.webhook_orders = {}
    app.state.webhook_orders[order_id] = "PAID"
    return {"code": 200, "msg": "回调接收成功"}

@app.get("/api/v1/order/status/{order_id}", tags=["4. 异步架构：Webhook与MQ"])
def check_order_status(order_id: str):
    webhook_orders = getattr(app.state, "webhook_orders", {})
    return {"order_id": order_id, "status": webhook_orders.get(order_id, "PENDING")}

db_user_points = {"user_888": 0}
MQ_NAME = "queue:order_paid_events"

async def mq_consumer_worker():
    while True:
        try:
            message = await redis_client.brpop(MQ_NAME, timeout=1)
            if message:
                _, order_id = message
                await asyncio.sleep(0.5)
                db_user_points["user_888"] = db_user_points.get("user_888", 0) + 100
        except Exception:
            await asyncio.sleep(1)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(mq_consumer_worker())

@app.post("/api/v1/order/{order_id}/pay_and_notify", tags=["4. 异步架构：Webhook与MQ"])
async def pay_and_notify(order_id: str):
    await redis_client.lpush(MQ_NAME, order_id)
    return {"code": 200, "msg": "支付成功，积分将在后台异步发放"}

@app.get("/api/v1/user/points/{user_id}", tags=["4. 异步架构：Webhook与MQ"])
def get_user_points(user_id: str):
    return {"user_id": user_id, "points": db_user_points.get(user_id, 0)}

# ========================================================
# 6. QA 效能工具平台 (批量造数工厂与库存重置)
# ========================================================
@app.post("/api/v1/tools/batch-orders", tags=["5. 测开效能造数工具"])
def batch_create_orders(count: int = 1000):
    if count <= 0 or count > 100000:
        return {"error": "数量必须在 1 到 100,000 之间"}
    start_time = time.time()
    orders = [
        (f"批量测试商品_SKU{random.randint(1000, 9999)}", random.randint(1, 50), "PENDING")
        for _ in range(count)
    ]
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.executemany("INSERT INTO orders (item_name, qty, status) VALUES (%s, %s, %s)", orders)
        conn.commit()
    finally:
        conn.close()
    end_time = time.time()
    logger.info(f"[造数工厂] 成功批量灌入 {count} 条数据，耗时 {end_time - start_time:.3f} 秒")
    return {
        "message": "batch generation success",
        "inserted_count": count,
        "time_cost_seconds": round(end_time - start_time, 3)
    }

@app.post("/api/v1/tools/init-test-stock/{sku_id}/{qty}", tags=["5. 测开效能造数工具"])
def init_test_stock(sku_id: str, qty: int):
    global successful_orders
    db_inventory[sku_id] = qty
    successful_orders = 0
    return {"msg": f"{sku_id} 库存已重置为 {qty}，累计售出计数器已归零"}