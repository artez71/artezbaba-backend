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
import unicodedata
import logging
from typing import Optional, Tuple, Dict, Any

# --- LOGGING ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("mrb-downloader")

# --- APP ---
app = FastAPI(title="MRB Video Downloader API", version="1.5.0")

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
USE_COOKIES = os.getenv("USE_COOKIES", "0") == "1"
COOKIES_FILE = os.getenv("COOKIES_FILE") or None

HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "30"))  # saniye
MAX_CONNS = int(os.getenv("HTTP_MAX_CONNS", "10"))
KEEPALIVE_CONNS = int(os.getenv("HTTP_KEEPALIVE_CONNS", "5"))

SUPPORTED_PATTERNS = [
    r"^(https?://)?(www\.)?(twitter\.com|x\.com)/.+",
    r"^(https?://)?(www\.)?tiktok\.com/.+",
    r"^(https?://)?(vm|vt)\.tiktok\.com/.+",
]

# ---- MODELS ----
class LinkRequest(BaseModel):
    url: str

# ---- HELPERS ----
def is_supported(url: str) -> bool:
    return any(re.match(p, url, flags=re.IGNORECASE) for p in SUPPORTED_PATTERNS)

def sanitize_filename(name: str, ext: str = "mp4") -> str:
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    name = re.sub(r"[\\/:*?\"<>|]+", " ", name).strip()
    name = re.sub(r"\s+", " ", name)
    if not name:
        name = "video"
    return f"{name}.{ext}"

def pick_best_mp4_format(info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    bad_protocols = {"m3u8", "m3u8_native", "http_dash_segments", "dash"}
    formats = info.get("formats") or []
    mp4s = [
        f for f in formats
        if f.get("ext") == "mp4"
        and f.get("vcodec") not in (None, "none")
        and f.get("acodec") not in (None, "none")
        and (f.get("protocol") or "").lower() not in bad_protocols
    ]
    if mp4s:
        return sorted(mp4s, key=lambda f: (f.get("tbr") or 0), reverse=True)[0]
    return None

def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None

def _cleanup_dir(path: str):
    try:
        shutil.rmtree(path, ignore_errors=True)
        logger.info(f"Geçici dizin silindi: {path}")
    except OSError as e:
        logger.error(f"Geçici dizin silinirken hata: {e}")

def _build_ytdlp_opts(skip_download: bool, url: str) -> dict:
    """
    Hız ve kararlılık için optimize:
      - concurrent_fragment_downloads: parça-parça paralel indirme
      - format: progressive mp4 odaklı (hls/dash hariç)
      - format_sort: mp4 ve daha küçük dosya tercih
      - retries: ağ hatalarında tekrar
    """
    opts = {
        "quiet": True,
        "noplaylist": True,
        "http_headers": {"User-Agent": USER_AGENT},
        "skip_download": skip_download,
        "outtmpl": os.path.join(tempfile.gettempdir(), "%(title).200B.%(ext)s"),
        "retries": 3,
        "fragment_retries": 3,
        "concurrent_fragment_downloads": 5,
        # bazı CDN'lerde TLS bug'ı varsa şunu açabilirsin: "nocheckcertificate": True,
    }

    if USE_COOKIES and COOKIES_FILE:
        opts["cookies"] = COOKIES_FILE

    # Progressive mp4’ü zorla; hls/dash yok
    opts["format"] = (
        "best[ext=mp4][protocol!=m3u8][protocol!=m3u8_native][protocol!=http_dash_segments]"
        "/best"  # fallback
    )
    opts["format_sort"] = ["ext:mp4:m4a", "filesize"]

    # Twitter/X için postprocess'e gerek kalmadan mp4 verebiliyor; yine de meta eklemek zarar vermez
    opts["postprocessors"] = [
        {"key": "FFmpegMetadata"},
    ]

    return opts

async def download_and_convert_with_ytdlp(url: str) -> Tuple[str, str, str]:
    if not ffmpeg_available():
        raise HTTPException(status_code=500, detail="FFmpeg bulunamadı.")

    tmpdir = tempfile.mkdtemp(prefix="mrb_")
    outtmpl = os.path.join(tmpdir, "%(title).200B.%(ext)s")

    ydl_opts = _build_ytdlp_opts(skip_download=False, url=url)
    ydl_opts["outtmpl"] = outtmpl

    # Eğer kaynak progressive mp4 vermediyse, mp4’e dönüştür (hız için preset veryfast)
    ydl_opts["postprocessors"] = ydl_opts.get("postprocessors", []) + [
        {"key": "FFmpegVideoConvertor", "preferedformat": "mp4"},
        {"key": "FFmpegMetadata"},
    ]
    ydl_opts["postprocessor_args"] = [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
    ]

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            logger.info(f"Video indiriliyor: {url}")
            info = ydl.extract_info(url, download=True)
            downloaded_path = ydl.prepare_filename(info)

        final_path = os.path.join(
            tmpdir, os.path.splitext(os.path.basename(downloaded_path))[0] + ".mp4"
        )
        if not os.path.exists(final_path):
            # bazen zaten mp4 iner; o zaman indirilen dosyayı kullan
            if os.path.exists(downloaded_path):
                final_path = downloaded_path
            else:
                raise FileNotFoundError("Dönüştürülmüş MP4 bulunamadı.")

        final_name = sanitize_filename(info.get("title") or info.get("id") or "video", ext="mp4")
        return final_path, final_name, tmpdir

    except Exception as e:
        _cleanup_dir(tmpdir)
        logger.error(f"İndirme/dönüştürme hatası: {e}")
        raise HTTPException(status_code=500, detail=f"İndirme başarısız: {str(e)}")

# ---- ROUTES ----
@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/get_video")
async def get_video(link_request: LinkRequest):
    url = link_request.url.strip()
    logger.info(f"Gelen istek: {url}")

    if not is_supported(url):
        raise HTTPException(status_code=400, detail="Sadece Twitter/X ve TikTok linkleri destekleniyor.")

    # 1) Önce bilgi çek (download=False). Progressive mp4 varsa direkt stream et (en hızlı yol)
    try:
        probe_opts = _build_ytdlp_opts(skip_download=True, url=url)
        with yt_dlp.YoutubeDL(probe_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                raise ValueError("Video bilgisi alınamadı.")
    except Exception as e:
        logger.error(f"Bilgi çekme hatası: {e}")
        raise HTTPException(status_code=400, detail=f"Video çözümlenemedi: {str(e)}")

    title = info.get("title") or info.get("id") or "video"
    fmt = pick_best_mp4_format(info)

    if fmt and fmt.get("url"):
        logger.info("Progressive MP4 bulundu, doğrudan akış başlatılıyor.")
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
                        r.aiter_bytes(), media_type=ct, headers={"Content-Disposition": disp}
                    )
        except httpx.HTTPError as e:
            logger.warning(f"Doğrudan akış başarısız, indirme+dönüştürmeye düşüyoruz: {e}")

    # 2) Doğrudan akış yoksa indir + mp4’e çevir ve dosya olarak yolla
    logger.info("Doğrudan akış mümkün değil, indir+dönüştür başlıyor.")
    final_path, final_name, tmpdir = await download_and_convert_with_ytdlp(url)
    task = BackgroundTask(_cleanup_dir, tmpdir)
    return FileResponse(path=final_path, media_type="video/mp4", filename=final_name, background=task)

# ---- ENTRYPOINT (lokal geliştirme) ----
if __name__ == "__main__":
    import uvicorn
    logger.info("MRB Video Downloader API başlatılıyor.")
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
