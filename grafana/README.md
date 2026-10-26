# grafana/ — 压测监控可视化（开箱即用）

AutoQA-Lab 的「可观测性」层：`docker compose up -d` 后 Grafana 自动加载 Prometheus 数据源与预置看板，压测期间实时观察瓶颈，无需任何手工配置。

## 文件清单

| 路径 | 职责 |
| :--- | :--- |
| `provisioning/datasources/datasource.yml` | 预置 Prometheus 数据源（uid: `prometheus`，指向 Compose 网络内 `http://prometheus:9090`） |
| `provisioning/dashboards/dashboards.yml` | 看板 Provider：自动扫描并加载下方 JSON 看板 |
| `dashboards/qa-mock-server.json` | QA 压测监控看板 |

## 看板面板

| 面板 | 指标 | 用途 |
| :--- | :--- | :--- |
| 核心吞吐量 RPS | `sum(rate(http_requests_total[1m]))` | 实时总吞吐 |
| TP99 响应时延 | `histogram_quantile(0.99, ...)` | 阈值着色：<50ms 绿 / 50-500ms 黄 / >500ms 红 |
| 吞吐量趋势 | 按 `path` 分组的 RPS 曲线 | 定位热点接口 |
| 响应时延分位数 | TP50 / TP99 双曲线 | slow/fast 对比实验 |
| HTTP 状态码分布 | 按 `status` 分组的堆叠柱 | 压测异常率观测 |

## 使用

```bash
docker compose up -d prometheus grafana
# 打开 http://localhost:3000（admin / admin）
# Prometheus 抓取对象：host.docker.internal:8000/metrics（被测靶场的 Instrumentator 暴露）
```

> 指标来源：`prometheus-fastapi-instrumentator` 在 `mock_server/app.py` 中注入，`/metrics` 端点自动暴露请求量、时延直方图等核心指标。
