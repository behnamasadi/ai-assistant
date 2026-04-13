FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    openssh-client \
    curl \
    ca-certificates \
    nodejs \
    npm \
 && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://code.claude.com/install.sh | bash || true

WORKDIR /app

COPY ui_tester/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --upgrade pip setuptools && \
    pip install --no-cache-dir -r requirements.txt

COPY shared /app/shared
COPY ui_tester /app/ui_tester

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

CMD ["python", "-m", "ui_tester.main"]
