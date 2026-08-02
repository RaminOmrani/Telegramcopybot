FROM python:3.11-slim

# فونت برای واترمارک + کتابخانه‌های تصویر
RUN apt-get update && apt-get install -y --no-install-recommends \
        fonts-dejavu-core \
        libjpeg62-turbo \
        zlib1g \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY telkap/ ./telkap/

RUN mkdir -p /app/data/downloads

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATABASE_URL=sqlite+aiosqlite:///data/telkap.db \
    DOWNLOAD_DIR=/app/data/downloads

CMD ["python", "-m", "telkap.main"]
