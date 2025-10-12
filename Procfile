# 1️⃣ Modern Python sürümü
FROM python:3.11-slim

# 2️⃣ Çalışma dizini
WORKDIR /app

# 3️⃣ Gereksiz cache'leri kapat
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# 4️⃣ Gerekli sistem paketleri (yt-dlp ve imageio-ffmpeg için)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libgl1 \
    git \
    && apt-get clean

# 5️⃣ pip'i güncelle
RUN pip install --upgrade pip setuptools wheel

# 6️⃣ Python bağımlılıklarını yükle
COPY requirements.txt .
RUN pip install -r requirements.txt

# 7️⃣ Proje dosyalarını kopyala
COPY . .

# 8️⃣ Railway'in PORT değişkeniyle uvicorn'u başlat
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
