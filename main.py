from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, HttpUrl

import yt_dlp
import httpx
import re
import os
import unicodedata
import logging
from typing import Dict, Any, Optional

# --- LOGGING ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("mrb-downloader")

# --- APP ---
app = FastAPI(title="MRB Video Downloader API", version="1.6.2")

# --- CORS ---
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN] if FRONTEND_ORIGIN != "*" else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- CONFIG ---
USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 10; SM-G973F) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36"
)
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "30"))
MAX_CONNS = int(os.getenv("HTTP_MAX_CONNS", "10"))
KEEPALIVE_CONNS = int(os.getenv("HTTP_KEEPALIVE_CONNS", "5"))

SUPPORTED_PATTERNS = [
    r"^(https?://)?(www\.)?(twitter\.com|x\.com)/.+",
    r"^(https?://)?(www\.)?tiktok\.com/.+",
    r"^(https?://)?(vm|vt)\.tiktok\.com/.+",
]

# ---- MODELS ----
class LinkRequest(BaseModel):
    url: HttpUrl

# ---- HELPERS ----
def is_supported(url: str) -> bool:
    return any(re.match(p, url, flags=re.IGNORECASE) for p in SUPPORTED_PATTERNS)

def sanitize_filename(name: str, ext: str = "mp4") -> str:
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    name = re.sub(r"[\\/:*?\"<>|]+", " ", name).strip()
    name = re.sub(r"\s+", "_", name)
    if not name:
        name = "video"
    return f"{name}.{ext}"

def pick_best_mp4_format(info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    formats = info.get("formats") or []
    mp4s = [
        f for f in formats
        if f.get("ext") == "mp4"
        and f.get("vcodec") not in (None, "none")
        and f.get("acodec") not in (None, "none")
        and (f.get("protocol") or "").lower() not in {"m3u8", "m3u8_native", "http_dash_segments", "dash"}
    ]
    if mp4s:
        return sorted(mp4s, key=lambda f: (f.get("height") or 0, f.get("tbr") or 0), reverse=True)[0]
    return None

def _build_ytdlp_opts() -> dict:
    opts = {
        "quiet": True,
        "noplaylist": True,
        "http_headers": {"User-Agent": USER_AGENT},
        "skip_download": True,
        "retries": 3,
        "fragment_retries": 3,
        "concurrent_fragment_downloads": 5,
        "format": "best",
        "format_sort": ["ext:mp4", "filesize:desc"],
    }
    
    use_cookies = os.getenv("USE_COOKIES", "0") == "1"
    cookies_file = os.getenv("COOKIES_FILE") or None
    if use_cookies and cookies_file:
        opts["cookies"] = cookies_file

    return opts

# ---- ROUTES ----
@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/get_video")
async def get_video(link_request: LinkRequest):
    url = str(link_request.url).strip()
    logger.info(f"Gelen istek: {url}")

    if not is_supported(url):
        raise HTTPException(status_code=400, detail="Sadece Twitter/X ve TikTok linkleri destekleniyor.")

    try:
        probe_opts = _build_ytdlp_opts()
        with yt_dlp.YoutubeDL(probe_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                raise ValueError("Video bilgisi alınamadı.")
    except Exception as e:
        logger.error(f"Video çözümlenemedi: {e}")
        raise HTTPException(status_code=500, detail=f"Video çözümlenirken bir hata oluştu: {str(e)}")

    title = info.get("title") or info.get("id") or "video"
    fmt = pick_best_mp4_format(info)

    if not fmt or not fmt.get("url"):
        raise HTTPException(status_code=404, detail="MP4 video formatı bulunamadı.")

    logger.info(f"Progressive MP4 bulundu: {fmt.get('url')}")
    filename = sanitize_filename(title, ext=fmt.get("ext", "mp4"))
    
    try:
        async with httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT},
            timeout=httpx.Timeout(HTTP_TIMEOUT),
            limits=httpx.Limits(max_connections=MAX_CONNS, max_keepalive_connections=KEEPALIVE_CONNS),
        ) as client:
            async with client.stream("GET", fmt["url"], follow_redirects=True) as r:
                r.raise_for_status()
                ct = r.headers.get("Content-Type", "video/mp4")
                disp = f'attachment; filename="{filename}"'
                
                return StreamingResponse(
                    r.aiter_bytes(),
                    media_type=ct,
                    headers={
                        "Content-Disposition": disp,
                        "Content-Length": r.headers.get("Content-Length")
                    }
                )
    except httpx.HTTPError as e:
        logger.error(f"Akış hatası: {e}")
        raise HTTPException(status_code=500, detail="Video akışı başlatılamadı.")

# ---- ENTRYPOINT ----
if __name__ == "__main__":
    import uvicorn
    logger.info("MRB Video Downloader API başlatılıyor.")
    
    port = int(os.getenv("PORT", 8000))
    is_prod = os.getenv("IS_PROD", "False").lower() == "true"
    
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=not is_prod)
