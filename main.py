from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timedelta
import httpx

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])

@app.get("/depremler")
async def depremler():
    end = datetime.utcnow()
    start = end - timedelta(days=7)
    url = (
        "https://earthquake.usgs.gov/fdsnws/event/1/query"
        f"?format=geojson&orderby=time&limit=500"
        f"&starttime={start.strftime('%Y-%m-%dT%H:%M:%S')}"
        f"&minlatitude=36&maxlatitude=42"
        f"&minlongitude=26&maxlongitude=45"
    )
    last_error = None
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                r = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
                r.raise_for_status()
                features = r.json().get("features", [])
                quakes = []
                for f in features:
                    p = f["properties"]
                    c = f["geometry"]["coordinates"]
                    quakes.append({
                        "id":      f["id"],
                        "time":    datetime.utcfromtimestamp(p["time"] / 1000).strftime("%Y-%m-%dT%H:%M:%S"),
                        "lat":     c[1],
                        "lon":     c[0],
                        "depth":   c[2],
                        "mag":     p.get("mag") or 0,
                        "magType": p.get("magType") or "",
                        "place":   p.get("place") or "",
                    })
                return {"count": len(quakes), "quakes": quakes}
        except Exception as e:
            last_error = e
    raise last_error

@app.get("/health")
async def health():
    return {"status": "ok"}
