FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    openssh-client \
    curl \
    ca-certificates \
 && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://code.claude.com/install.sh | bash || true

WORKDIR /app

COPY code_reviewer/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --upgrade pip setuptools && \
    pip install --no-cache-dir -r requirements.txt

COPY shared /app/shared
COPY code_reviewer /app/code_reviewer

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

CMD ["python", "-m", "code_reviewer.main"]
