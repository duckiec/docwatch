FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p /app/data

FROM base AS test

RUN pip install --no-cache-dir pytest==8.3.4
RUN pytest -q

FROM base AS runtime

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD python -c "import sys, urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=3).read(); sys.exit(0)"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
