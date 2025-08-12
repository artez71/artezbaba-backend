# main.py
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel
import yt_dlp
import httpx
import re
import os

app = FastAPI(title="MRB Video Downloader API", version="1.0.0")

# --- CORS ---
# Production'da FRONTEND_ORIGIN ortam değişkenine kendi domainini yaz (örn. https://mrbdownloader.com)
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "*")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN] if FRONTEND_ORIGIN != "*" else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Models ---
class LinkRequest(BaseModel):
    url: str

# --- Utils ---
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)

def sanitize_filename(name: str, ext: str = "mp4") -> str:
    name = re.sub(r"[\\/:*?\"<>|]+", " ", name).strip()
    name = re.sub(r"\s+", " ", name)
    if not name:
        name = "video"
    if not name.lower().endswith(f".{ext}"):
        name = f"{name}.{ext}"
    return name


def pick_best_mp4_format(info: dict) -> dict | None:
    """MP4 uzantılı, doğrudan indirilebilir bir format seçmeye çalış.
    Bulunamazsa None döner (çağıran taraf fallback yapar)."""
    formats = info.get("formats") or []
    # Tercih: mp4, progressive (vcodec!=none ve acodec!=none), en yüksek bitrate
    mp4s = [
        f for f in formats
        if (f.get("ext") == "mp4") and f.get("vcodec") != "none" and f.get("acodec") != "none"
    ]
    if mp4s:
        return sorted(mp4s, key=lambda f: (f.get("tbr") or 0), reverse=True)[0]

    # Bazen sadece video+ses ayrı akış olur; yine de mp4 container varsa onu seç.
    mp4_any = [f for f in formats if f.get("ext") == "mp4"]
    if mp4_any:
        return sorted(mp4_any, key=lambda f: (f.get("tbr") or 0), reverse=True)[0]

    return None


async def stream_from_url(url: str, filename: str, content_type: str | None = None):
    headers = {"User-Agent": UA}
    async with httpx.AsyncClient(headers=headers, timeout=None, follow_redirects=True) as client:
        try:
            async with client.stream("GET", url) as r:
                if r.status_code >= 400:
                    raise HTTPException(status_code=400, detail=f"Kaynak indirilemedi: {r.status_code}")

                # İçerik tipi yoksa MP4 varsay (tarayıcı indirme için yeterli)
                ct = content_type or r.headers.get("Content-Type", "video/mp4")
                disp = f"attachment; filename=\"{filename}\""

                return StreamingResponse(
                    r.aiter_bytes(),
                    media_type=ct,
                    headers={
                        "Content-Disposition": disp,
                        # İstersen Content-Length de geçebilirsin; ama chunked için zorunlu değil
                        # "Content-Length": r.headers.get("Content-Length", "")
                    },
                )
        except httpx.HTTPError as e:
            raise HTTPException(status_code=400, detail=f"Ağ hatası: {str(e)}")


# --- Routes ---
@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/get_video")
async def get_video(link_request: LinkRequest):
    url = link_request.url

    ydl_opts = {
        "quiet": True,
        "noplaylist": True,
        "skip_download": True,
        # UA ve cookie ayarları bazı sitelerde gerekebilir; şimdilik UA yeterli
        "http_headers": {"User-Agent": UA},
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        title = info.get("title") or info.get("id") or "video"
        # Öncelik MP4 format
        fmt = pick_best_mp4_format(info)

        if fmt and fmt.get("url"):
            filename = sanitize_filename(title, ext=fmt.get("ext", "mp4"))
            ct = fmt.get("http_headers", {}).get("Content-Type") or None
            return await stream_from_url(fmt["url"], filename=filename, content_type=ct)

        # Fallback: direkt info['url'] (HLS olabilir). Tarayıcı MP4 beklediğinden
        # bu durumda indirme düzgün olmayabilir. Kullanıcıya anlaşılır bir hata ver.
        direct_url = info.get("url")
        if direct_url:
            # İçerik tipi bilinmiyorsa mp4 varsayacağız.
            filename = sanitize_filename(title, ext="mp4")
            return await stream_from_url(direct_url, filename=filename)

        raise HTTPException(status_code=400, detail="Uygun video akışı bulunamadı.")

    except yt_dlp.utils.DownloadError as e:
        # yt-dlp kaynaklı spesifik hata
        raise HTTPException(status_code=400, detail=f"Çözümleme hatası: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# --- Entrypoint (Railway/Heroku gibi) ---
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)))


