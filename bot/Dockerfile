FROM python:3.11-slim

WORKDIR /app

COPY bot/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY shared /app/shared
COPY bot /app/bot

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

CMD ["python", "-m", "bot.main"]
