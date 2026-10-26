# ============================================================================
# ai_agent/smart_scheduler.py — 基于 Git Diff 的精准回归测试调度器
#
# 设计原则（本版修复）：
#   1. LLM 客户端延迟到真正使用时才创建（原先模块导入即实例化，未配置
#      OPENAI_API_KEY 时 import 即抛 TypeError，连优雅降级的机会都没有）；
#   2. 未配置 API Key 时降级为「确定性关键词映射」兜底，CI 永不中断；
#   3. LLM 解析失败时全量回归（宁多勿漏），保证质量红线。
# ============================================================================
import glob
import json
import os
import re
import subprocess
import sys

from pathlib import Path

# 直接运行本脚本 (python ai_agent/smart_scheduler.py) 时，项目根目录不在 sys.path，
# 导致 `from config import settings` 失败；此处先行把项目根注入 sys.path。
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from config import settings


def _build_llm():
    """延迟构建 LLM 客户端：仅当配置了 API Key 时才可用，否则返回 None"""
    api_key = settings.OPENAI_API_KEY.strip()
    if not api_key:
        return None
    from langchain_openai import ChatOpenAI  # 延迟导入，未装 langchain 也不影响规则降级

    os.environ["OPENAI_API_KEY"] = api_key
    os.environ["OPENAI_API_BASE"] = settings.OPENAI_API_BASE
    return ChatOpenAI(model=settings.LLM_MODEL, temperature=0.1)


def get_git_diff():
    """获取当前代码库的变更 (对比上一次 commit，提取工作区和暂存区的差异)"""
    try:
        result = subprocess.run(
            ["git", "diff", "HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
        )
        return result.stdout.strip()
    except Exception as e:
        print(f"[警告] 获取 Git Diff 失败 (仓库未初始化?): {e}")
        return ""


def get_all_test_files():
    """扫描项目所有的测试文件"""
    files = glob.glob("tests/test_*.py")
    return [f.replace("\\", "/") for f in files]


# ---------------------------------------------------------------------------
# 规则降级：关键词 -> 测试文件映射（无需 LLM 也能做精准调度）
# ---------------------------------------------------------------------------
RULE_MAP = [
    (re.compile(r"mock_server/app\.py|/orders|pay_order|cancel_order|状态机"), "tests/test_order.py"),
    (re.compile(r"seckill|分布式锁|redis.*lock|inventory"), "tests/test_concurrency_lock.py"),
    (re.compile(r"webhook|pay_callback|签名"), "tests/test_async_webhook.py"),
    (re.compile(r"pay_and_notify|积分|mq_consumer|MQ"), "tests/test_mq_consistency.py"),
    (re.compile(r"vehicle|车联网|bms|cabin|command"), "tests/test_vehicle.py"),
    (re.compile(r"mock_server/"), "tests/test_asgi_coverage.py"),
]


def rule_based_select(diff_content, test_files):
    """确定性规则调度：按变更关键词锁定相关测试，作为 LLM 缺失时的可靠兜底"""
    selected = []
    for pattern, test_file in RULE_MAP:
        if pattern.search(diff_content) and test_file in test_files:
            selected.append(test_file)
    # 变更触及核心业务但未命中任何规则时，保守全量回归
    return selected or test_files


def smart_select_tests(diff_content, test_files):
    """AI 智能调度：LLM 可用时走大模型决策，否则走规则降级；解析失败全量兜底"""
    if not diff_content:
        return []

    llm = _build_llm()
    if llm is None:
        print("[降级模式] 未配置 OPENAI_API_KEY，使用确定性关键词映射调度")
        return rule_based_select(diff_content, test_files)

    print("正在启动 AI 引擎，分析代码变更 (Git Diff)...")
    from langchain_core.prompts import ChatPromptTemplate

    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                """你是一个大厂的高级持续集成(CI)调度算法。
你的任务是根据研发提交的代码变更(Git Diff)，从现有的全量测试用例库中，精准挑选出需要运行的测试文件。

规则：
1. 如果变更涉及核心业务逻辑（比如改了订单、支付、数据库逻辑），找出名字最相关的测试文件。
2. 如果变更仅仅是改了 README、注释、或者纯前端样式，与后端业务无关，返回空列表。
3. 【严格输出格式】：你只能输出一个合法的 JSON 字符串数组，不要包含任何 Markdown 符号(如 ```json)或解释文字！
例如：["tests/test_order.py", "tests/test_payment.py"]""",
            ),
            ("human", "【可用测试文件库】:\n{test_files}\n\n【Git Diff 代码变更】:\n{diff_content}"),
        ]
    )

    chain = prompt | llm
    response = chain.invoke({"test_files": json.dumps(test_files), "diff_content": diff_content})

    try:
        # 净化输出，防止模型犯抽带了 markdown 标记
        clean_output = response.content.replace("```json", "").replace("```", "").strip()
        # 仅截取第一个 [...] 片段，避免模型输出多余解释
        match = re.search(r"\[.*\]", clean_output, re.DOTALL)
        clean_output = match.group(0) if match else clean_output
        selected_files = json.loads(clean_output)
        # 模型可能幻觉出不存在的文件，做一次白名单过滤
        valid = [f for f in selected_files if f in test_files]
        return valid or test_files
    except Exception as e:
        print(f"[警告] AI 返回格式异常，触发安全降级（兜底全量执行）: {response.content}")
        print(f"[警告] 解析错误详情: {e}")
        return test_files  # 兜底策略：为了保障质量，全量跑测试


# ---------------------------------------------------------------------------
# CI/CD 路由入口
# ---------------------------------------------------------------------------
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
