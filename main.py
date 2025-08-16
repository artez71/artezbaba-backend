import os
import tempfile
import shutil
from pathlib import Path

from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse
from starlette.background import BackgroundTask

from yt_dlp import YoutubeDL

app = FastAPI(title="DL Site", version="1.0")

# (İstersen tek domain açmak istersen CORS'u daraltabilirsin)
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
  <style>
    :root { font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial; }
    body { display:flex; min-height:100dvh; align-items:center; justify-content:center; background:#0b1220; color:#f3f4f6; margin:0; }
    .card { width:min(680px, 92vw); background:#111827; padding:24px; border-radius:18px; box-shadow: 0 10px 30px rgba(0,0,0,.35); }
    h1 { margin:0 0 16px; font-weight:700; letter-spacing:.2px; }
    p { opacity:.9; margin:0 0 16px; font-size:14px; line-height:1.5; }
    .row { display:flex; gap:10px; margin-top:12px; }
    input[type="url"]{ flex:1; padding:14px 16px; border-radius:12px; border:1px solid #374151; background:#0f172a; color:#e5e7eb; outline:none; }
    button { padding:14px 18px; border-radius:12px; border:0; background:#3b82f6; color:white; font-weight:700; cursor:pointer; }
    button:disabled{opacity:.6; cursor:not-allowed}
    .hint{font-size:12px; opacity:.7}
    .ok{color:#34d399}
    .err{color:#f87171}
    .footer{margin-top:14px; font-size:12px; opacity:.6}
    .badge{display:inline-block; background:#1f2937; padding:4px 8px; border-radius:999px; font-size:11px; margin-right:6px;}
  </style>
</head>
<body>
  <div class="card">
    <h1>Twitter/X & TikTok Video İndir</h1>
    <p>Linki yapıştır → <span class="badge">twitter.com | x.com</span><span class="badge">tiktok.com</span> → <strong>İndir</strong></p>
    <div class="row">
      <input id="url" type="url" placeholder="https://x.com/... veya https://www.tiktok.com/..." />
      <button id="go">İndir</button>
    </div>
    <p class="hint">Twitter için bazen çerez (cookie) gerekir. Gerekirse sunucuya <code>COOKIES_TXT</code> olarak eklemen yeterli.</p>
    <p id="msg" class="hint"></p>
    <div class="footer">© mini dl • yalnızca izinli içerikler için</div>
  </div>
  <script>
    const urlEl = document.getElementById('url');
    const btn = document.getElementById('go');
    const msg = document.getElementById('msg');

    function setMsg(text, ok=false){
      msg.textContent = text || '';
      msg.className = ok ? 'hint ok' : (text ? 'hint err' : 'hint');
    }

    btn.addEventListener('click', () => {
      const u = urlEl.value.trim();
      if(!u){ setMsg('Lütfen bir URL gir.', false); return; }
      setMsg('');
      btn.disabled = true;
      // İndirmeyi direkt GET ile tetikliyoruz ki tarayıcı dosyayı indirsin
      const downloadUrl = '/download?url=' + encodeURIComponent(u);
      // Yeni bir gizli iframe ile indirme (sayfa değişmesin)
      const iframe = document.createElement('iframe');
      iframe.style.display = 'none';
      iframe.src = downloadUrl;
      document.body.appendChild(iframe);
      setTimeout(() => {
        btn.disabled = false;
        setMsg('İndirme başlatıldı. Eğer inmedi ise linkin erişimi engelli olabilir.', true);
        setTimeout(()=>setMsg(''), 6000);
      }, 800);
    });
  </script>
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

    # Opsiyonel: Railway'de COOKIES_TXT env değişkenine Netscape formatında cookie yapıştırırsan
    cookies_txt = os.getenv("COOKIES_TXT", "").strip()
    if cookies_txt:
        cookiefile_path = str(Path(tmpdir) / "cookies.txt")
        with open(cookiefile_path, "w", encoding="utf-8") as f:
            f.write(cookies_txt)

    ydl_opts = {
        "outtmpl": str(Path(tmpdir) / "%(title).200B.%(ext)s"),
        "merge_output_format": "mp4",          # video+audio -> mp4
        "format": "bv*+ba/best",               # en iyi görüntü+ses
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        # Twitter/TikTok sık 403 verirse bunu açabilirsin:
        # "http_headers": {"User-Agent": "Mozilla/5.0"},
    }
    if cookiefile_path:
        ydl_opts["cookiefile"] = cookiefile_path

    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)  # indir
            out_path = ydl.prepare_filename(info)        # gerçek çıktı adı (ext .mp4 olabilir)
            base = os.path.splitext(out_path)[0]
            # merger mp4 üretmiş olabilir
            if not os.path.exists(out_path) and os.path.exists(base + ".mp4"):
                out_path = base + ".mp4"

        if not os.path.exists(out_path):
            raise RuntimeError("İndirme başarısız: çıktı dosyası bulunamadı.")

        filename = os.path.basename(out_path)
        task = BackgroundTask(lambda: _cleanup_dir(tmpdir))
        # İçerik türünü mp4 varsayıyoruz; dl formatı farklıysa tarayıcı yine indirir.
        return FileResponse(
            out_path,
            media_type="video/mp4",
            filename=filename,
            background=task
        )
    except Exception as e:
        _cleanup_dir(tmpdir)
        raise HTTPException(status_code=400, detail=f"İndirme hatası: {str(e)}")
