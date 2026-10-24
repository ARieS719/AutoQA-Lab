# 🚀 Agentic-QA-Engine: 微服务全链路自动化测试与 AI 质效平台

[![CI/CD Pipeline](https://github.com/ARieS719/agentic-qa-engine/actions/workflows/main.yml/badge.svg)](https://github.com/ARieS719/agentic-qa-engine/actions)
[![Coverage](https://img.shields.io/badge/coverage-88%25-brightgreen.svg)]()
[![Python](https://img.shields.io/badge/python-3.11-blue.svg)]()
[![Framework](https://img.shields.io/badge/FastAPI%20%7C%20Pytest%20%7C%20Locust-orange.svg)]()

面向高并发交易与异步通知场景构建的企业级微服务测试工程基建。系统涵盖 **FastAPI 白盒被测靶场、三层白盒接口自动化、Locust 高并发压测与瓶颈调优、GitHub Actions 持续集成门禁（80%+ 覆盖率卡点）**，并融合 **LLM (DeepSeek-V3)** 实现基于 OpenAPI 的测试生成、Git Diff 精准回归调度与报错自愈。

---

## 🏗️ 核心架构与技术亮点

### 1. 全链路白盒接口自动化测试体系 (`tests/`)
* **高仿真微服务被测靶场 (`mock_server/app.py`)**：基于 `FastAPI + MySQL + Redis` 实现核心订单状态机（`PENDING -> PAID / CANCELLED`）、Redis 分布式锁秒杀防超卖、第三方支付 Webhook 签名回调及 MQ 异步积分发放最终一致性。
* **三层白盒断言机制**：
  1. **契约层**：基于 `Pydantic (schemas.py)` 强校验 HTTP 响应结构与字段类型；
  2. **持久层**：通过 `PyMySQL` 穿透校验数据库落库状态与库存扣减准确性，配合 `cleanup_order_db` 夹具实现测试脏数据 Teardown 自动清库闭环；
  3. **缓存与异步层**：设计轮询断言（Polling Assertion）与 `ThreadPoolExecutor` 瞬时并发冲击，验证 Redis 分布式锁防超卖与异步消息最终一致性。
* **多维数据驱动与 Fuzzing 模糊测试**：结合 `YAML` 参数化驱动覆盖等价类/边界值异常流，并引入 `Hypothesis` 属性测试生成 200+ 极端畸形 Payload 轰炸接口，确保服务零 `500` 崩溃。

### 2. 高并发性能压测与瓶颈调优闭环 (`tests/locustfile.py`)
* **压测前置内存队列预热**：利用 Locust `@events.test_start` 钩子在压测启动前自动校验并批量灌入 10,000+ 待支付订单至线程安全内存队列 `queue.Queue()`，消除压测过程中的读库 I/O 干扰。
* **性能瓶颈定位与调优实验 (`PERF_MODE=slow | fast`)**：
  * **问题定位**：在 `slow` 基准模式下，通过 Locust 阶梯式压测与 Prometheus 监控定位到高频秒杀与支付链路存在 **Redis 分布式锁粒度过大（锁内包含非核心风控阻塞 I/O）** 以及 **`orders` 表缺少 `(status, created_at)` 联合索引导致全表扫描** 两大瓶颈。
  * **调优效果**：通过引入**前置无锁快速失败拦截（Fast-Fail）+ 细粒度锁剥离非核心 I/O + MySQL 联合索引自愈**，核心并发吞吐量与响应时延实现数量级跃升：

| 压测指标 (100 并发虚拟用户) | 优化前 (`PERF_MODE=slow`) | 优化后 (`PERF_MODE=fast`) | 提升幅度 |
| :--- | :---: | :---: | :---: |
| **核心接口吞吐量 (TPS / RPS)** | ~18.5 req/s | **620+ req/s** | **提升 30 倍+ (综合混合链路提升 6 倍+)** |
| **TP99 响应时延 (99%ile Latency)** | 2150 ms | **38 ms** | **时延降低 98%** |
| **高并发超卖率 / 异常率** | 0% (存在等锁超时 500) | **0% 超卖 / 0% 异常** | **100% 账实一致** |

### 3. CI/CD 持续集成门禁与 AI 质效工具链 (`ai_agent/` & `.github/workflows/`)
* **容器化流水线与覆盖率卡点**：基于 `GitHub Actions + Docker Compose` 自动拉起 MySQL 与 Redis 基础设施，结合 `TestClient` 进程内 ASGI 契约测试与端到端测试，设置 `--cov-fail-under=80` 强制覆盖率门禁（当前覆盖率 **88%**），并自动发布 Allure 报告至 GitHub Pages。
* **Git Diff 智能精准调度 (`smart_scheduler.py`)**：解析 `git diff HEAD` 代码变更轨迹，动态筛选受影响的 Pytest 测试子集执行，缩短日常 CI 回归耗时。
* **OpenAPI 智能体测试与报错自愈 (`openapi_tester.py` & `failure_analyzer.py` & `self_healer.py`)**：
  * 动态拉取 `/openapi.json` 规范，通过 ReAct 工具调用自动发起 HTTP 请求并执行只读 SQL 完成数据库白盒核验；
  * 基于 Pytest `pytest_runtest_makereport` 钩子捕获失败用例堆栈与服务端尾部日志，自动输出 Markdown 根因分析报告与代码自愈补丁。

---

## 📂 项目目录结构

```text
agentic-qa-engine/
├── .github/workflows/main.yml      # GitHub Actions CI/CD 自动化流水线与覆盖率门禁
├── ai_agent/                       # AI 测开提效工具链 (DeepSeek-V3 + LangChain)
│   ├── failure_analyzer.py         # Pytest Hook 失败堆栈与服务端日志智能归因
│   ├── openapi_tester.py           # 基于 OpenAPI 的 ReAct 接口测试与 SQL 白盒穿透智能体
│   ├── self_healer.py              # 测试脚本断言失效自动修复引擎
│   └── smart_scheduler.py          # 基于 Git Diff 的精准回归测试调度器
├── db_init/01_create_tables.sql    # MySQL 初始化建表与联合索引脚本
├── examples/healing_demo/          # 独立存放用于演示 AI 自愈能力的示例脚本
├── mock_server/app.py              # FastAPI 高并发交易与异步通知微服务靶场
├── tests/                          # 核心自动化测试与压测套件
│   ├── data/order_abnormal_cases.yaml  # 边界值与无效等价类 YAML 测试数据
│   ├── conftest.py                 # 全局 Session 鉴权、DB 连接、脏数据清理与 AI 诊断钩子
│   ├── locustfile.py               # Locust 内存队列预热与高并发混合压测脚本
│   ├── schemas.py                  # Pydantic 响应 Schema 契约模型
│   ├── test_asgi_coverage.py       # 进程内 ASGI 全分支白盒契约与覆盖率测试
│   ├── test_async_webhook.py       # 异步 Webhook 签名回调与轮询断言测试
│   ├── test_concurrency_lock.py    # Redis 分布式锁瞬时并发防超卖测试
│   ├── test_fuzz.py                # Hypothesis 属性驱动模糊测试 (Fuzzing)
│   ├── test_mq_consistency.py      # Redis MQ 异步积分发放最终一致性测试
│   └── test_order.py               # 订单全生命周期状态机与 YAML 数据驱动测试
├── docker-compose.yml              # MySQL 8.0 + Redis 7 + Prometheus 容器编排
├── prometheus.yml                  # Prometheus 监控采集配置
└── requirements.txt                # 项目依赖清单