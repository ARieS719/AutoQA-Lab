# ============================================================================
# mock_server/deps.py — 路由层共享依赖（打破 app.py <-> vehicle.py 循环导入）
# ============================================================================
import pymysql

from fastapi import Header, HTTPException

from config import settings


def get_db_connection():
    """白盒落库连接工厂：全部路由共享同一套 MySQL 连接配置"""
    return pymysql.connect(
        host=settings.DB_HOST,
        port=settings.DB_PORT,
        user=settings.DB_USER,
        password=settings.DB_PASS,
        database=settings.DB_NAME,
        cursorclass=pymysql.cursors.DictCursor,
    )


def verify_token(authorization: str = Header(None)):
    """Bearer Token 鉴权依赖：全业务域（电商 + 车联网）统一复用"""
    if not authorization or authorization != "Bearer mock_token_888":
        raise HTTPException(status_code=401, detail="无效或缺失的 Token，禁止访问！")
    return authorization
