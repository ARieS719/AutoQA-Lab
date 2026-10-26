# tests/utils.py — 测试通用工具库
import os
import time

import yaml


def read_yaml_data(file_name):
    """
    通用的 YAML 数据读取函数
    """
    # 获取当前 utils.py 所在的绝对路径目录 (即 tests/ 目录)
    current_dir = os.path.dirname(__file__)

    # 拼装出 YAML 文件的绝对路径: tests/data/xxx.yaml
    yaml_path = os.path.join(current_dir, "data", file_name)

    # 打开并加载 YAML 数据
    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
        return data


def poll_until(predicate, timeout: float = 3.0, interval: float = 0.2, description: str = "条件达成"):
    """通用轮询断言工具：每隔 interval 秒检查一次 predicate，超时抛 AssertionError

    灰盒异步链路（Webhook 回调流转 / MQ 积分发放）测试的公共基座。
    """
    deadline = time.time() + timeout
    last_value = None
    while time.time() < deadline:
        value = predicate()
        last_value = value
        if value:
            return value
        time.sleep(interval)
    raise AssertionError(f"轮询断言超时：等待 {timeout}s 后仍未满足『{description}』，最后观测值: {last_value!r}")
