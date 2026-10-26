# examples/ — 演示素材

存放面向特定能力的演示脚本，**不属于常规测试收集范围**（`pyproject.toml` 已配置 `norecursedirs`，裸跑 `pytest` 不会误收集）。

## healing_demo/ — AI 断言自愈演示

`test_broken_demo.py` 是一个**故意写坏断言**的测试脚本（期望 200，实际应为 422），用于演示 `ai_agent/self_healer.py` 的自动修复能力。

### 演示步骤

```bash
# 1. 确保靶场已在 8000 端口运行、.env 已配置 OPENAI_API_KEY
# 2. 把坏素材放进常规测试目录
Copy-Item examples\healing_demo\test_broken_demo.py tests\test_broken_demo.py

# 3. 运行自愈引擎：AI 读取报错堆栈 + OpenAPI 规范，自动修复断言
venv\Scripts\python.exe ai_agent/self_healer.py

# 4. 观察：tests\test_broken_demo.py 被修复为 assert 422（原文件备份为 .bak），回归通过
# 5. 演示完清理
Remove-Item tests\test_broken_demo.py, tests\test_broken_demo.py.bak
```
