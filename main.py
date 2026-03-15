from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timedelta
import httpx, re

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])

def parse_kandilli(html):
    quakes, seen = [], set()
    pat = re.compile(
        r'(\d{4}\.\d{2}\.\d{2})\s+(\d{2}:\d{2}:\d{2})\s+'
        r'([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+'
        r'([\d.-]+)\s+([\d.-]+)\s+([\d.-]+)\s{2,}(.+?)(?:\s{2,}|$)'
    )
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
            "id":      qid,
            "time":    f"{m.group(1).replace('.', '-')}T{m.group(2)}",
            "lat":     float(m.group(3)),
            "lon":     float(m.group(4)),
            "depth":   float(m.group(5)),
            "mag":     mag,
            "magType": mag_type,
            "place":   m.group(9).strip(),
        })
    return sorted(quakes, key=lambda q: q["time"], reverse=True)

async def fetch_kandilli():
    urls = [
        "http://www.koeri.boun.edu.tr/scripts/lst6.asp",
        "http://www.koeri.boun.edu.tr/scripts/lst5.asp",
        "http://www.koeri.boun.edu.tr/scripts/lst4.asp",
    ]
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        for url in urls:
            try:
                r = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
                if r.status_code == 200:
                    data = parse_kandilli(r.text)
                    if data: return data
            except: continue
    return None

async def fetch_usgs(days=30):
    end = datetime.utcnow()
    start = end - timedelta(days=days)
    # USGS max 20000 limit, sayfalayarak çek
    all_quakes = []
    offset = 1
    while True:
        url = (
            "https://earthquake.usgs.gov/fdsnws/event/1/query"
            f"?format=geojson&orderby=time&limit=2000&offset={offset}"
            f"&starttime={start.strftime('%Y-%m-%dT%H:%M:%S')}"
            f"&endtime={end.strftime('%Y-%m-%dT%H:%M:%S')}"
            f"&minlatitude=36&maxlatitude=42"
            f"&minlongitude=26&maxlongitude=45"
        )
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
        features = r.json().get("features", [])
        for f in features:
            p = f["properties"]
            c = f["geometry"]["coordinates"]
            all_quakes.append({
                "id":      f["id"],
                "time":    datetime.utcfromtimestamp(p["time"] / 1000).strftime("%Y-%m-%dT%H:%M:%S"),
                "lat":     c[1], "lon": c[0], "depth": c[2],
                "mag":     p.get("mag") or 0,
                "magType": p.get("magType") or "",
                "place":   p.get("place") or "",
            })
        if len(features) < 2000: break
        offset += 2000
    return all_quakes

@app.get("/depremler")
async def depremler():
    data = await fetch_kandilli()
    source = "Kandilli"
    if not data:
        data = await fetch_usgs(30)
        source = "USGS"
    return {"count": len(data), "source": source, "quakes": data}

@app.get("/health")
async def health():
    return {"status": "ok"}
