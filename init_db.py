# ============================================================================
# init_db.py — 数据库初始化脚本（单一事实来源：db_init/*.sql）
#
# 用法：
#   docker compose up -d --wait test-db   # 先拉起 MySQL
#   python init_db.py                     # 一键执行全部建表 SQL
#
# 说明：本脚本与容器 entrypoint、服务启动自愈执行的是同一批 SQL 文件，
# 避免三处建表逻辑漂移。车联网 6 大核心业务表定义见 db_init/02_vehicle_tables.sql。
# ============================================================================
import glob
import os

import pymysql

from config import settings


def load_sql_statements():
    """读取 db_init 下全部 .sql 并按分号拆分。

    注意：不手工剥离 `--` 注释（MySQL 会把 `-- ` 行注释连同 DDL 一并解析），
    避免把「注释 + 建表语句」整体误过滤掉。
    """
    statements = []
    for path in sorted(glob.glob(os.path.join("db_init", "*.sql"))):
        print(f"[init_db] 正在解析: {path}")
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
        for stmt in raw.split(";"):
            stmt = stmt.strip()
            if stmt:
                statements.append(stmt)
    return statements


def init_database():
    print("正在连接 MySQL (Docker)...")
    try:
        conn = pymysql.connect(
            host=settings.DB_HOST,
            port=settings.DB_PORT,
            user=settings.DB_USER,
            password=settings.DB_PASS,
            database=settings.DB_NAME,
        )
        with conn.cursor() as cursor:
            for i, sql in enumerate(load_sql_statements(), 1):
                cursor.execute(sql)
                print(f"第 {i} 条建表语句执行完成！")
        conn.commit()
        conn.close()
        print("所有核心业务表（电商 + 车联网）已全部创建完毕！")
    except Exception as e:
        print(f"数据库连接或执行失败: {e}")


if __name__ == "__main__":
    init_database()
