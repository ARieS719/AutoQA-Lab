# ============================================================================
# ai_agent/failure_analyzer.py — Pytest Hook 失败堆栈与服务端日志智能归因
#
# 设计原则：优雅降级。未配置 OPENAI_API_KEY 时返回本地规则诊断，
# LLM 调用异常时不中断 CI 流水线，保证「AI 是增强、不是负担」。
# ============================================================================
import os
import sys

from pathlib import Path

# 直接运行本脚本 (python ai_agent/failure_analyzer.py) 时，项目根目录不在 sys.path，
# 导致 `from config import settings` 失败；此处先行把项目根注入 sys.path。
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_openai import ChatOpenAI

from config import settings


class LangChainFailureAgent:
    def __init__(self):
        self.api_key = settings.OPENAI_API_KEY.strip()
        self.chain = None

        if self.api_key:
            os.environ["OPENAI_API_KEY"] = self.api_key
            os.environ["OPENAI_API_BASE"] = settings.OPENAI_API_BASE
            self.llm = ChatOpenAI(
                model=settings.LLM_MODEL,
                temperature=0.1,
                max_retries=2,
            )
            self.prompt_template = PromptTemplate(
                input_variables=["test_name", "error_trace", "server_logs"],
                template="""你是一名资深测试开发架构师。请根据以下信息进行 CI/CD 流水线的测试失败归因。

【失败测试用例】: {test_name}
【抛出异常堆栈】: {error_trace}
【服务端尾部日志】: {server_logs}

请输出 Markdown 格式的排查诊断报告：
1. 核心故障点总结（区分是后端服务 Bug 还是测试脚本断言失效）
2. 深度根因推导（结合报错堆栈与服务端日志）
3. 建议排查与修复步骤
""",
            )
            self.chain = self.prompt_template | self.llm | StrOutputParser()

    def analyze(self, test_name: str, error_trace: str, server_logs: str = "无附加日志") -> str:
        """向大模型发起调用；若未配置 API Key 则优雅降级，确保 CI 流水线不中断"""
        if not self.chain:
            last_line = error_trace.splitlines()[-1] if error_trace else "Unknown"
            return (
                f"[本地降级诊断] 未检测到 OPENAI_API_KEY 环境变量，跳过远程 LLM 调用。\n"
                f"- 失败用例: {test_name}\n"
                f"- 核心异常: {last_line}"
            )
        try:
            return self.chain.invoke(
                {
                    "test_name": test_name,
                    "error_trace": error_trace,
                    "server_logs": server_logs,
                }
            )
        except Exception as e:
            return f"LangChain Agent 调用异常: {str(e)}"


if __name__ == "__main__":
    agent = LangChainFailureAgent()
    res = agent.analyze(
        test_name="tests/test_order.py::test_full_order_lifecycle",
        error_trace="AssertionError: 预期状态码 200，实际返回 500",
        server_logs="pymysql.err.OperationalError: (2003, 'Can\\'t connect to MySQL server')",
    )
    print(res)
