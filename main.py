from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timedelta
import httpx, json

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])

AFAD_URL = "https://deprem.afad.gov.tr/EventData/GetEventsByFilter"

@app.get("/depremler")
async def depremler():
    end = datetime.utcnow()
    start = end - timedelta(days=3)
    payload = {
        "EventSearchFilterList": [
            {"FilterType": 8, "Value": start.strftime("%Y-%m-%dT%H:%M:%S")},
            {"FilterType": 9, "Value": end.strftime("%Y-%m-%dT%H:%M:%S")},
        ],
        "Skip": 0,
        "Take": 500,
        "SortDescriptor": {"field": "eventDate", "dir": "desc"}
    }
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(AFAD_URL, json=payload,
            headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
    raw = r.json()
    quakes = []
    for q in (raw if isinstance(raw, list) else raw.get("eventList", [])):
        quakes.append({
            "id":      str(q.get("eventID") or q.get("id", "")),
            "time":    q.get("eventDate") or q.get("date", ""),
            "lat":     float(q.get("latitude") or q.get("lat", 0)),
            "lon":     float(q.get("longitude") or q.get("lon", 0)),
            "depth":   float(q.get("depth", 0)),
            "mag":     float(q.get("magnitude") or q.get("ml") or q.get("mag", 0)),
            "magType": q.get("magnitudeType", "ML"),
            "place":   q.get("location") or q.get("place", ""),
        })
    return {"count": len(quakes), "quakes": quakes}

@app.get("/health")
async def health():
    return {"status": "ok"}
