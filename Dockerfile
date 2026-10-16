FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV RICH_DATA_DIR=/data
ENV RICH_FEE_RATE=1.0
ENV RICH_FEE_MIN=10
RUN mkdir -p /data

CMD ["python", "-u", "rich_all.py"]
