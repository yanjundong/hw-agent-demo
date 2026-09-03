# AgentArts 平台标准 Dockerfile（参照 agentarts CLI 官方模板）
# 注意：AgentArts Runtime 为 ARM64（鲲鹏）架构，请在 ARM64 Linux
# 环境构建镜像（agentarts deploy 的 docker build 不带 --platform）。
FROM python:3.12-slim

# 平台运行时环境标记 + 日志即时输出
ENV DOCKER_CONTAINER=true
ENV PYTHONUNBUFFERED=1

# 使用国内 PyPI 镜像加速依赖下载（默认华为云；可换阿里云 mirrors.aliyun.com/pypi/simple/）
ENV PIP_INDEX_URL=https://mirrors.huaweicloud.com/repository/pypi/simple/
ENV PIP_TRUSTED_HOST=mirrors.huaweicloud.com

WORKDIR /app

# 创建非 root 用户（平台安全基线）
RUN groupadd -g 1000 appuser && \
    useradd -u 1000 -g 1000 -m -s /bin/bash appuser

# 先装依赖，利用 Docker 层缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码（.env 不进入镜像，环境变量由 AgentArts Runtime 注入）
COPY main.py .
COPY app/ ./app/
COPY static/ ./static/

RUN chown -R appuser:appuser /app

EXPOSE 8080

USER appuser

# 华为云标准启动：main.py 内调用 AgentArtsRuntimeApp 的 handler.run()
CMD ["python", "main.py"]
