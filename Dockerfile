# ============================================================
# AutoQA-Lab 被测靶场镜像
# 使用场景：无本地 Python 环境时，一行命令拉起被测服务
#   docker build -t qa-mock-server .
#   docker run -d --name qa-mock-server -p 8000:8000 \
#       -e DB_HOST=host.docker.internal -e REDIS_HOST=host.docker.internal \
#       qa-mock-server
# ============================================================

# 1. 轻量级 Python 3.11 官方镜像作为基础
FROM python:3.11-slim

# 2. 日志实时刷新，避免 Docker 吞掉服务输出
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# 3. 设置容器工作目录
WORKDIR /app

# 4. 先拷贝依赖清单并安装（利用 Docker 层缓存，加速重复构建）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 5. 拷贝项目代码与配置
COPY . .

# 6. 暴露服务端口
EXPOSE 8000

# 7. 启动被测微服务靶场
CMD ["uvicorn", "mock_server.app:app", "--host", "0.0.0.0", "--port", "8000"]
