import os
import subprocess
import requests
import json
import re
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from dotenv import load_dotenv  # 新增导入

# 自动寻找项目根目录的 .env 文件，并将里面的密钥加载到内存中
load_dotenv()
# =====================================================================
# 1. 核心基建：配置顶级开源大模型
# =====================================================================
os.environ["OPENAI_API_KEY"] = os.getenv("OPENAI_API_KEY")
os.environ["OPENAI_API_BASE"] = "https://api.siliconflow.cn/v1"
llm = ChatOpenAI(model="deepseek-ai/DeepSeek-V3", temperature=0.1)

# =====================================================================
# 2. 核心功能函数
# =====================================================================
def get_live_api_docs():
    """拉取最新接口规范"""
    try:
        data = requests.get("http://127.0.0.1:8000/openapi.json").json()
        return json.dumps({"paths": data.get("paths"), "schemas": data.get("components", {}).get("schemas")}, ensure_ascii=False)
    except Exception:
        return "无法获取文档"

def run_pytest(target_path):
    """执行 Pytest 并捕获全局输出"""
    print(f"\n 正在执行测试: pytest {target_path}")
    
    # 强制指定环境变量和流输出为 utf-8，无视 Windows 本地的 GBK 限制
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    
    result = subprocess.run(
        ["pytest", target_path, "-v", "--tb=short"], 
        capture_output=True, 
        text=True,
        encoding="utf-8",  # 强行用 utf-8 解码 Pytest 的输出
        env=env
    )
    return result.returncode, result.stdout

def extract_failed_files(pytest_output):
    """从 Pytest 的全局输出中，用正则精准提取出所有失败的 .py 文件路径"""
    failed_files = set()
    for line in pytest_output.split("\n"):
        # 匹配 FAILED tests/test_broken.py::test_xyz 或者 ERROR
        if line.startswith("FAILED") or line.startswith("ERROR"):
            match = re.search(r"(tests/[^\s:]+\.py)", line)
            if match:
                failed_files.add(match.group(1))
    return list(failed_files)

def heal_test_code(test_file_path, error_log):
    """核心：调用 AI 修复单个损坏的代码"""
    print(f"检测到 {test_file_path} 失败！触发 AI Self-Healing...")
    
    with open(test_file_path, "r", encoding="utf-8") as f:
        bad_code = f.read()
        
    api_docs = get_live_api_docs()
    
    prompt = ChatPromptTemplate.from_messages([
        ("system", """你是一个自动化测试自愈(Self-Healing)专家。
系统当前最新的 OpenAPI 规范如下：
{api_docs}

任务：
1. 分析下方提供的【报错日志】和【损坏的测试源码】。
2. 结合 OpenAPI 规范，找出导致失败的原因。
3. 修复代码。
4. 极其重要：你只能输出修复后的完整 Python 纯代码，必须用 ```python 和 ``` 包裹。绝对不要输出任何解释性的废话！"""),
        ("human", "【报错日志】:\n{error_log}\n\n【损坏的代码】:\n{bad_code}")
    ])
    
    chain = prompt | llm
    print("大模型正在深度阅读报错堆栈与文档，重构测试代码...")
    response = chain.invoke({"api_docs": api_docs, "error_log": error_log, "bad_code": bad_code})
    
    match = re.search(r"```python(.*?)```", response.content, re.DOTALL)
    if match:
        healed_code = match.group(1).strip()
        with open(test_file_path, "w", encoding="utf-8") as f:
            f.write(healed_code)
        print("✅ AI 修复完成！已将新代码覆写到原文件。")
        return True
    else:
        print("❌ AI 未返回标准的 Python 代码格式，自愈失败。")
        return False

# =====================================================================
# 3. CI/CD 全局扫描与路由中枢
# =====================================================================
if __name__ == "__main__":
    # 真实 CI/CD 中的执行方式：扫描整个 tests 目录
    target_dir = "tests/"
    
    code, out = run_pytest(target_dir)
    
    if code == 0:
        print(" 全局测试通过，流水线健康！")
    else:
        print(" 检测到全局流水线存在失败用例！正在启动大模型分析日志...")
        
        # 1. 自动揪出所有报错的文件
        failed_files = extract_failed_files(out)
        
        if not failed_files:
            print("无法从日志中提取具体的失败文件，可能并非断言错误。")
            print("完整报错日志如下：\n", out)
        else:
            print(f"锁定到 {len(failed_files)} 个损坏的测试文件: {failed_files}")
            
            # 2. 遍历修复每一个坏掉的文件
            heal_count = 0
            for bad_file in failed_files:
                print(f"\n==============================================")
                print(f"正在抢修文件: {bad_file}")
                success = heal_test_code(bad_file, out)
                if success:
                    heal_count += 1
            
            # 3. 修复完毕后，进行一次全局回归重跑
            if heal_count > 0:
                print(f"\n 成功修复 {heal_count} 个文件！正在触发全局流水线重跑 (Global Auto-Retry)...")
                code2, out2 = run_pytest(target_dir)
                if code2 == 0:
                    print("运行成功！所有损坏的用例均已被 AI 修复，流水线恢复绿色状态 (Passed)！")
                else:
                    print("全局重试依然有失败项，部分复杂逻辑可能需要人工介入。")