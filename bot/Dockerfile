FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg git && \
    rm -rf /var/lib/apt/lists/*

COPY bot/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --upgrade pip "setuptools>=75" wheel && \
    pip install --no-cache-dir -r requirements.txt

COPY shared /app/shared
COPY bot /app/bot

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

CMD ["python", "-m", "bot.main"]
