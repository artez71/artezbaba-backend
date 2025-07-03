from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import yt_dlp
import requests
from fastapi.responses import StreamingResponse

app = FastAPI()

# CORS açık
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class LinkRequest(BaseModel):
    url: str

@app.post("/get_video")
def get_video(link_request: LinkRequest):
    url = link_request.url
    ydl_opts = {'quiet': True, 'skip_download': True}

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
            if 'url' in info:
                video_url = info['url']
            else:
                formats = info.get('formats')
                if formats:
                    video_url = formats[-1]['url']
                else:
                    raise HTTPException(status_code=400, detail="Video URL not found")

            video = requests.get(video_url, stream=True)
            return StreamingResponse(video.iter_content(1024), media_type="video/mp4")

        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))
