FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends espeak-ng libgomp1 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN useradd -m app && mkdir -p /app/data && chown -R app /app/data
USER app
EXPOSE 8000 9092
HEALTHCHECK CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/health')"
# exec: uvicorn must receive Docker's stop signal itself to shut down cleanly and let calls finish.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers"]
