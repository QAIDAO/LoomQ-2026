FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# pyqpanda 依赖 libcurl.so.4
RUN apt-get update && apt-get install -y --no-install-recommends \
    libcurl4 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace/submission
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

CMD ["python", "evaluator.py", "--json-out", "/tmp/loomq-public-report.json"]
