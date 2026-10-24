import os
from langchain_openai import ChatOpenAI
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser
from dotenv import load_dotenv

load_dotenv()

class LangChainFailureAgent:
    def __init__(self):
        self.api_key = os.getenv("OPENAI_API_KEY", "").strip()
        self.base_url = os.getenv("OPENAI_API_BASE", "https://api.siliconflow.cn/v1")
        self.model_name = os.getenv("LLM_MODEL", "deepseek-ai/DeepSeek-V3")
        self.chain = None

        if self.api_key:
            os.environ["OPENAI_API_KEY"] = self.api_key
            os.environ["OPENAI_API_BASE"] = self.base_url
            self.llm = ChatOpenAI(
                model=self.model_name,
                temperature=0.1,
                max_retries=2
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
"""
            )
            self.chain = self.prompt_template | self.llm | StrOutputParser()

    def analyze(self, test_name: str, error_trace: str, server_logs: str = "无附加日志") -> str:
        """向大模型发起调用；若未配置 API Key 则优雅降级，确保 CI 流水线不中断"""
        if not self.chain:
            return (
                f"[本地降级诊断] 未检测到 OPENAI_API_KEY 环境变量，跳过远程 LLM 调用。\n"
                f"- 失败用例: {test_name}\n"
                f"- 核心异常: {error_trace.splitlines()[-1] if error_trace else 'Unknown'}"
            )
        try:
            return self.chain.invoke({
                "test_name": test_name,
                "error_trace": error_trace,
                "server_logs": server_logs
            })
        except Exception as e:
            return f"LangChain Agent 调用异常: {str(e)}"

if __name__ == "__main__":
    agent = LangChainFailureAgent()
    res = agent.analyze(
        test_name="tests/test_order.py::test_full_order_lifecycle",
        error_trace="AssertionError: 预期状态码 200，实际返回 500",
        server_logs="pymysql.err.OperationalError: (2003, 'Can\\'t connect to MySQL server')"
    )
    print(res)