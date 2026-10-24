# 1. 拿一个轻量级的 Python 3.11 官方镜像作为基础
FROM python:3.11-slim

# 2. 设置容器里的工作目录
WORKDIR /app

# 3. 把生成的依赖清单拷进去
COPY requirements.txt .

# 4. 在容器里安装这些依赖
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 5. 把你的项目代码（连带配置文件等）全盘拷入容器
COPY . .

# 6. 暴露 8000 端口，让外面能访问
EXPOSE 8000

# 7. 指定启动这个集装箱时要执行的命令
CMD ["uvicorn", "mock_server.app:app", "--host", "0.0.0.0", "--port", "8000"]