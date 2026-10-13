"""Thu Nar Sin Yar TTS - our own Burmese voiceover website.
Paste story text -> Thiha/Nilar (Azure neural voices via Edge TTS) -> MP3.
Long texts are split at sentence boundaries, synthesized per chunk, then
merged into one MP3 with ffmpeg.
"""
import os, re, subprocess, tempfile, uuid
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
import edge_tts

VOICES = {
    "thiha": "my-MM-ThihaNeural",   # male
    "nilar": "my-MM-NilarNeural",   # female
}
SITE_PASSWORD = os.environ.get("SITE_PASSWORD", "")
MAX_CHUNK = 1200

app = FastAPI(title="Thu Nar Sin Yar TTS")

def split_text(text: str, max_len: int = MAX_CHUNK):
    """Split Burmese text into chunks at sentence ends (။) / line breaks."""
    text = text.strip()
    if len(text) <= max_len:
        return [text]
    parts = re.split(r"(?<=[။.!?])\s*|\n+", text)
    chunks, cur = [], ""
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if len(cur) + len(p) + 1 <= max_len:
            cur = (cur + " " + p).strip()
        else:
            if cur:
                chunks.append(cur)
            while len(p) > max_len:  # hard-split an over-long sentence
                chunks.append(p[:max_len])
                p = p[max_len:]
            cur = p
    if cur:
        chunks.append(cur)
    return chunks

async def synth_chunk(text: str, voice: str, rate: str, path: str):
    await edge_tts.Communicate(text, voice, rate=rate).save(path)

def merge_mp3s(paths, out_path):
    if len(paths) == 1:
        os.replace(paths[0], out_path)
        return
    lst = out_path + ".txt"
    with open(lst, "w") as f:
        for p in paths:
            f.write(f"file '{p}'\n")
    r = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
         "-i", lst, "-c:a", "libmp3lame", "-q:a", "3", out_path],
        capture_output=True, text=True)
    os.remove(lst)
    if r.returncode != 0:  # fallback: raw concat (same encoder params)
        with open(out_path, "wb") as out:
            for p in paths:
                with open(p, "rb") as fh:
                    out.write(fh.read())

@app.get("/", response_class=HTMLResponse)
async def index():
    base = os.path.dirname(__file__)
    for cand in ("index.html", os.path.join("static", "index.html")):
        p = os.path.join(base, cand)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return f.read()
    raise HTTPException(500, "index.html missing")

@app.get("/api/health")
async def health():
    return {"ok": True}

@app.post("/api/tts")
async def tts(req: Request):
    body = await req.json()
    if SITE_PASSWORD and body.get("password") != SITE_PASSWORD:
        raise HTTPException(403, "wrong password")
    text = (body.get("text") or "").strip()
    voice_key = body.get("voice", "thiha")
    rate_pct = int(body.get("rate", 0))
    if not text:
        raise HTTPException(400, "no text")
    if len(text) > 60000:
        raise HTTPException(400, "text too long (max 60000 chars per request)")
    voice = VOICES.get(voice_key, VOICES["thiha"])
    rate = f"{rate_pct:+d}%"
    chunks = split_text(text)
    tmp = tempfile.mkdtemp(prefix="tts_")
    paths = []
    try:
        for i, ch in enumerate(chunks):
            p = os.path.join(tmp, f"part_{i:03d}.mp3")
            await synth_chunk(ch, voice, rate, p)
            paths.append(p)
        out = os.path.join(tmp, "voiceover.mp3")
        merge_mp3s(paths, out)
        fname = f"voiceover_{voice_key}_{uuid.uuid4().hex[:6]}.mp3"
        return FileResponse(out, media_type="audio/mpeg", filename=fname)
    except Exception as e:
        raise HTTPException(502, f"synthesis failed: {e}")
