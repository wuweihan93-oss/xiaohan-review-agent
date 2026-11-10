FROM python:3.12-slim-bookworm

# 手动在国内构建时默认使用镜像站；GitHub Actions 可以切换到官方源。
ARG DEBIAN_MIRROR=https://mirrors.aliyun.com
ARG PYPI_INDEX=https://mirrors.cloud.tencent.com/pypi/simple/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PATH="/app/backend/.venv/bin:$PATH"

# 使用国内镜像安装 Faiss、ONNX 和 Milvus Lite 的 Linux 运行库。
RUN sed -i "s|http://deb.debian.org|${DEBIAN_MIRROR}|g; s|https://deb.debian.org|${DEBIAN_MIRROR}|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

# 从国内 PyPI 镜像安装固定版本，避免额外下载 GHCR 上的 uv 镜像。
RUN python -m pip install --no-cache-dir --index-url "${PYPI_INDEX}" uv==0.13.6
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
# 版本与哈希仍来自锁文件；依赖从腾讯云镜像安装，避免阿里云缺少部分锁定版本。
RUN --mount=type=cache,target=/root/.cache/uv \
    uv export --frozen --no-dev --no-emit-project --format requirements.txt --output-file /tmp/requirements.txt > /dev/null \
    && uv venv --python /usr/local/bin/python .venv \
    && uv pip install --python .venv/bin/python --require-hashes \
        --default-index "${PYPI_INDEX}" --requirements /tmp/requirements.txt \
    && rm -f /tmp/requirements.txt
COPY backend/app ./app
RUN mkdir -p /app/backend/data /app/backend/uploads /app/tutorial

EXPOSE 8010
HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8010/api/health', timeout=8)"

# Milvus Lite 使用本地文件，保持单进程；部署时不使用开发模式的 --reload。
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8010", "--workers", "1"]
