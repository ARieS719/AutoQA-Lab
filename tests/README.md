# tests/ — 三层白盒自动化测试与压测套件

AutoQA-Lab 的「测试体系」：32 项用例 + 200 组 Hypothesis 模糊数据，服务端代码覆盖率 93.14%（CI 硬门禁 80%）。

## 三层白盒断言体系

```
┌─ 契约层 ──── Pydantic 强校验 HTTP 响应结构与字段类型
├─ 持久层 ──── PyMySQL 穿透校验落库状态，QA_AUTO_ 前缀 + Teardown 自动清库
└─ 异步层 ──── poll_until 轮询断言 + ThreadPoolExecutor 瞬时并发冲击
```

## 测试文件矩阵

| 文件 | 验证目标 | 特点 |
| :--- | :--- | :--- |
| `test_order.py` | 订单全生命周期状态机 | YAML 数据驱动 6 组边界值 |
| `test_pay_idempotency.py` | 并发支付恰 1 成功 | 10 线程 CAS 防重实测 |
| `test_concurrency_lock.py` | 10 线程抢 1 件 0 超卖 | 分布式锁/原子扣减 |
| `test_async_webhook.py` | 签名回调异步流转 | 真异步 + 轮询断言 |
| `test_mq_consistency.py` | 积分最终一致性 | 状态隔离 + 轮询 |
| `test_security.py` | 越权/伪造 Token/签名篡改/重放/SQL 注入 | 7 类安全场景 |
| `test_fuzz.py` | 200 组畸形 Payload 零 500 | Hypothesis 属性测试 |
| `test_vehicle.py` | 车联网四链路契约+落库 | 双域复用同一基建 |
| `test_asgi_coverage.py` | 进程内全分支覆盖 | CI 覆盖率确定性底座 |
| `locustfile.py` | 混合链路压测 + 库存/订单预热 | 100 并发 0 失败 |

## 公共基建

| 文件 | 职责 |
| :--- | :--- |
| `conftest.py` | 全局登录 Session、MySQL 白盒连接、脏数据清理夹具、AI 失败归因钩子（pytest_runtest_makereport） |
| `utils.py` | YAML 数据读取、`poll_until` 轮询断言基座 |
| `schemas.py` | Pydantic 响应契约模型 |
| `data/order_abnormal_cases.yaml` | 边界值/无效等价类测试数据 |

## 运行

```bash
# 全量 + 覆盖率门禁（需先启动靶场与 docker compose 基础设施）
venv\Scripts\python.exe -m pytest tests/ --cov=mock_server --cov-report=term-missing --cov-fail-under=80

# 单文件调试
venv\Scripts\python.exe -m pytest tests/test_security.py -v --tb=short
```
