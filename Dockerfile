FROM docker.m.daocloud.io/library/python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

COPY app ./app
COPY data/knowledge_base.txt ./data/knowledge_base.txt
# 确保 app/templates 目录存在（空目录 COPY 不会报错）
RUN mkdir -p /app/app/templates

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1"]
