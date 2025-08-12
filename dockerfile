FROM python:3.12-slim

# ffmpeg yükle
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Railway ortamında portu ENV’den alacak
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
