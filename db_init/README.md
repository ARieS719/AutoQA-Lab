# db_init/ — MySQL 初始化 SQL（单一事实来源）

AutoQA-Lab 的全部建表语句。**同一份 SQL 被三条路径消费**，杜绝「测试环境与生产环境建表漂移」：

| 路径 | 触发时机 |
| :--- | :--- |
| MySQL 容器 entrypoint | `docker compose up` 首次创建数据卷时自动执行 |
| 服务启动自愈 | FastAPI 启动时读取本目录全部 `*.sql` 执行（跳过建库语句） |
| `init_db.py` 脚本 | 手动执行 `python init_db.py` 一键建表 |

## 文件清单

| 文件 | 内容 |
| :--- | :--- |
| `01_create_tables.sql` | `orders` 表（电商域）+ `(status, created_at)` 联合索引 |
| `02_vehicle_tables.sql` | 车联网 6 大核心业务表：`vehicle_events` / `vehicle_commands` / `bms_alerts` / `cabin_settings` / `charging_orders`（充电订单扩展位）/ `ota_tasks`（OTA 升级扩展位） |

## 约定

- 全部 `CREATE TABLE IF NOT EXISTS`，幂等可重复执行；
- 建库语句（`CREATE DATABASE` / `USE`）只写在 `01` 文件头部，供容器 entrypoint 以 root 身份执行；运行时自愈会跳过这两类语句；
- 新增表请在 `02` 或后续编号文件中追加，并在 `mock_server` 的启动自愈与对应测试中同步落地。
