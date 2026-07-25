# 运行环境：Python 3.12（与实施规格第 2 节一致）
FROM python:3.12-slim

WORKDIR /app

# 仅安装编译期可能需要的系统库；psycopg/binary 与 pgvector 均提供预编译 wheel，
# 正常情况下不会触发编译，这里保留 libpq-dev 仅是兜底
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# 先装依赖再做代码拷贝，利用镜像层缓存
COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir .

COPY . .

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
