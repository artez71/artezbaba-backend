import os
import re
import tempfile
import shutil
import unicodedata
from typing import Optional, Dict
from urllib.parse import urlparse, quote

import requests
from fastapi import FastAPI, Form, Body, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import StreamingResponse

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

def ascii_fallback(name: str) -> str:
    """
    HTTP header için ASCII fallback dosya adı üretir.
    Türkçe/özel harfleri temizler.
    """
    normalized = unicodedata.normalize("NFKD", name)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    safe = re.sub(r'[^A-Za-z0-9._-]+', '_', ascii_only).strip("._")
    return safe or "file"

@app.post("/get_video")
def get_video(
    url: Optional[str] = Form(None),
    payload: Optional[Dict] = Body(None)
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
            "noplaylist": True,
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
            path = ydl.prepare_filename(info)
            if not path.endswith(".mp4"):
                path = path.rsplit(".", 1)[0] + ".mp4"

        def file_iter(p: str, chunk_size: int = 1024 * 1024):
            try:
                with open(p, "rb") as f:
                    while True:
                        chunk = f.read(chunk_size)
                        if not chunk:
                            break
                        yield chunk
            finally:
                shutil.rmtree(tmpdir, ignore_errors=True)

        basename = os.path.basename(path)
        fallback_name = ascii_fallback(basename)
        filename_star = quote(basename)

        headers = {
            # fallback + UTF-8 gerçek ad
            "Content-Disposition": f"attachment; filename=\"{fallback_name}\"; filename*=UTF-8''{filename_star}",
            "Cache-Control": "no-store",
        }
        try:
            size = os.path.getsize(path)
            headers["Content-Length"] = str(size)
        except Exception:
            pass

        return StreamingResponse(file_iter(path), media_type="video/mp4", headers=headers)

    except Exception as e:
        msg = str(e)
        if "HTTP Error 403" in msg or "Unsupported URL" in msg:
            msg = "TikTok bağlantısına erişilemedi. Linki uygulama yerine tarayıcıdan kopyalayın."
        raise HTTPException(status_code=400, detail=f"İndirme hatası: {msg}")

@app.get("/")
def root():
    return {"message": "Backend ayakta, video indirme için /get_video kullan."}
