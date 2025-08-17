import os, tempfile, shutil
from typing import Optional
from urllib.parse import urlparse

import requests
from fastapi import FastAPI, Form, Body, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from yt_dlp import YoutubeDL
import imageio_ffmpeg as ffmpeg

# ffmpeg yolunu PATH'e ekle
os.environ["PATH"] += os.pathsep + os.path.dirname(ffmpeg.get_ffmpeg_exe())

app = FastAPI()

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def expand_short_url(url: str, timeout: float = 6.0) -> str:
    """vt.tiktok.com gibi kısaltılmış linkleri gerçek TikTok URL’sine çevirir."""
    try:
        r = requests.get(url, allow_redirects=True, timeout=timeout)
        return r.url or url
    except Exception:
        return url

def normalize_tiktok_url(url: str) -> str:
    """TikTok kısa linklerini genişletir."""
    host = urlparse(url).netloc.lower()
    if host.startswith("vt.tiktok.com"):
        url = expand_short_url(url)
    return url

@app.post("/get_video")
def get_video(
    # Hem FormData hem JSON destekleniyor; anahtar adı **url**
    url: Optional[str] = Form(None),
    payload: Optional[dict] = Body(None)
):
    url = url or (payload or {}).get("url")
    if not url:
        raise HTTPException(status_code=422, detail="url zorunludur.")

    url = normalize_tiktok_url(url)

    try:
        tmpdir = tempfile.mkdtemp()
        outtmpl = f"{tmpdir}/%(title)s.%(ext)s"

        ydl_opts = {
            "outtmpl": outtmpl,
            "merge_output_format": "mp4",
            "http_headers": {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "Referer": "https://www.tiktok.com/",
            },
        }

        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            if not filename.endswith(".mp4"):
                filename = filename.rsplit(".", 1)[0] + ".mp4"

        def cleanup():
            shutil.rmtree(tmpdir, ignore_errors=True)

        return FileResponse(
            filename,
            media_type="video/mp4",
            filename=os.path.basename(filename),
            background=BackgroundTask(cleanup),
        )

    except Exception as e:
        msg = str(e)
        if "HTTP Error 403" in msg or "Unsupported URL" in msg:
            msg = "TikTok bağlantısına erişilemedi. Linki uygulama yerine tarayıcıdan kopyalayın."
        raise HTTPException(status_code=400, detail=f"İndirme hatası: {msg}")

@app.get("/")
def root():
    return {"message": "Backend ayakta, video indirme için /get_video kullan."}
