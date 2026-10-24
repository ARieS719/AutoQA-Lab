# tests/conftest.py
import os
import glob
import pytest
import requests
import pymysql
from config import settings as app_config
from ai_agent.failure_analyzer import LangChainFailureAgent

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", 3306))
DB_USER = os.getenv("DB_USER", "qa_user")
DB_PASS = os.getenv("DB_PASS", "qa_pass")
DB_NAME = os.getenv("DB_NAME", "automation_shop")

@pytest.fixture(scope="session")
def auth_session():
    """全局鉴权夹具：自动登录并在 Session Header 中注入 Bearer Token"""
    print("\n[Fixture启动] 正在进行全局登录并获取 Token...")
    session = requests.Session()
    login_url = f"{app_config.BASE_URL}/api/v1/login"
    
    try:
        resp = session.post(login_url, params={"username": "admin", "password": "123456"}, timeout=3)
        token = resp.json().get("token")
        if not token:
            pytest.fail("致命错误：无法获取 Token，鉴权失败！")
        session.headers.update({"Authorization": f"Bearer {token}"})
        print(f"[Fixture启动] Token 注入成功: {token[:10]}...")
    except Exception as e:
        pytest.fail(f"登录接口请求异常，请检查 FastAPI 靶场服务是否启动: {str(e)}")
        
    yield session
    
    print("\n[Fixture结束] 测试执行完毕，销毁全局 HTTP Session。")
    session.close()

@pytest.fixture(scope="session")
def db_connection():
    """全局数据库连接夹具 (Session级别)：供白盒落库状态断言使用"""
    print("\n[DB连接] 正在连接 MySQL 测试数据库...")
    connection = pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True
    )
    yield connection
    
    print("\n[DB连接] 所有测试结束，断开数据库连接。")
    connection.close()

@pytest.fixture(scope="function")
def cleanup_order_db(db_connection):
    """
    订单业务专属 Teardown 数据清理夹具：
    自动清理测试期间生成的带有 'QA_AUTO_' 前缀的测试订单，防止测试脏数据污染环境。
    """
    yield
    print("\n[数据清理] 开始执行订单表测试脏数据清理...")
    try:
        with db_connection.cursor() as cursor:
            sql = "DELETE FROM orders WHERE item_name LIKE %s"
            cursor.execute(sql, ("QA_AUTO_%",))
            deleted_rows = cursor.rowcount
        db_connection.commit()
        print(f"[数据清理] 完毕，安全清理了 {deleted_rows} 条以 QA_AUTO_ 开头的测试订单。")
    except Exception as e:
        db_connection.rollback()
        print(f"[数据清理异常] 回滚事务: {str(e)}")

# 实例化 AI 故障诊断 Agent
diagnostic_agent = LangChainFailureAgent()

@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Pytest 钩子函数：当测试用例执行失败时，自动捕获堆栈与服务端日志触发 AI 根因诊断"""
    outcome = yield
    report = outcome.get_result()

    if report.when == "call" and report.failed:
        test_name = item.nodeid
        error_trace = str(call.excinfo.value) if call.excinfo else report.longreprtext
        print(f"\n\n[AI Agent 介入] 检测到测试用例失败: {test_name}，正在拉取日志进行智能归因...")
        
        server_logs = ""
        try:
            log_files = sorted(glob.glob("logs/server_*.log"))
            if log_files:
                with open(log_files[-1], "r", encoding="utf-8") as f:
                    server_logs = "".join(f.readlines()[-30:])
        except Exception:
            pass

        analysis_result = diagnostic_agent.analyze(
            test_name=test_name,
            error_trace=error_trace,
            server_logs=server_logs
        )
        print("\n================ AI 诊断报告 ================")
        print(analysis_result)
        print("================================================\n")