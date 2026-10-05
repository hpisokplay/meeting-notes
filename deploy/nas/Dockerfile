FROM node:22-bookworm-slim
ENV DEBIAN_FRONTEND=noninteractive TZ=Asia/Taipei PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8 PYTHONDONTWRITEBYTECODE=1
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-pip ffmpeg ca-certificates fonts-noto-cjk procps && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /tmp/requirements.txt
RUN npm install -g @anthropic-ai/claude-code@2.1.280 && rm -f /usr/local/bin/claude
COPY claude-wrapper.sh /usr/local/bin/claude
RUN chmod +x /usr/local/bin/claude
WORKDIR /app
ENV MEETINGS_DATA=/data MEETINGS_STATIC=/app/web MEETINGS_WORKER_KEY_FILE=/run/meetings/worker.key HOME=/claude-home HF_HOME=/data/.hf
EXPOSE 8778
CMD ["python3", "-m", "server.main"]
