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

# --- LOGGER AYARLARI ---
# Hataları ve önemli bilgileri konsola yazdırmak için
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# --- FASTAPI UYGULAMASI ---
app = FastAPI(title="MRB Video Downloader API", version="1.4.1")

# --- CORS AYARLARI ---
# Geliştirme için her yerden erişime izin veriyoruz.
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN] if FRONTEND_ORIGIN != "*" else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- KONFİGÜRASYON ---
# yt-dlp için mobil User-Agent
USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 10; SM-G973F) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36"
)
# Cookie kullanımını isteğe bağlı hale getirdik
USE_COOKIES = os.getenv("USE_COOKIES", "0") == "1"
COOKIES_FILE = os.getenv("COOKIES_FILE", None)

# --- URL DOĞRULAMA ---
SUPPORTED_PATTERNS = [
    r"^(https?://)?(www\.)?(twitter\.com|x\.com)/.+",
    r"^(https?://)?(www\.)?tiktok\.com/.+",
    r"^(https?://)?(vm|vt)\.tiktok\.com/.+",
]

def is_supported(url: str) -> bool:
    """Verilen URL'nin desteklenen bir platforma ait olup olmadığını kontrol eder."""
    return any(re.match(p, url, flags=re.IGNORECASE) for p in SUPPORTED_PATTERNS)

# --- MODEL TANIMLARI ---
class LinkRequest(BaseModel):
    url: str

# --- YARDIMCI FONKSİYONLAR ---
def sanitize_filename(name: str, ext: str = "mp4") -> str:
    """Dosya adını ASCII uyumlu ve güvenli hale getirir."""
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    name = re.sub(r"[\\/:*?\"<>|]+", " ", name).strip()
    name = re.sub(r"\s+", " ", name)
    if not name:
        name = "video"
    return f"{name}.{ext}"

def pick_best_mp4_format(info: dict) -> dict | None:
    """Progressive MP4 formatını bulur (HLS/DASH olmayan)."""
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
        # En yüksek bit hızına (tbr) sahip olanı seç
        return sorted(mp4s, key=lambda f: (f.get("tbr") or 0), reverse=True)[0]
    return None

def ffmpeg_available() -> bool:
    """FFmpeg'in sistemde yüklü olup olmadığını kontrol eder."""
    return shutil.which("ffmpeg") is not None

def _cleanup_dir(path: str):
    """Geçici dizini siler."""
    try:
        shutil.rmtree(path, ignore_errors=True)
        logger.info(f"Geçici dizin silindi: {path}")
    except OSError as e:
        logger.error(f"Geçici dizin silinirken hata oluştu: {e}")

def _build_ytdlp_opts(skip_download: bool, url: str) -> dict:
    """yt-dlp için seçenekleri oluşturur."""
    opts = {
        # quiet=True'yi kapattık, hata ayıklama için önemli
        "quiet": False,
        "noplaylist": True,
        "http_headers": {"User-Agent": USER_AGENT},
        "skip_download": skip_download,
        # Dosya adı için başlık bilgisini kullanır
        "outtmpl": os.path.join(tempfile.gettempdir(), "%(title).200B.%(ext)s"),
        "progress_hooks": [lambda d: logger.info(d.get("status", ""))]
    }
    if USE_COOKIES and COOKIES_FILE:
        if not os.path.exists(COOKIES_FILE):
            logger.warning(f"Cookies dosyası bulunamadı: {COOKIES_FILE}")
        else:
            opts["cookies"] = COOKIES_FILE

    # Twitter için bestvideo+bestaudio formatını zorunlu kılar
    if "twitter.com" in url or "x.com" in url:
        opts["format"] = "bestvideo*+bestaudio/best"
        opts["postprocessors"] = [
            # FFmpeg ile dönüştürme ve birleştirme yapması için
            {"key": "FFmpegVideoConvertor", "preferedformat": "mp4"},
            {"key": "FFmpegMetadata"},
        ]
    
    return opts

async def download_and_convert_with_ytdlp(url: str) -> tuple[str, str, str]:
    """Videoyu indirir, MP4'e dönüştürür ve dosya yolunu döndürür."""
    if not ffmpeg_available():
        raise HTTPException(
            status_code=500,
            detail="FFmpeg sistemi üzerinde bulunamadı. Lütfen kurun ve PATH'e ekleyin."
        )

    tmpdir = tempfile.mkdtemp(prefix="mrb_")
    outtmpl = os.path.join(tmpdir, "%(title).200B.%(ext)s")
    
    ydl_opts = _build_ytdlp_opts(skip_download=False, url=url)
    ydl_opts["outtmpl"] = outtmpl
    
    # Tüm videoları h.264/aac mp4 formatına dönüştürmek için postprocessor ekliyoruz.
    # Bu, en geniş uyumluluğu sağlar.
    ydl_opts["postprocessors"] = ydl_opts.get("postprocessors", []) + [
        {"key": "FFmpegVideoConvertor", "preferedformat": "mp4"},
        {"key": "FFmpegMetadata"},
    ]
    ydl_opts["postprocessor_args"] = [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart"
    ]

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            logger.info(f"Video indiriliyor ve dönüştürülüyor: {url}")
            info = ydl.extract_info(url, download=True)
            downloaded_path = ydl.prepare_filename(info)

        final_path = os.path.join(tmpdir, os.path.splitext(os.path.basename(downloaded_path))[0] + ".mp4")
        if not os.path.exists(final_path):
            raise FileNotFoundError("Dönüştürülmüş MP4 dosyası bulunamadı.")
            
        final_name = sanitize_filename(info.get("title") or info.get("id") or "video", ext="mp4")
        return final_path, final_name, tmpdir

    except Exception as e:
        _cleanup_dir(tmpdir)
        logger.error(f"İndirme veya dönüştürme hatası: {e}")
        raise HTTPException(status_code=500, detail=f"İndirme işlemi başarısız: {str(e)}")

# --- YOL TANIMLARI (ROUTES) ---
@app.get("/health")
async def health():
    """Uygulamanın çalışıp çalışmadığını kontrol eder."""
    return {"status": "ok"}

@app.post("/get_video")
async def get_video(link_request: LinkRequest):
    """Verilen URL'deki videoyu indirir ve geri döndürür."""
    url = link_request.url.strip()
    logger.info(f"Gelen istek: {url}")

    if not is_supported(url):
        raise HTTPException(
            status_code=400,
            detail="Sadece Twitter/X ve TikTok linkleri destekleniyor."
        )

    try:
        # 1. Aşama: Bilgileri çek
        probe_opts = _build_ytdlp_opts(skip_download=True, url=url)
        with yt_dlp.YoutubeDL(probe_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                raise ValueError("Video bilgileri çekilemedi.")
    except Exception as e:
        logger.error(f"Video bilgileri çekilirken hata: {e}")
        raise HTTPException(status_code=400, detail=f"Video çözümlenemedi: {str(e)}")

    title = info.get("title") or info.get("id") or "video"
    fmt = pick_best_mp4_format(info)

    # 2. Aşama: Eğer progressive MP4 varsa, doğrudan proxy et
    if fmt and fmt.get("url"):
        logger.info("Progressive MP4 formatı bulundu, doğrudan akış başlatılıyor.")
        filename = sanitize_filename(title, ext=fmt.get("ext", "mp4"))
        try:
            async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=None) as client:
                async with client.stream("GET", fmt["url"], follow_redirects=True) as r:
                    r.raise_for_status()
                    ct = r.headers.get("Content-Type", "video/mp4")
                    disp = f'attachment; filename="{filename}"'
                    return StreamingResponse(
                        r.aiter_bytes(),
                        media_type=ct,
                        headers={"Content-Disposition": disp}
                    )
        except httpx.HTTPError as e:
            logger.error(f"Doğrudan akış hatası: {e}")
            pass # Proxy başarısız olursa ikinci aşamaya geç

    # 3. Aşama: Progressive MP4 yoksa, indir, dönüştür ve dosyayı gönder
    logger.info("Doğrudan akış mümkün değil, video indirilecek ve dönüştürülecek.")
    final_path, final_name, tmpdir = await download_and_convert_with_ytdlp(url)
    
    # Dosya indikten sonra geçici dizini silmek için arka plan görevi
    task = BackgroundTask(_cleanup_dir, tmpdir)
    return FileResponse(
        path=final_path,
        media_type="video/mp4",
        filename=final_name,
        background=task
    )

# --- UYGULAMA BAŞLANGICI ---
if __name__ == "__main__":
    import uvicorn
    # Uygulama başlamadan önce FFmpeg kontrolü
    if not ffmpeg_available():
        logger.error(
            "HATA: FFmpeg sistemi üzerinde bulunamadı. "
            "Lütfen FFmpeg'i kurun ve PATH değişkeninize ekleyin."
        )
        exit(1)
        
    logger.info("MRB Video Downloader API başlatılıyor.")
    port = int(os.getenv("PORT", 8000))
    # --reload parametresi geliştirme aşamasında önemlidir
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
