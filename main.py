from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timedelta
import httpx, re

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])

# ── KANDİLLİ (direkt parse — çalışan versiyon) ───────────────────────────────
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
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        for url in [
            "http://www.koeri.boun.edu.tr/scripts/lst6.asp",
            "http://www.koeri.boun.edu.tr/scripts/lst5.asp",
        ]:
            try:
                r = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
                if r.status_code == 200:
                    data = parse_kandilli(r.text)
                    if data: return data
            except: continue
    return []

# ── AFAD (orhanaydogdu API) ───────────────────────────────────────────────────
def normalize_afad(q):
    try:
        mag = float(q.get("mag") or 0)
        coords = q.get("geojson", {}).get("coordinates", [0, 0])
        lat = float(q.get("lat") or (coords[1] if len(coords) > 1 else 0))
        lon = float(q.get("lon") or (coords[0] if len(coords) > 0 else 0))
        return {
            "id":      str(q.get("earthquake_id") or ""),
            "time":    q.get("date_time") or "",
            "lat":     lat,
            "lon":     lon,
            "depth":   float(q.get("depth") or 0),
            "mag":     mag,
            "magType": "ML",
            "place":   q.get("title") or "",
        }
    except:
        return None

async def fetch_afad():
    BASE = "https://api.orhanaydogdu.com.tr/deprem/afad"
    end = datetime.utcnow()
    start = end - timedelta(days=30)

    # Önce live çek
    live = []
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(f"{BASE}/live", headers={"User-Agent": "Mozilla/5.0"})
            j = r.json()
            if j.get("status"):
                live = [q for q in (normalize_afad(x) for x in j.get("result", [])) if q]
    except: pass

    # Sonra archive çek
    archive = []
    try:
        params = {
            "date": start.strftime("%Y-%m-%d"),
            "date_end": end.strftime("%Y-%m-%d"),
            "limit": 2000
        }
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(f"{BASE}/archive", params=params, headers={"User-Agent": "Mozilla/5.0"})
            j = r.json()
            if j.get("status"):
                archive = [q for q in (normalize_afad(x) for x in j.get("result", [])) if q]
    except: pass

    # Birleştir, tekrarları kaldır
    seen = set()
    merged = []
    for q in live + archive:
        if q["id"] and q["id"] not in seen:
            seen.add(q["id"])
            merged.append(q)
    return sorted(merged, key=lambda q: q["time"], reverse=True)

# ── ENDPOINTLEr ──────────────────────────────────────────────────────────────
@app.get("/kandilli")
async def kandilli():
    data = await fetch_kandilli()
    return {"count": len(data), "source": "Kandilli", "quakes": data}

@app.get("/afad")
async def afad():
    try:
        data = await fetch_afad()
        return {"count": len(data), "source": "AFAD", "quakes": data}
    except Exception as e:
        return {"count": 0, "source": "AFAD", "quakes": [], "error": str(e)}

@app.get("/depremler")
async def depremler():
    data = await fetch_kandilli()
    return {"count": len(data), "source": "Kandilli", "quakes": data}

@app.get("/health")
async def health():
    return {"status": "ok"}
