# ============================================================================
# examples/healing_demo/test_broken_demo.py — AI 自愈引擎演示素材（故意损坏）
#
# ⚠️ 本文件故意写错断言（期望 200，实际应为 422），用于演示 self_healer 的修复能力。
# 它位于 examples/ 目录，pytest 常规收集不会执行（pyproject.toml 已排除）。
#
# 演示步骤（README 有详细说明）：
#   1. 将本文件复制到 tests/ 目录下；
#   2. 运行 python ai_agent/self_healer.py  -> AI 读取报错 + OpenAPI 规范，自动修复断言；
#   3. 观察文件被修复为 assert resp.status_code == 422，且回归通过（原文件自动备份为 .bak）；
#   4. 删除 tests/ 下的临时文件。
# ============================================================================
import requests


def test_broken_order():
    """
    测试订单创建参数校验失败场景（演示版本：断言被故意写坏）
    """
    url = "http://127.0.0.1:8000/api/v1/orders"
    headers = {"Authorization": "Bearer mock_token_888"}

    # 错误的 Payload：字段名错了，值也错了
    bad_payload = {
        "quantity": -5  # 字段名错了，值也错了
    }

    resp = requests.post(url, json=bad_payload, headers=headers)

    # 【故意损坏】正确断言应为 422，此处错误地期望 200，等待 AI Self-Healing 修复
    assert resp.status_code == 200
    assert "detail" in resp.json()
