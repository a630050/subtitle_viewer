# 第一阶段：使用一个功能完整的 Python 版本来安装依赖
FROM python:3.11-slim AS builder

# 设置环境变量，优化 Docker 构建
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# 安装依赖到一个虚拟环境中
RUN python -m venv .venv
COPY requirements.txt ./
# 激活虚拟环境并安装 requirements.txt 中的包
RUN . .venv/bin/activate && pip install --no-cache-dir -r requirements.txt

# 第二阶段：使用一个轻量的镜像来运行应用
FROM python:3.11-slim

WORKDIR /app

# 从第一阶段拷贝已经安装好依赖的虚拟环境
COPY --from=builder /app/.venv .venv/
# 拷贝您应用的所有代码
COPY . .

# 设置环境变量，让后续指令能找到虚拟环境中的 Python 和包
ENV PATH="/app/.venv/bin:$PATH"

# 暴露端口，与 fly.toml 和 gunicorn 的 --bind port 一致
EXPOSE 8080

# 启动指令：使用 Gunicorn 和 Eventlet 来运行应用
# 这会执行 fly.toml 中 [processes] 定义的指令
# 注意：Fly.io 默认会忽略 CMD，而去执行 fly.toml 中的 [processes] 指令
# 但保留一个 CMD 是一个好习惯，以便 Docker 镜像可以独立运行
CMD ["gunicorn", "-k", "eventlet", "-w", "1", "--bind", "0.0.0.0:8080", "app:app"]