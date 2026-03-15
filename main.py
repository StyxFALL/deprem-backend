from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timedelta
import httpx, re

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])

# ── KANDİLLİ ──────────────────────────────────────────────────────────────────
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

async def get_kandilli():
    urls = [
        "http://www.koeri.boun.edu.tr/scripts/lst6.asp",
        "http://www.koeri.boun.edu.tr/scripts/lst5.asp",
    ]
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        for url in urls:
            try:
                r = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
                if r.status_code == 200:
                    data = parse_kandilli(r.text)
                    if data: return data
            except: continue
    return []

# ── AFAD ──────────────────────────────────────────────────────────────────────
async def get_afad(days=30):
    end = datetime.utcnow()
    start = end - timedelta(days=days)
    payload = {
        "EventSearchFilterList": [
            {"FilterType": 8, "Value": start.strftime("%Y-%m-%dT%H:%M:%S")},
            {"FilterType": 9, "Value": end.strftime("%Y-%m-%dT%H:%M:%S")},
        ],
        "Skip": 0, "Take": 10000,
        "SortDescriptor": {"field": "eventDate", "dir": "desc"}
    }
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            "https://deprem.afad.gov.tr/EventData/GetEventsByFilter",
            json=payload,
            headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
        )
        r.raise_for_status()
    raw = r.json()
    items = raw if isinstance(raw, list) else raw.get("eventList", raw.get("result", []))
    if not items: return []
    quakes = []
    for q in items:
        try:
            mag = float(q.get("magnitude") or q.get("ml") or q.get("mag") or 0)
            quakes.append({
                "id":      str(q.get("eventID") or q.get("id", "")),
                "time":    (q.get("eventDate") or q.get("date", "")).replace("Z", ""),
                "lat":     float(q.get("latitude") or q.get("lat", 0)),
                "lon":     float(q.get("longitude") or q.get("lon", 0)),
                "depth":   float(q.get("depth", 0)),
                "mag":     mag,
                "magType": q.get("magnitudeType", "ML"),
                "place":   q.get("location") or q.get("place", ""),
            })
        except: pass
    return sorted(quakes, key=lambda q: q["time"], reverse=True)

# ── ENDPOINTLEr ───────────────────────────────────────────────────────────────
@app.get("/kandilli")
async def kandilli():
    data = await get_kandilli()
    return {"count": len(data), "source": "Kandilli", "quakes": data}

@app.get("/afad")
async def afad():
    try:
        data = await get_afad(30)
        return {"count": len(data), "source": "AFAD", "quakes": data}
    except Exception as e:
        return {"count": 0, "source": "AFAD", "quakes": [], "error": str(e)}

@app.get("/depremler")
async def depremler():
    # Geriye dönük uyumluluk — önce AFAD, sonra Kandilli
    try:
        data = await get_afad(30)
        if data: return {"count": len(data), "source": "AFAD", "quakes": data}
    except: pass
    data = await get_kandilli()
    return {"count": len(data), "source": "Kandilli", "quakes": data}

@app.get("/health")
async def health():
    return {"status": "ok"}
