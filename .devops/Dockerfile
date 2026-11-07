# CPU-only agent/UI image. Keep llama.cpp + Qwen on the host.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Build with pinned Python dependencies from your existing project.
COPY requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir -r /app/requirements.txt

# Include application source; sensitive/persistent files excluded via .dockerignore.
# The personal vault is bind-mounted at runtime (see compose.yaml), not baked in.
COPY . /app

# Drop privileges for execution.
RUN useradd --create-home --uid 10001 agent && \
    mkdir -p /app/vault && \
    chown -R agent:agent /app
USER agent

EXPOSE 8000

# Overridden via compose.yaml. Default is an example only.
CMD ["python", "-m", "uvicorn", "src.app:app", "--host", "0.0.0.0", "--port", "8000"]
