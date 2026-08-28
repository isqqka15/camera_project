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
ENV DLIB_NO_GUI_SUPPORT=1
ENV DLIB_GIF_SUPPORT=0
ENV DLIB_JPEG_SUPPORT=1
ENV DLIB_PNG_SUPPORT=1
ENV CFLAGS="-mno-avx -mno-avx2 -mno-fma -mno-sse4.1 -mno-sse4.2"
ENV CXXFLAGS="-mno-avx -mno-avx2 -mno-fma -mno-sse4.1 -mno-sse4.2"
ENV CMAKE_ARGS="-DDLIB_GIF_SUPPORT=OFF -DDLIB_JPEG_SUPPORT=ON -DDLIB_USE_CUDA=OFF -DUSE_AVX_INSTRUCTIONS=OFF"
ENV USE_AVX_INSTRUCTIONS=0
RUN CMAKE_BUILD_PARALLEL_LEVEL=2 pip install --no-cache-dir dlib
RUN /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir -r /workspace/requirements.txt

COPY app /workspace/app
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /workspace
USER appuser
EXPOSE 8000
CMD ["/opt/venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
