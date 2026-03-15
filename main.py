from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timedelta
import httpx, re

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])

# ── ORHAN AYDOĞDU API (Kandilli + AFAD) ────────────────────────────────────
BASE = "https://api.orhanaydogdu.com.tr/deprem"

def normalize(q, provider):
    try:
        mag = float(q.get("mag") or q.get("magnitude") or 0)
        lat = float(q.get("lat") or q.get("latitude") or q.get("geojson", {}).get("coordinates", [0,0])[1] or 0)
        lon = float(q.get("lon") or q.get("longitude") or q.get("geojson", {}).get("coordinates", [0,0])[0] or 0)
        return {
            "id":      str(q.get("earthquake_id") or q.get("id", "")),
            "time":    q.get("date_time") or q.get("date") or "",
            "lat":     lat,
            "lon":     lon,
            "depth":   float(q.get("depth") or 0),
            "mag":     mag,
            "magType": "ML",
            "place":   q.get("title") or q.get("location") or "",
        }
    except:
        return None

async def fetch_orhanaydogdu(provider="kandilli", days=30):
    end = datetime.utcnow()
    start = end - timedelta(days=days)
    url = f"{BASE}/{provider}/archive"
    params = {
        "date": start.strftime("%Y-%m-%d"),
        "date_end": end.strftime("%Y-%m-%d"),
        "limit": 2000
    }
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
    j = r.json()
    if not j.get("status"):
        raise Exception(j.get("serverMessage", "API hatası"))
    items = j.get("result", [])
    quakes = [normalize(q, provider) for q in items]
    quakes = [q for q in quakes if q]
    return sorted(quakes, key=lambda q: q["time"], reverse=True)

# ── KANDİLLİ YEDEK (parse) ──────────────────────────────────────────────────
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
            "id": qid,
            "time": f"{m.group(1).replace('.', '-')}T{m.group(2)}",
            "lat": float(m.group(3)), "lon": float(m.group(4)),
            "depth": float(m.group(5)), "mag": mag,
            "magType": mag_type, "place": m.group(9).strip(),
        })
    return sorted(quakes, key=lambda q: q["time"], reverse=True)

async def fetch_kandilli_raw():
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        for url in ["http://www.koeri.boun.edu.tr/scripts/lst6.asp",
                    "http://www.koeri.boun.edu.tr/scripts/lst5.asp"]:
            try:
                r = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
                if r.status_code == 200:
                    data = parse_kandilli(r.text)
                    if data: return data
            except: continue
    return []

# ── ENDPOINTLEr ──────────────────────────────────────────────────────────────
@app.get("/kandilli")
async def kandilli():
    try:
        data = await fetch_orhanaydogdu("kandilli", 30)
        if data: return {"count": len(data), "source": "Kandilli", "quakes": data}
    except: pass
    # Yedek: direkt parse
    data = await fetch_kandilli_raw()
    return {"count": len(data), "source": "Kandilli", "quakes": data}

@app.get("/afad")
async def afad():
    try:
        data = await fetch_orhanaydogdu("afad", 30)
        return {"count": len(data), "source": "AFAD", "quakes": data}
    except Exception as e:
        return {"count": 0, "source": "AFAD", "quakes": [], "error": str(e)}

@app.get("/depremler")
async def depremler():
    try:
        data = await fetch_orhanaydogdu("kandilli", 30)
        if data: return {"count": len(data), "source": "Kandilli", "quakes": data}
    except: pass
    data = await fetch_kandilli_raw()
    return {"count": len(data), "source": "Kandilli", "quakes": data}

@app.get("/health")
async def health():
    return {"status": "ok"}
