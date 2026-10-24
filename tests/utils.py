# tests/utils.py
import yaml
import os

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