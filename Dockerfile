# Python'ın hafif bir sürümünü temel imaj olarak kullanırız
FROM python:3.10-slim

# Gerekli sistem paketlerini kurarız (ffmpeg dahil)
RUN apt-get update && apt-get install -y ffmpeg

# Proje dosyalarını Docker imajına kopyalarız
WORKDIR /app
COPY requirements.txt .

# Python kütüphanelerini kurarız
RUN pip install --no-cache-dir -r requirements.txt

# Geri kalan kodumuzu kopyalarız
COPY . .

# Uygulamanın çalışacağı portu belirtiriz
EXPOSE 8000

# Uygulamayı başlatırız (Shell form, ortam değişkenleri genişler)
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port $PORT"]
