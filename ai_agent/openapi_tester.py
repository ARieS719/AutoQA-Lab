import json
import os
import sys
from pathlib import Path

# 直接运行本脚本 (python ai_agent/openapi_tester.py) 时，项目根目录不在 sys.path，
# 导致 `from config import settings` 失败；此处先行把项目根注入 sys.path。
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pymysql
import requests

from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

from config import settings

BASE_URL = settings.BASE_URL

def get_live_api_docs() -> str:
    """动态拉取 FastAPI 实时 OpenAPI (Swagger) 规范"""
    try:
        resp = requests.get(f"{BASE_URL}/openapi.json", timeout=3)
        data = resp.json()
        return json.dumps({
            "endpoints": data.get("paths", {}),
            "schemas": data.get("components", {}).get("schemas", {})
        }, ensure_ascii=False)
    except Exception as e:
        return f"无法拉取 API 文档: {e}"

@tool
def send_dynamic_http_request(method: str, endpoint: str, json_payload: dict = None) -> str:
    """通用 HTTP 执行器：自动携带鉴权 Token 向被测靶场发起请求"""
    url = f"{BASE_URL}{endpoint}"
    headers = {"Authorization": "Bearer mock_token_888"}
    try:
        print(f"\n[HTTP 执行] {method.upper()} {url} | Payload: {json_payload}")
        if method.upper() == "POST":
            resp = requests.post(url, json=json_payload, headers=headers, timeout=5)
        elif method.upper() == "PUT":
            resp = requests.put(url, json=json_payload, headers=headers, timeout=5)
        else:
            resp = requests.get(url, headers=headers, timeout=5)
        return f"HTTP Status: {resp.status_code}, Body: {resp.text}"
    except Exception as e:
        return f"请求异常: {str(e)}"

@tool
def execute_readonly_sql(sql_query: str) -> str:
    """底层数据库白盒穿透器：仅允许执行 SELECT 语句验证落库状态"""
    if not sql_query.strip().upper().startswith("SELECT"):
        return "安全拦截：仅允许执行 SELECT 只读查询。"
    try:
        print(f"\n[SQL 白盒穿透] {sql_query}")
        conn = pymysql.connect(
            host=settings.DB_HOST,
            port=settings.DB_PORT,
            user=settings.DB_USER,
            password=settings.DB_PASS,
            database=settings.DB_NAME,
            cursorclass=pymysql.cursors.DictCursor,
        )
        with conn.cursor() as cursor:
            cursor.execute(sql_query)
            records = cursor.fetchall()
        conn.close()
        return f"数据库查询结果: {records}"
    except Exception as e:
        return f"SQL执行失败: {str(e)}"

def run_production_qa_agent(intent: str):
    api_key = settings.OPENAI_API_KEY.strip()
    if not api_key:
        print("未检测到 OPENAI_API_KEY，请在 .env 文件中配置后运行。")
        return

    os.environ["OPENAI_API_KEY"] = api_key
    os.environ["OPENAI_API_BASE"] = settings.OPENAI_API_BASE

    llm = ChatOpenAI(model=settings.LLM_MODEL, temperature=0.1)
    tools = [send_dynamic_http_request, execute_readonly_sql]
    tools_map = {t.name: t for t in tools}
    llm_with_tools = llm.bind_tools(tools)
    api_schema = get_live_api_docs()

    system_prompt = f"""你是一个资深 AI 测试开发架构师。
系统当前 OpenAPI 规范：
{api_schema}

请基于 ReAct 循环自主完成全链路测试：
1. 解析 OpenAPI 规范，调用 HTTP 工具发起业务请求构造数据。
2. 提取响应中的业务主键（如 order_id），调用 SQL 工具穿透查询底层 `orders` 表核对状态。
3. 输出结构化中文测试报告。
"""
    messages = [HumanMessage(content=f"{system_prompt}\n\n测试任务指令：{intent}")]
    print(f"\n[AI 测开任务下发]：{intent}\n" + "=" * 70)

    for _ in range(6):
        response = llm_with_tools.invoke(messages)
        messages.append(response)
        if not response.tool_calls:
            print("\n[AI 自动化测试与白盒校验报告]：\n")
            print(response.content)
            return

        for tool_call in response.tool_calls:
            tool_name = tool_call["name"]
            tool_args = tool_call["args"]
            print(f"[Agent 工具调度] 调用: {tool_name} | 参数: {tool_args}")
            selected_tool = tools_map.get(tool_name)
            tool_result = selected_tool.invoke(tool_args) if selected_tool else f"未找到工具 {tool_name}"
            messages.append(ToolMessage(content=str(tool_result), tool_call_id=tool_call["id"]))

if __name__ == "__main__":
    test_intent = (
        "1. 调用创建订单接口，购买商品 'QA_AUTO_AI智能体测试显卡'，数量 3 件；"
        "2. 获取返回的 order_id 后，立即调用支付接口完成该订单支付；"
        "3. 编写 SQL 穿透查询 `orders` 表，验证该 order_id 的 status 是否已准确流转为 'PAID'。"
    )
    run_production_qa_agent(test_intent)
