import os
import subprocess
import glob
import json
import re
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from dotenv import load_dotenv  # 新增导入

# 自动寻找项目根目录的 .env 文件，并将里面的密钥加载到内存中
load_dotenv()
# ==========================================
# 1. 核心基建
# ==========================================
os.environ["OPENAI_API_KEY"] = os.getenv("OPENAI_API_KEY")
os.environ["OPENAI_API_BASE"] = "https://api.siliconflow.cn/v1"
llm = ChatOpenAI(model="deepseek-ai/DeepSeek-V3", temperature=0.1)

# ==========================================
# 2. 核心功能：获取变更与测试列表
# ==========================================
def get_git_diff():
    """获取当前代码库的变更 (对比上一次 commit，提取工作区和暂存区的差异)"""
    try:
        result = subprocess.run(["git", "diff", "HEAD"], capture_output=True, text=True, encoding="utf-8")
        return result.stdout.strip()
    except Exception as e:
        return ""

def get_all_test_files():
    """扫描项目所有的测试文件"""
    files = glob.glob("tests/test_*.py")
    return [f.replace("\\", "/") for f in files]  # 兼容 Windows 的斜杠路径

# ==========================================
# 3. AI 智能调度引擎
# ==========================================
def smart_select_tests(diff_content, test_files):
    if not diff_content:
        return []

    print("正在启动 AI 引擎，分析代码变更 (Git Diff)...")
    prompt = ChatPromptTemplate.from_messages([
        ("system", """你是一个大厂的高级持续集成(CI)调度算法。
你的任务是根据研发提交的代码变更(Git Diff)，从现有的全量测试用例库中，精准挑选出需要运行的测试文件。

规则：
1. 如果变更涉及核心业务逻辑（比如改了订单、支付、数据库逻辑），找出名字最相关的测试文件。
2. 如果变更仅仅是改了 README、注释、或者纯前端样式，与后端业务无关，返回空列表。
3. 【严格输出格式】：你只能输出一个合法的 JSON 字符串数组，不要包含任何 Markdown 符号(如 ```json)或解释文字！
例如：["tests/test_order.py", "tests/test_payment.py"]"""),
        ("human", "【可用测试文件库】:\n{test_files}\n\n【Git Diff 代码变更】:\n{diff_content}")
    ])

    chain = prompt | llm
    response = chain.invoke({"test_files": json.dumps(test_files), "diff_content": diff_content})
    
    try:
        # 净化输出，防止模型犯抽带了 markdown 标记
        clean_output = response.content.replace("```json", "").replace("```", "").strip()
        selected_files = json.loads(clean_output)
        return selected_files
    except Exception as e:
        print(f" AI 返回格式异常，触发安全降级（兜底全量执行）: {response.content}")
        return test_files # 兜底策略：如果 AI 解析出错，为了保障质量，全量跑测试

# ==========================================
# 4. CI/CD 路由入口
# ==========================================
if __name__ == "__main__":
    print("==============================================")
    print("[阶段三：智能精准测试调度 (Smart Test Selection)] 启动")
    print("==============================================")
    
    diff = get_git_diff()
    all_tests = get_all_test_files()
    
    if not all_tests:
        print("未在 tests/ 目录下找到任何 test_*.py 文件！")
        exit(1)

    print(f"当前项目拥有全量测试用例: {len(all_tests)} 个")
    
    if not diff:
        print("状态：Git Diff 为空。当前代码库没有未提交的业务变更。")
        print("体验提示：请随便去修改一行你的业务代码（不要 commit），然后再运行此脚本！")
    else:
        print(f"成功提取代码变更，DIFF 长度: {len(diff)} 字符")
        selected_tests = smart_select_tests(diff, all_tests)
        
        print("\n==============================================")
        if not selected_tests:
            print("AI 决策结果：【跳过测试阶段】\n原因：代码变更未影响核心业务逻辑，绿灯直接放行！")
        else:
            print(f"AI 决策结果：精准锁定 {len(selected_tests)} 个强相关测试文件！避免了全量浪费！")
            print(f"目标文件: {selected_tests}")
            
            # 动态组装 pytest 命令
            cmd = ["pytest"] + selected_tests + ["-v", "--tb=short"]
            print(f"\n 动态生成的流水线命令: {' '.join(cmd)}")
            
            # 执行针对性测试 (带上 UTF-8 护盾防止 Windows 乱码报错)
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            subprocess.run(cmd, env=env)