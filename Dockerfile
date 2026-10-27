FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ffmpeg \
        fonts-noto-core \
        fonts-noto-extra \
        libfreetype6-dev \
        libfribidi-dev \
        libharfbuzz-dev \
        libjpeg62-turbo-dev \
        libraqm-dev \
        zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --no-binary=Pillow -r requirements.txt \
    && python -c "from PIL import features; assert features.check('raqm'), 'Pillow was built without RAQM'; print('RAQM:', features.check('raqm'))"

COPY data/quran.json data/quran_qpc_hafs.json data/quran_qpc_hafs.sha256 ./data/
COPY . .
RUN python fetch_assets.py && test -f public/index.html && test -f data/quran_qpc_hafs.json

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-10000}"]
