# ai_agent/__init__.py — 包初始化
#
# 把项目根目录注入 sys.path，保证两种运行方式都能正常 import config：
#   1. python ai_agent/failure_analyzer.py        （直接运行脚本，脚本目录在 sys.path 首位）
#   2. python -m ai_agent.failure_analyzer        （模块方式运行）
# 原因：直接运行 ai_agent/ 下的脚本时，Python 只把 ai_agent/ 目录加入 sys.path，
# 项目根目录的 config.py 无法被 import；本文件在包初始化时先行执行，提前修复。
import sys

from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
