FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    openssh-client \
    curl \
    ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Claude Code CLI is required by the agent SDK's headless runtime.
RUN curl -fsSL https://code.claude.com/install.sh | bash || true

WORKDIR /app

COPY developer_agent/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY shared /app/shared
COPY developer_agent /app/developer_agent

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

CMD ["python", "-m", "developer_agent.main"]
