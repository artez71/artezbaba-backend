import os
import tempfile
import shutil
from pathlib import Path

from fastapi import FastAPI, Query, HTTPException, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse
from starlette.background import BackgroundTask

from yt_dlp import YoutubeDL

app = FastAPI(title="DL Site", version="1.0")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

INDEX_HTML = """
<!doctype html>
<html lang="tr">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Twitter/TikTok Video İndir</title>
</head>
<body>
  <h1>Twitter & TikTok Video İndirici</h1>
  <form action="/get_video" method="post">
    <input type="url" name="url" placeholder="https://x.com/... veya https://www.tiktok.com/..." required />
    <button type="submit">İndir</button>
  </form>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
def home():
    return HTMLResponse(INDEX_HTML)

def _cleanup_dir(path: str):
    shutil.rmtree(path, ignore_errors=True)

@app.get("/download")
def download(url: str = Query(..., description="Twitter/X veya TikTok video URL")):
    tmpdir = tempfile.mkdtemp(prefix="dl-")
    cookiefile_path = None

    cookies_txt = os.getenv("COOKIES_TXT", "").strip()
    if cookies_txt:
        cookiefile_path = str(Path(tmpdir) / "cookies.txt")
        with open(cookiefile_path, "w", encoding="utf-8") as f:
            f.write(cookies_txt)

    ydl_opts = {
        "outtmpl": str(Path(tmpdir) / "%(title).200B.%(ext)s"),
        "merge_output_format": "mp4",
        "format": "bv*+ba/best",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    }
    if cookiefile_path:
        ydl_opts["cookiefile"] = cookiefile_path

    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            out_path = ydl.prepare_filename(info)
            base = os.path.splitext(out_path)[0]
            if not os.path.exists(out_path) and os.path.exists(base + ".mp4"):
                out_path = base + ".mp4"

        if not os.path.exists(out_path):
            raise RuntimeError("İndirme başarısız: çıktı dosyası bulunamadı.")

        filename = os.path.basename(out_path)
        task = BackgroundTask(lambda: _cleanup_dir(tmpdir))
        return FileResponse(
            out_path,
            media_type="video/mp4",
            filename=filename,
            background=task
        )
    except Exception as e:
        _cleanup_dir(tmpdir)
        raise HTTPException(status_code=400, detail=f"İndirme hatası: {str(e)}")

# --- Yeni eklenen endpoint ---
@app.post("/get_video")
def get_video(url: str = Form(...)):
    return download(url)
