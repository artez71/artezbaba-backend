# main.py
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

import yt_dlp
import httpx
import re
import os
import tempfile
import shutil

app = FastAPI(title="MRB Video Downloader API", version="1.3.0")

# --- CORS ---
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN] if FRONTEND_ORIGIN != "*" else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Config ---
USE_COOKIES = os.getenv("USE_COOKIES", "0") == "1"
COOKIES_FILE = os.getenv("COOKIES_FILE")

# 📌 Mobil User-Agent (Android Chrome)
UA = (
    "Mozilla/5.0 (Linux; Android 10; SM-G973F) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36"
)

# --- Models ---
class LinkRequest(BaseModel):
    url: str

# --- Utils ---
def sanitize_filename(name: str, ext: str = "mp4") -> str:
    name = re.sub(r"[\\/:*?\"<>|]+", " ", name).strip()
    name = re.sub(r"\s+", " ", name)
    if not name:
        name = "video"
    if not name.lower().endswith(f".{ext}"):
        name = f"{name}.{ext}"
    return name

def pick_best_mp4_format(info: dict) -> dict | None:
    formats = info.get("formats") or []
    mp4s = [
        f for f in formats
        if f.get("ext") == "mp4" and f.get("vcodec") != "none" and f.get("acodec") != "none"
    ]
    if mp4s:
        return sorted(mp4s, key=lambda f: (f.get("tbr") or 0), reverse=True)[0]
    mp4_any = [f for f in formats if f.get("ext") == "mp4"]
    if mp4_any:
        return sorted(mp4_any, key=lambda f: (f.get("tbr") or 0), reverse=True)[0]
    return None

def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None

async def stream_from_url(url: str, filename: str, content_type: str | None = None, extra_headers: dict | None = None):
    base_headers = {"User-Agent": UA}
    if extra_headers:
        base_headers.update(extra_headers)

    async with httpx.AsyncClient(headers=base_headers, timeout=None, follow_redirects=True) as client:
        async with client.stream("GET", url) as r:
            if r.status_code >= 400:
                raise HTTPException(status_code=400, detail=f"Kaynak indirilemedi: {r.status_code}")
            ct = content_type or r.headers.get("Content-Type", "video/mp4")
            disp = f'attachment; filename="{filename}"'
            return StreamingResponse(
                r.aiter_bytes(),
                media_type=ct,
                headers={"Content-Disposition": disp},
            )

def _cleanup_dir(path: str):
    shutil.rmtree(path, ignore_errors=True)

def _build_ytdlp_opts(skip_download: bool, outtmpl: str | None = None, url: str | None = None) -> dict:
    opts = {
        "quiet": True,
        "noplaylist": True,
        "http_headers": {"User-Agent": UA},
        "skip_download": skip_download,
    }
    if USE_COOKIES and COOKIES_FILE:
        opts["cookies"] = COOKIES_FILE
    if outtmpl:
        opts["outtmpl"] = outtmpl

    # Twitter (X) için format ayarı
    if url and "twitter.com" in url or "x.com" in url:
        opts["format"] = "bestvideo+bestaudio/best"

    return opts

async def download_to_mp4_with_ytdlp(url: str) -> tuple[str, str, str]:
    if not ffmpeg_available():
        raise HTTPException(status_code=500, detail="FFmpeg bulunamadı.")
    tmpdir = tempfile.mkdtemp(prefix="mrb_")
    outtmpl = os.path.join(tmpdir, "%(title).200B.%(ext)s")
    ydl_opts = _build_ytdlp_opts(skip_download=False, outtmpl=outtmpl, url=url)
    ydl_opts["merge_output_format"] = "mp4"
    ydl_opts["postprocessors"] = [{"key": "FFmpegVideoRemuxer", "preferredformat": "mp4"}]

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        downloaded = ydl.prepare_filename(info)

    base = os.path.splitext(os.path.basename(downloaded))[0]
    candidate = os.path.join(tmpdir, f"{base}.mp4")
    final_path = candidate if os.path.exists(candidate) else downloaded
    final_name = sanitize_filename(info.get("title") or info.get("id") or "video", ext="mp4")
    return final_path, final_name, tmpdir

# --- Routes ---
@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/get_video")
async def get_video(link_request: LinkRequest):
    url = link_request.url.strip()
    try:
        probe_opts = _build_ytdlp_opts(skip_download=True, url=url)
        with yt_dlp.YoutubeDL(probe_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as e:
        raise HTTPException(status_code=400, detail=f"Çözümleme hatası: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    title = info.get("title") or info.get("id") or "video"
    fmt = pick_best_mp4_format(info)

    if fmt and fmt.get("url"):
        filename = sanitize_filename(title, ext=fmt.get("ext", "mp4"))
        ct = (fmt.get("http_headers") or {}).get("Content-Type") or None
        return await stream_from_url(fmt["url"], filename, content_type=ct, extra_headers=fmt.get("http_headers"))

    final_path, final_name, tmpdir = await download_to_mp4_with_ytdlp(url)
    task = BackgroundTask(_cleanup_dir, tmpdir)
    return FileResponse(path=final_path, media_type="video/mp4", filename=final_name, background=task)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
