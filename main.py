from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import httpx, re

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])

KANDILLI_URL = "http://www.koeri.boun.edu.tr/scripts/lst6.asp"

def parse(html):
    quakes, seen = [], set()
    pat = re.compile(r'(\d{4}\.\d{2}\.\d{2})\s+(\d{2}:\d{2}:\d{2})\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.-]+)\s+([\d.-]+)\s+([\d.-]+)\s{2,}(.+?)(?:\s{2,}|$)')
    for line in html.splitlines():
        m = pat.match(line.strip())
        if not m: continue
        vals = {"MD": m.group(6), "ML": m.group(7), "Mw": m.group(8)}
        mag, mag_type = None, None
        for t in ["ML", "Mw", "MD"]:
            if vals[t] != "-.-":
                try: mag = float(vals[t]); mag_type = t; break
                except: pass
        if mag is None: continue
        qid = f"{m.group(1)}{m.group(2)}{m.group(3)}"
        if qid in seen: continue
        seen.add(qid)
        quakes.append({
            "id": qid,
            "time": f"{m.group(1).replace('.', '-')}T{m.group(2)}",
            "lat": float(m.group(3)),
            "lon": float(m.group(4)),
            "depth": float(m.group(5)),
            "mag": mag,
            "magType": mag_type,
            "place": m.group(9).strip()
        })
    return sorted(quakes, key=lambda q: q["time"], reverse=True)

@app.get("/depremler")
async def depremler():
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(KANDILLI_URL, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
    data = parse(r.text)
    return {"count": len(data), "quakes": data}

@app.get("/health")
async def health():
    return {"status": "ok"}
