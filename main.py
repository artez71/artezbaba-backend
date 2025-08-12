from fastapi import FastAPI, Query, Body
from fastapi.responses import StreamingResponse, JSONResponse
import yt_dlp
import requests

app = FastAPI()

def download_video_stream(url: str):
    try:
        ydl_opts = {
            "quiet": True,
            "format": "mp4/best",
            "nocheckcertificate": True,
            "noplaylist": True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        if "url" in info:
            video_url = info["url"]
        elif "formats" in info and len(info["formats"]) > 0:
            video_url = info["formats"][-1]["url"]
        else:
            return None, "Video linki alınamadı"

        def iterfile():
            with requests.get(video_url, stream=True) as r:
                for chunk in r.iter_content(chunk_size=1024 * 512):
                    if chunk:
                        yield chunk

        return StreamingResponse(iterfile(), media_type="video/mp4"), None

    except Exception as e:
        return None, str(e)


@app.get("/get_video")
def get_video(url: str = Query(..., description="Video URL")):
    stream, error = download_video_stream(url)
    if error:
        return JSONResponse({"error": error}, status_code=500)
    return stream


@app.post("/get_video")
def post_video(data: dict = Body(...)):
    url = data.get("url")
    if not url:
        return JSONResponse({"error": "URL gerekli"}, status_code=400)
    
    stream, error = download_video_stream(url)
    if error:
        return JSONResponse({"error": error}, status_code=500)
    return stream
