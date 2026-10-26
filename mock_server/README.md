# mock_server/ — 被测微服务靶场（FastAPI 双业务域）

AutoQA-Lab 的「被测对象」：为测试与压测提供高仿真目标系统，覆盖电商高并发交易与车联网两大业务域，共 23 个业务接口。

## 模块清单

| 文件 | 职责 |
| :--- | :--- |
| `app.py` | 电商域：订单状态机、秒杀防超卖、Webhook 验签回调、MQ 积分、QA 造数工具、健康探针与统一异常/日志基建 |
| `vehicle.py` | 车联网域：车辆事件上报、远程指令状态机、BMS 告警、座舱偏好（挂载为独立 Router） |
| `deps.py` | 路由共享依赖：MySQL 连接工厂与 Bearer Token 鉴权（打破 app ↔ vehicle 循环导入） |

## 关键设计（面试考点）

- **CAS 防重复扣款**：`UPDATE ... WHERE status='PENDING'` + `rowcount` 校验，并发重复支付仅一人成功（`test_pay_idempotency.py` 实测）；
- **秒杀三级方案**：前置无锁快速失败 → Redis 原子库存 → Lua 脚本单次往返完成「检查+扣减+计数」；`PERF_MODE=slow` 保留粗粒度分布式锁作为反面教材；
- **Webhook 安全**：HMAC-SHA256 验签 + 时间戳防重放（±300s）+ `SET NX` 幂等去重 + 先 ACK 后异步流转；
- **启动自愈**：服务启动即执行 `db_init/*.sql`（单一事实来源）并自检补齐 `created_at` 列与联合索引；
- **统一健壮性**：全局异常兜底（结构化 500）、请求访问日志、`/health` 探针、`/metrics` Prometheus 指标。

## 启动

```bash
venv\Scripts\python.exe -m uvicorn mock_server.app:app --host 0.0.0.0 --port 8000
# Swagger: http://127.0.0.1:8000/docs ｜ 健康: http://127.0.0.1:8000/health
```

## 接口总览

| 域 | 接口 |
| :--- | :--- |
| 电商 | 登录、创建订单、支付、取消、秒杀、库存查询、Webhook 回调、订单状态查询、支付+通知、积分查询、批量造数、库存重置、测试状态归零 |
| 车联网 | 车辆事件上报、指令创建/执行/查询、BMS 告警上报/检索、座舱偏好 UPSERT/查询 |
