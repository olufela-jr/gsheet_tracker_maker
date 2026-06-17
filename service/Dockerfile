FROM python:3.12-slim

WORKDIR /app

# Install dependencies first so this layer caches across code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Cloud Run injects PORT. gunicorn binds to it.
ENV PORT=8080

CMD exec gunicorn --bind :$PORT --workers 2 --threads 8 --timeout 120 main:app
