# ai_agent/ — AI 测开质效工具链（DeepSeek-V3 + LangChain）

AutoQA-Lab 的「AI 提效层」：四个独立工具覆盖测试生命周期的四个环节，全部实现**无 Key 优雅降级**——LLM 不可用时 CI 流水线永不中断。

## 工具清单

| 工具 | 环节 | 能力 | 降级设计 |
| :--- | :--- | :--- | :--- |
| `failure_analyzer.py` | 失败诊断 | 挂在 Pytest Hook（`conftest.py`）上，失败即自动捕获完整堆栈 + 服务端尾部日志，输出 Markdown 根因报告 | 无 Key 输出本地规则诊断 |
| `smart_scheduler.py` | 回归调度 | 解析 `git diff HEAD` 代码变更，LLM 从全量用例中精准挑选受影响子集 | 无 Key 走确定性关键词映射；解析失败全量兜底 |
| `openapi_tester.py` | 智能测试 | 动态拉取 `/openapi.json`，ReAct 循环驱动 HTTP 请求 + 只读 SQL 白盒核验，输出测试报告 | 无 Key 明确提示退出 |
| `self_healer.py` | 断言自愈 | 运行 pytest 揪出失败文件 → 结合报错与 OpenAPI 规范让 LLM 重写 → 修复前自动备份、仅限改写 `tests/` 文件 → 自动回归重跑 | 无 Key 跳过修复 |

## 运行方式（两种均可）

```bash
venv\Scripts\python.exe ai_agent/smart_scheduler.py     # 直接运行
venv\Scripts\python.exe -m ai_agent.smart_scheduler     # 模块方式
```

前置：`.env` 中配置 `OPENAI_API_KEY`（默认走 SiliconFlow 的 DeepSeek-V3，可通过 `OPENAI_API_BASE` / `LLM_MODEL` 切换）。

## 设计原则

1. **延迟构建**：LLM 客户端只在真正调用时创建，未配置 Key 时导入模块也不崩溃；
2. **安全护栏**：自愈引擎只允许改写 `tests/` 文件；SQL 工具只放行 SELECT 语句；
3. **配置单一来源**：模型、网关、密钥统一读取 `config.py`，代码零硬编码。
