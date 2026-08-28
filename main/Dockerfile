FROM python:3.10-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential cmake libgl1 libglib2.0-0 libopenblas-dev libgomp1 \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
WORKDIR /workspace
COPY app/requirements.txt /workspace/requirements.txt
RUN /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir -r /workspace/requirements.txt

COPY app /workspace/app
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /workspace
USER appuser
EXPOSE 8000
CMD ["/opt/venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
