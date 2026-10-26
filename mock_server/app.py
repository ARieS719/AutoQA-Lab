# ============================================================================
# mock_server/app.py — 企业级高并发交易与异步通知微服务被测靶场
#
# 靶场设计目标（面试叙事主线）：
#   1. 订单状态机 (PENDING -> PAID / CANCELLED)：支付/取消采用 CAS 条件原子更新，
#      并发重复支付仅一人成功，演示幂等防重的正确姿势；
#   2. 秒杀防超卖：库存从「进程内存字典」升级为「Redis 原子库存」，配合分布式锁，
#      即使多 Worker 部署也不会超卖；PERF_MODE=slow|fast 保留锁粒度调优对比实验；
#   3. Webhook 签名回调：MD5 签名升级为 HMAC-SHA256 + 时间戳防重放 + 幂等去重，
#      且改为「先 ACK 回执、后台队列异步流转状态」的真实异步链路；
#   4. MQ 积分发放：Redis List 模拟消息队列，最终一致性可被轮询断言验证；
#   5. 车联网第二业务域 (vehicle.py)：车辆事件/远程指令/BMS 告警/座舱偏好全链路。
# ============================================================================
import asyncio
import glob
import hashlib
import hmac
import json
import os
import random
import time

from typing import Optional

import redis.asyncio as redis

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from loguru import logger
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel, Field

from config import settings
from mock_server.deps import get_db_connection, verify_token
from mock_server.vehicle import vehicle_router

# ============================================================================
# 1. 全局配置与监控基建（全部来自统一配置中心 config.py，支持环境变量覆盖）
# ============================================================================
PERF_MODE = settings.PERF_MODE

WEBHOOK_SECRET = settings.WEBHOOK_SECRET
# 签名时间戳允许的最大时钟偏移（秒），超出即判定为重放/伪造回调
WEBHOOK_TIMESTAMP_TOLERANCE = 300

MQ_NAME = "queue:order_paid_events"          # 订单支付事件队列（模拟 MQ）
WEBHOOK_QUEUE = "queue:webhook_callbacks"    # 支付回调异步处理队列
POINTS_HASH = "user_points"                  # Redis Hash: 用户积分账本
STOCK_KEY_PREFIX = "inventory:stock:"        # Redis String: 秒杀库存
SOLD_KEY_PREFIX = "inventory:sold:"          # Redis String: 累计售出
WEBHOOK_STATE_PREFIX = "webhook:order:"      # Redis String: 回调流转后的订单状态
REPLAY_KEY_PREFIX = "webhook:replay:"        # Redis String(NX): 回调幂等去重

app = FastAPI(
    title="Enterprise E-Commerce & Vehicle-IoT QA Target Server",
    description=(
        "企业级高并发交易 + 车联网微服务靶场：涵盖订单状态机、Redis 分布式锁秒杀防超卖、"
        "Webhook 签名回调异步链路、MQ 积分最终一致性及车辆远程指令状态机"
    ),
    version="4.0.0",
)

Instrumentator().instrument(app).expose(app, endpoint="/metrics")

if not os.path.exists("logs"):
    os.makedirs("logs")

logger.add(
    "logs/server_{time:YYYY-MM-DD}.log",
    rotation="00:00",
    retention="7 days",
    level="INFO",
    encoding="utf-8",
)

redis_client = redis.Redis(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    password=settings.REDIS_PASS,
    decode_responses=True,
    # redis-py 5.x 异步连接池默认上限仅 100：500 并发压测时池耗尽会抛
    # MaxConnectionsError -> 500。这里按压测上限放大并加超时保护，
    # 单次 Redis 操作超过 5s 视为异常快速失败，避免请求无限挂起。
    max_connections=1000,
    socket_timeout=5,
    socket_connect_timeout=5,
)


# ============================================================================
# 2. 统一异常拦截与全链路请求日志（服务健壮性基建）
# ============================================================================
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """参数校验失败统一拦截：记录非法 Payload 并返回结构化 422，防止畸形数据击穿业务层"""
    client_ip = request.client.host if request.client else "Unknown"
    logger.warning(f"[安全拦截 422] | 源IP: {client_ip} | 路径: {request.url.path} | 非法Payload: {exc.body}")
    return JSONResponse(
        status_code=422,
        content={"detail": "参数校验失败，非法请求已拦截并记录", "errors": exc.errors()},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """兜底异常处理器：任何未捕获异常都返回结构化 500，保证模糊测试『零 500 裸奔』语义"""
    logger.error(f"[未捕获异常 500] 路径: {request.url.path} | 异常类型: {type(exc).__name__} | 详情: {exc}")
    return JSONResponse(
        status_code=500,
        content={"code": 500, "detail": "服务器内部错误，异常已记录日志"},
    )


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    """统一访问日志：记录方法/路径/状态码/耗时，压测时配合 Prometheus 交叉定位瓶颈"""
    start = time.perf_counter()
    response = await call_next(request)
    latency_ms = (time.perf_counter() - start) * 1000
    if request.url.path != "/metrics":  # 排除监控抓取噪音
        logger.info(f"[访问日志] {request.method} {request.url.path} -> {response.status_code} | {latency_ms:.1f}ms")
    return response


@app.get("/health", tags=["0. 服务健康"])
async def health_check():
    """健康探针：供 CI 启动自检 / Docker healthcheck / 压测前探活使用"""
    return {"status": "ok", "service": "qa-mock-server", "version": app.version}


# ============================================================================
# 3. 数据库连接与表结构/索引自愈初始化（db_init/*.sql 的双保险）
# ============================================================================
# 注：get_db_connection / verify_token 统一定义于 mock_server/deps.py，供电商+车联网全域复用

# 运行时自愈用的幂等建表语句（与 db_init/*.sql 保持同构；容器首次启动由 MySQL
# entrypoint 自动执行 SQL，此处兜底保证任何环境下服务启动即表结构就绪）
SELF_HEAL_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS orders (
    id INT AUTO_INCREMENT PRIMARY KEY,
    item_name VARCHAR(100) NOT NULL,
    qty INT NOT NULL,
    status VARCHAR(20) DEFAULT 'PENDING',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

SCHEMA_DIR = "db_init"


def load_schema_statements():
    """从 db_init/*.sql 读取全部初始化语句（单一事实来源），跳过建库语句供运行时兜底执行。

    这样设计的目的：容器首启走 MySQL entrypoint、本地/CI 服务启动走自愈，
    两套路径执行的是同一份 SQL，不会出现「测试环境与生产环境建表漂移」。
    """
    statements = []
    for path in sorted(glob.glob(os.path.join(SCHEMA_DIR, "*.sql"))):
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
        for stmt in raw.split(";"):
            stmt = stmt.strip()
            if not stmt:
                continue
            upper = stmt.upper()
            if upper.startswith(("CREATE DATABASE", "USE ")):
                continue
            statements.append(stmt)
    return statements


@app.on_event("startup")
async def ensure_database_schema():
    logger.info(f"[启动自检] 正在初始化交易数据库表结构 (当前性能模式 PERF_MODE={PERF_MODE})...")
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            schema_statements = load_schema_statements()
            if schema_statements:
                for stmt in schema_statements:
                    cursor.execute(stmt)
            else:
                # 极端兜底：db_init 目录缺失时仍保证核心订单表可用
                cursor.execute(SELF_HEAL_SCHEMA_SQL)

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
        # 靶场允许降级启动（便于演示故障注入），但必须留下清晰告警
        logger.error(f"[启动自检] 数据库初始化失败，请检查 MySQL 连接: {e}")


# ============================================================================
# 4. 核心交易链路：鉴权与订单状态机 (PENDING -> PAID / CANCELLED)
# ============================================================================
class OrderRequest(BaseModel):
    item_name: str = Field(..., min_length=1, max_length=100, description="商品名称不能为空")
    qty: int = Field(..., gt=0, le=10000, description="订单数量必须在 1 到 10000 之间")


@app.post("/api/v1/login", tags=["1. 全局鉴权"])
def login(username: str = "admin", password: str = "123456"):
    # 注意：硬编码账号密码仅用于测试靶场，生产环境必须接入真实 IAM/Token 体系
    if username == "admin" and password == "123456":
        return {"code": 200, "message": "success", "token": "mock_token_888"}
    raise HTTPException(status_code=401, detail="账号或密码错误")


@app.post("/api/v1/orders", tags=["2. 订单状态机链路"])
def create_order(order: OrderRequest, token: str = Depends(verify_token)):
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO orders (item_name, qty, status) VALUES (%s, %s, %s)",
                (order.item_name, order.qty, "PENDING"),
            )
            order_id = cursor.lastrowid
        conn.commit()
    finally:
        conn.close()
    return {
        "code": 200,
        "message": "success",
        "data": {"order_id": order_id, "item_name": order.item_name, "current_status": "PENDING"},
    }


@app.post("/api/v1/orders/{order_id}/pay", tags=["2. 订单状态机链路"])
def pay_order(order_id: int):
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            # PERF_MODE=slow 模拟无索引全表扫描慢查询开销；fast 模式直接走主键索引
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

            # 【CAS 原子条件更新】仅当仍为 PENDING 时才流转。
            # 并发场景：两个请求同时读到 PENDING，先提交者 rowcount=1 成功，
            # 后者被 InnoDB 行锁阻塞至前者提交后 rowcount=0 -> 返回 409，杜绝重复扣款。
            cursor.execute("UPDATE orders SET status='PAID' WHERE id=%s AND status='PENDING'", (order_id,))
            if cursor.rowcount == 0:
                raise HTTPException(status_code=409, detail="订单状态已被并发修改，请勿重复支付")
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

            # 与支付同款 CAS 防并发篡改
            cursor.execute("UPDATE orders SET status='CANCELLED' WHERE id=%s AND status='PENDING'", (order_id,))
            if cursor.rowcount == 0:
                raise HTTPException(status_code=409, detail="订单状态已被并发修改")
        conn.commit()
    finally:
        conn.close()
    return {"code": 0, "msg": "取消成功", "data": {"order_id": order_id, "status": "CANCELLED"}}


# ============================================================================
# 5. 高并发分布式锁与秒杀防超卖 (Redis 原子库存 + 锁粒度调优对比)
# ============================================================================
def _stock_key(sku_id: str) -> str:
    return f"{STOCK_KEY_PREFIX}{sku_id}"


def _sold_key(sku_id: str) -> str:
    return f"{SOLD_KEY_PREFIX}{sku_id}"


# Lua 原子扣减脚本：在 Redis 服务端单次执行「检查库存 -> 扣减 -> 累计售出」，
# Redis 单线程执行脚本保证原子性，替代应用层分布式锁（fast 模式专用）。
# 返回剩余库存；返回 -1 表示库存不足（不做任何写入）。
LUA_DEDUCT_STOCK = """
local stock = tonumber(redis.call('GET', KEYS[1]) or '0')
if stock > 0 then
    redis.call('DECR', KEYS[1])
    redis.call('INCR', KEYS[2])
    return stock - 1
end
return -1
"""


@app.post("/api/v1/seckill/{sku_id}", tags=["3. 高并发秒杀与分布式锁"])
async def seckill_item(sku_id: str, mode: Optional[str] = Query(None, description="可选覆盖模式: slow 或 fast")):
    """
    核心压测靶点：对比【粗粒度分布式锁 (slow)】与【Redis Lua 原子扣减 (fast)】的吞吐量差异。
    库存已 Redis 化，即使 uvicorn 多 Worker 部署也不会超卖。
    """
    active_mode = mode or PERF_MODE
    lock_key = f"lock:seckill:{sku_id}"

    if active_mode == "slow":
        # 【反面教材 / 优化前缺陷态】：
        # 未做前置库存判断，所有请求无脑排队抢锁；且将耗时的非核心 I/O (0.05s) 包裹在锁内部，
        # 锁吞吐（串行 ~20 请求/秒）远低于流量到达速率时，排队请求等锁超时 -> LockError 500。
        async with redis_client.lock(lock_key, timeout=5, blocking_timeout=3):
            current_stock = int(await redis_client.get(_stock_key(sku_id)) or 0)
            await asyncio.sleep(0.05)  # 模拟风控校验与审计日志耗时阻塞在锁内
            if current_stock > 0:
                await redis_client.decr(_stock_key(sku_id))
                await redis_client.incr(_sold_key(sku_id))
                return {"code": 200, "msg": "抢购成功！", "remain": current_stock - 1, "mode": "slow"}
            return {"code": 200, "msg": "库存不足，抢购失败", "remain": 0, "mode": "slow"}

    # 【正面教材 / 优化后高并发态】
    # 1. 前置无锁快速失败 (Fast-Fail)：库存售罄后直接拦截，避免 99% 无效流量冲击 Redis
    if int(await redis_client.get(_stock_key(sku_id)) or 0) <= 0:
        return {"code": 200, "msg": "库存不足，抢购失败(前置快速拦截)", "remain": 0, "mode": "fast"}

    # 2. 非核心风控耗时照常模拟（保持在关键路径之外）
    await asyncio.sleep(0.002)

    # 3. 【核心优化】单条 Lua 脚本在 Redis 端原子完成「检查 + 扣减 + 计数」，
    #    一次网络往返即完成整个抢购，无锁、无排队、无超时 —— 吞吐量上限即 Redis 本身。
    remain = await redis_client.eval(LUA_DEDUCT_STOCK, 2, _stock_key(sku_id), _sold_key(sku_id))
    if remain >= 0:
        return {"code": 200, "msg": "抢购成功！", "remain": remain, "mode": "fast"}
    return {"code": 200, "msg": "库存不足，抢购失败", "remain": 0, "mode": "fast"}


@app.get("/api/v1/inventory/{sku_id}", tags=["3. 高并发秒杀与分布式锁"])
async def get_inventory(sku_id: str):
    remain = int(await redis_client.get(_stock_key(sku_id)) or 0)
    sold = int(await redis_client.get(_sold_key(sku_id)) or 0)
    return {"remain_stock": remain, "total_sold": sold}


# ============================================================================
# 6. 异步架构：Webhook 签名回调（HMAC-SHA256 + 防重放 + 队列异步流转）
# ============================================================================
def build_webhook_signature(order_id: str, amount: str, timestamp: str) -> str:
    """HMAC-SHA256 签名：将订单号、金额、时间戳与密钥共同摘要，替代不安全的 MD5"""
    message = f"{order_id}|{amount}|{timestamp}".encode("utf-8")
    return hmac.new(WEBHOOK_SECRET.encode("utf-8"), message, hashlib.sha256).hexdigest()


@app.post("/api/v1/webhook/pay_callback", tags=["4. 异步架构：Webhook与MQ"])
async def pay_callback(order_id: str, amount: float, signature: str, timestamp: str = ""):
    """第三方支付回调入口：验签通过后『先 ACK 回执、后台异步流转』，模拟真实支付链路"""
    # 1. 时间戳防重放：缺失或时钟偏移超限一律拒绝
    try:
        ts = float(timestamp)
    except (TypeError, ValueError):
        return {"code": 403, "msg": "非法回调：缺少或非法时间戳"}
    if abs(time.time() - ts) > WEBHOOK_TIMESTAMP_TOLERANCE:
        return {"code": 403, "msg": "非法回调：时间戳过期，疑似重放攻击"}

    # 2. HMAC-SHA256 验签
    expected_signature = build_webhook_signature(order_id, str(amount), timestamp)
    if not hmac.compare_digest(signature, expected_signature):
        logger.warning(f"[Webhook拦截] order_id={order_id} 签名校验失败")
        return {"code": 403, "msg": "非法回调签名"}

    # 3. 幂等去重：同订单+同时间戳的重复回调直接拒绝（SET NX 原子去重，10 分钟过期）
    replay_key = f"{REPLAY_KEY_PREFIX}{order_id}:{timestamp}"
    if not await redis_client.set(replay_key, "1", nx=True, ex=600):
        return {"code": 403, "msg": "重复回调已拦截（幂等去重）"}

    # 4. 先回执后处理：入队即返回 200，状态由后台消费者异步流转，避免阻塞支付平台
    await redis_client.lpush(WEBHOOK_QUEUE, json.dumps({"order_id": order_id, "amount": amount}))
    return {"code": 200, "msg": "回调接收成功，正在异步处理"}


async def webhook_consumer_worker():
    """Webhook 后台消费者：模拟支付平台对账后把订单状态流转为 PAID"""
    while True:
        try:
            message = await redis_client.brpop(WEBHOOK_QUEUE, timeout=1)
            if message:
                _, raw = message
                data = json.loads(raw)
                await asyncio.sleep(0.5)  # 模拟对账处理耗时：让『轮询断言』真正派上用场
                await redis_client.set(f"{WEBHOOK_STATE_PREFIX}{data['order_id']}", "PAID")
        except Exception:
            await asyncio.sleep(1)


@app.get("/api/v1/order/status/{order_id}", tags=["4. 异步架构：Webhook与MQ"])
async def check_order_status(order_id: str):
    status = await redis_client.get(f"{WEBHOOK_STATE_PREFIX}{order_id}") or "PENDING"
    return {"order_id": order_id, "status": status}


# ============================================================================
# 7. MQ 削峰与积分发放最终一致性（Redis Hash 账本，多进程共享）
# ============================================================================
async def mq_consumer_worker():
    """MQ 消费者：消费订单支付事件，为 user_888 异步发放 100 积分"""
    while True:
        try:
            message = await redis_client.brpop(MQ_NAME, timeout=1)
            if message:
                _, order_id = message
                await asyncio.sleep(0.5)  # 模拟积分系统处理耗时
                await redis_client.hincrby(POINTS_HASH, "user_888", 100)
                logger.info(f"[MQ消费] 订单 {order_id} 积分已发放 +100")
        except Exception:
            await asyncio.sleep(1)


@app.post("/api/v1/order/{order_id}/pay_and_notify", tags=["4. 异步架构：Webhook与MQ"])
async def pay_and_notify(order_id: str):
    await redis_client.lpush(MQ_NAME, order_id)
    return {"code": 200, "msg": "支付成功，积分将在后台异步发放"}


@app.get("/api/v1/user/points/{user_id}", tags=["4. 异步架构：Webhook与MQ"])
async def get_user_points(user_id: str):
    points = int(await redis_client.hget(POINTS_HASH, user_id) or 0)
    return {"user_id": user_id, "points": points}


# ============================================================================
# 8. QA 效能工具平台（批量造数工厂 / 库存重置 / 测试状态归零）
# ============================================================================
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
        "time_cost_seconds": round(end_time - start_time, 3),
    }


@app.post("/api/v1/tools/init-test-stock/{sku_id}/{qty}", tags=["5. 测开效能造数工具"])
async def init_test_stock(sku_id: str, qty: int):
    await redis_client.set(_stock_key(sku_id), qty)
    await redis_client.set(_sold_key(sku_id), 0)
    return {"msg": f"{sku_id} 库存已重置为 {qty}，累计售出计数器已归零"}


@app.post("/api/v1/tools/reset-test-state", tags=["5. 测开效能造数工具"])
async def reset_test_state():
    """测试状态归零：清空 MQ 队列 / Webhook 队列 / 积分账本，保证用例间的确定性隔离"""
    await redis_client.delete(MQ_NAME, WEBHOOK_QUEUE, POINTS_HASH)
    return {"msg": "MQ 队列与积分账本已重置，测试状态归零"}


# ============================================================================
# 9. 车联网第二业务域挂载（车辆事件/远程指令/BMS告警/座舱偏好）
# ============================================================================
app.include_router(vehicle_router)

# ============================================================================
# 10. 后台消费者任务生命周期管理
# ============================================================================
_consumer_tasks = []


@app.on_event("startup")
async def startup_event():
    _consumer_tasks.append(asyncio.create_task(mq_consumer_worker()))
    _consumer_tasks.append(asyncio.create_task(webhook_consumer_worker()))
    logger.info("[后台任务] MQ 积分消费者与 Webhook 异步处理器已启动")


@app.on_event("shutdown")
async def shutdown_event():
    for task in _consumer_tasks:
        task.cancel()
    # 等待取消真正完成，避免事件循环关闭时出现 "Task was destroyed but it is pending" 告警
    for task in _consumer_tasks:
        try:
            await task
        except asyncio.CancelledError:
            pass
    # 关闭连接池：TestClient 多次实例化（新事件循环）时避免旧连接跨 loop 复用
    await redis_client.aclose()
    logger.info("[后台任务] 消费者协程已优雅退出")
