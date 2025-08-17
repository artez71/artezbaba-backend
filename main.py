import os
import tempfile
import shutil
from pathlib import Path

from fastapi import FastAPI, Form, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from yt_dlp import YoutubeDL
import imageio_ffmpeg as ffmpeg  # ffmpeg binary buradan gelecek

# ffmpeg yolunu yt-dlp için ayarlıyoruz
os.environ["PATH"] += os.pathsep + os.path.dirname(ffmpeg.get_ffmpeg_exe())

app = FastAPI()

# CORS ayarı
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # gerekirse burayı domain ile sınırla
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/get_video")
def get_video(url: str = Form(...)):
    try:
        # Geçici klasör
        tmpdir = tempfile.mkdtemp()

        ydl_opts = {
            "outtmpl": f"{tmpdir}/%(title)s.%(ext)s",
            "merge_output_format": "mp4",
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
        raise HTTPException(status_code=400, detail=f"İndirme hatası: {str(e)}")


@app.get("/")
def root():
    return {"message": "Backend ayakta, video indirme için /get_video kullan."}
