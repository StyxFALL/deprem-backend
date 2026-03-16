from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timedelta
import httpx, re, asyncio, os
from collections import defaultdict

# ── AYARLAR ───────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_API   = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_URL     = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent?key={GEMINI_API_KEY}"
RENDER_URL     = "https://deprem-backend-kvmk.onrender.com"

SISTEM_PROMPT = """Sen bir deprem ve doğal afet uzmanısın. Türkiye'nin jeolojik yapısını, 
fay hatlarını ve deprem tarihini çok iyi biliyorsun. Kullanıcılara:
- Deprem bilimi hakkında bilimsel ama anlaşılır açıklamalar yaparsın
- Türkiye'deki fay hatları ve riskli bölgeler hakkında bilgi verirsin
- Deprem hazırlığı ve güvenlik konularında pratik tavsiyeler verirsin
- Panik yaratmadan, sakin ve güven verici bir dille konuşursun
- Yanıtların kısa ve öz olsun, Telegram mesajına uygun
Sadece deprem, afet, jeoloji ve güvenlik konularında yardımcı ol. 
Konu dışı sorularda kibarca konuya yönlendir."""

aboneler: dict[int, dict] = {}
bolge_gecmis: dict[str, list] = defaultdict(list)

# ── TELEGRAM ──────────────────────────────────────────────────────────────────
async def telegram_gonder(chat_id: int, metin: str):
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(f"{TELEGRAM_API}/sendMessage", json={
            "chat_id": chat_id,
            "text": metin,
            "parse_mode": "Markdown"
        })

async def telegram_herkese_gonder(metin: str):
    for chat_id in list(aboneler.keys()):
        await telegram_gonder(chat_id, metin)

# ── GEMİNİ ────────────────────────────────────────────────────────────────────
async def gemini_sor(soru: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(GEMINI_URL, json={
                "contents": [
                    {"role": "user", "parts": [{"text": SISTEM_PROMPT}]},
                    {"role": "model", "parts": [{"text": "Anlaşıldı, deprem uzmanı olarak yardımcı olacağım."}]},
                    {"role": "user", "parts": [{"text": soru}]}
                ]
            })
        data = r.json()
        print(f"[Gemini yanit]: {data}")
        candidates = data.get("candidates", [])
        if not candidates:
            blocked = data.get("promptFeedback", {}).get("blockReason", "")
            print(f"[Gemini engel]: {blocked}")
            return "Bu soruya yanıt veremiyorum. Depremle ilgili başka bir soru sorabilirsiniz."
        candidate = candidates[0]
        parts = candidate.get("content", {}).get("parts", [])
        metin = " ".join(p.get("text", "") for p in parts if "text" in p)
        return metin or "Yanıt alınamadı."
    except Exception as e:
        print(f"[Gemini hata]: {e}")
        return "Şu an yanıt veremiyorum, lütfen tekrar deneyin."

# ── BİLDİRİM METİNLERİ ───────────────────────────────────────────────────────
def bildirim_metni(q: dict) -> str:
    zaman = q.get("time", "").replace("T", " ")
    return (
        f"🚨 *DEPREM BİLDİRİMİ*\n\n"
        f"📍 Yer: {q.get('place', 'Bilinmiyor')}\n"
        f"💥 Büyüklük: {q.get('mag', '?')} {q.get('magType', '')}\n"
        f"🕐 Zaman: {zaman}\n"
        f"🔻 Derinlik: {q.get('depth', '?')} km\n\n"
        f"⚠️ Güvende olun, panik yapmayın.\n"
        f"📞 Acil: 112"
    )

def kume_uyari_metni(bolge: str, depremler: list) -> str:
    return (
        f"⚠️ *KÜME UYARISI — {bolge.upper()}*\n\n"
        f"Son 1 saatte bu bölgede *{len(depremler)} küçük deprem* tespit edildi.\n\n"
        f"Bu tür kümelenmeler bazen daha büyük bir depremin habercisi olabilir.\n"
        f"Dikkatli olun, acil çantanızı hazırlayın.\n\n"
        f"📞 Acil: 112"
    )

# ── KOMUT İŞLEYİCİ ───────────────────────────────────────────────────────────
async def yanit_uret(chat_id: int, metin: str):
    m = metin.strip().lower()

    if m in ["/start", "/yardim", "yardım", "merhaba"]:
        yanit = (
            "👋 *Deprem Bot'a Hoş Geldiniz!*\n\n"
            "📋 Komutlar:\n\n"
            "🔍 /sondeprem — Son 10 depremi gör\n"
            "🔔 /abone — Otomatik bildirim al\n"
            "📍 /konum İstanbul — Şehir filtresi\n"
            "⚙️ /esik 3.5 — Büyüklük eşiği ayarla\n"
            "🔕 /iptal — Bildirimleri durdur\n"
            "📋 /nehyapmali — Deprem güvenlik rehberi\n\n"
            "💬 Veya deprem hakkında herhangi bir soru sorabilirsiniz!"
        )

    elif m == "/sondeprem":
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(f"{RENDER_URL}/kandilli")
                quakes = r.json().get("quakes", [])[:10]
            lines = ["🔍 *SON 10 DEPREM*\n_Kaynak: Kandilli Rasathanesi_\n"]
            for q in quakes:
                mag = q["mag"]
                if mag >= 4.0:
                    renk = "🔴"
                elif mag >= 3.0:
                    renk = "🟡"
                else:
                    renk = "🟢"
                zaman = q["time"].replace("T", " ")
                tarih = zaman[5:16]
                lines.append(
                    f"━━━━━━━━━━━━━━━\n"
                    f"{renk} *{mag} {q['magType']}* — {q['place']}\n"
                    f"📅 {tarih}  |  🔻 {q['depth']} km"
                )
            lines.append("━━━━━━━━━━━━━━━")
            yanit = "\n".join(lines)
        except:
            yanit = "⚠️ Deprem verisi alınamadı, lütfen tekrar deneyin."

    elif m == "/abone":
        if chat_id not in aboneler:
            aboneler[chat_id] = {"sehir": None, "min_mag": 3.5}
        yanit = (
            "✅ *Abone oldunuz!*\n\n"
            "📊 Varsayılan eşik: 3.5+ büyüklük\n\n"
            "Şehir filtresi: /konum İstanbul\n"
            "Eşik değiştir: /esik 4.0\n"
            "İptal: /iptal"
        )

    elif m.startswith("/konum "):
        sehir = metin.strip()[7:].strip().title()
        if chat_id not in aboneler:
            aboneler[chat_id] = {"sehir": sehir, "min_mag": 3.5}
        else:
            aboneler[chat_id]["sehir"] = sehir
        yanit = f"📍 *{sehir}* için filtre ayarlandı!\nSadece bu bölgedeki depremler bildirilecek."

    elif m.startswith("/esik "):
        try:
            esik = float(metin.strip().split()[1])
            if chat_id not in aboneler:
                aboneler[chat_id] = {"sehir": None, "min_mag": esik}
            else:
                aboneler[chat_id]["min_mag"] = esik
            yanit = f"⚙️ Bildirim eşiği *{esik}* olarak ayarlandı."
        except:
            yanit = "❌ Geçersiz değer. Örnek: /esik 3.5"

    elif m == "/iptal":
        aboneler.pop(chat_id, None)
        yanit = "🔕 Aboneliğiniz iptal edildi."

    elif m == "/nehyapmali":
        yanit = (
            "📋 *Depremde Yapılması Gerekenler*\n\n"
            "🏠 *İÇERİDEYSEN:*\n"
            "• Çök-Kapan-Tutun pozisyonu al\n"
            "• Sağlam masanın altına gir\n"
            "• Camlardan uzak dur\n\n"
            "🌳 *DIŞARIDAYSAN:*\n"
            "• Binalardan, direklerden uzaklaş\n"
            "• Açık alana geç\n\n"
            "🚗 *ARAÇTAYSANş*\n"
            "• Köprü/üstgeçitten uzakta dur\n\n"
            "📞 Acil: *112*"
        )

    else:
        await telegram_gonder(chat_id, "🤔 Araştırıyorum...")
        yanit = await gemini_sor(metin)

    await telegram_gonder(chat_id, yanit)

# ── TELEGRAM POLLING ──────────────────────────────────────────────────────────
son_update_id = 0

async def telegram_dinle():
    global son_update_id
    while True:
        try:
            async with httpx.AsyncClient(timeout=35) as client:
                r = await client.get(f"{TELEGRAM_API}/getUpdates", params={
                    "offset": son_update_id + 1,
                    "timeout": 30
                })
                updates = r.json().get("result", [])
                for update in updates:
                    son_update_id = update["update_id"]
                    msg = update.get("message", {})
                    chat_id = msg.get("chat", {}).get("id")
                    text = msg.get("text", "")
                    if chat_id and text:
                        await yanit_uret(chat_id, text)
        except Exception as e:
            print(f"[Telegram hata]: {e}")
            await asyncio.sleep(5)

# ── UYKU ÖNLEYİCİ ────────────────────────────────────────────────────────────
async def uyku_onleyici():
    while True:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.get(f"{RENDER_URL}/health")
        except:
            pass
        await asyncio.sleep(600)

# ── DEPREM ALARMCISI ──────────────────────────────────────────────────────────
son_kontrol_zamani = ""

async def deprem_alarmcisi():
    global son_kontrol_zamani
    while True:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(f"{RENDER_URL}/kandilli")
                quakes = r.json().get("quakes", [])

            if quakes:
                en_yeni = quakes[0]["time"]
                if son_kontrol_zamani and en_yeni > son_kontrol_zamani:
                    yeniler = [q for q in quakes if q["time"] > son_kontrol_zamani]
                    for q in yeniler:
                        mag   = q.get("mag", 0)
                        place = q.get("place", "")
                        bolge_gecmis[place].append({"time": q["time"], "mag": mag})
                        bir_saat_once = (datetime.utcnow() - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
                        bolge_gecmis[place] = [
                            d for d in bolge_gecmis[place] if d["time"] > bir_saat_once
                        ]
                        kucukler = [d for d in bolge_gecmis[place] if d["mag"] < 3.5]
                        if len(kucukler) == 5:
                            await telegram_herkese_gonder(kume_uyari_metni(place, kucukler))
                        metin = bildirim_metni(q)
                        for chat_id, ayarlar in list(aboneler.items()):
                            if mag < ayarlar.get("min_mag", 3.5):
                                continue
                            sehir = ayarlar.get("sehir")
                            if sehir and sehir.lower() not in place.lower():
                                continue
                            await telegram_gonder(chat_id, metin)
                son_kontrol_zamani = en_yeni
        except Exception as e:
            print(f"[Alarmcı hata]: {e}")
        await asyncio.sleep(60)

# ── FASTAPI ───────────────────────────────────────────────────────────────────
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])

@app.on_event("startup")
async def startup():
    asyncio.create_task(telegram_dinle())
    asyncio.create_task(deprem_alarmcisi())
    asyncio.create_task(uyku_onleyici())

# ── KANDİLLİ ─────────────────────────────────────────────────────────────────
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

# ── AFAD ──────────────────────────────────────────────────────────────────────
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
    live = []
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(f"{BASE}/live", headers={"User-Agent": "Mozilla/5.0"})
            j = r.json()
            if j.get("status"):
                live = [q for q in (normalize_afad(x) for x in j.get("result", [])) if q]
    except: pass
    archive = []
    try:
        params = {"date": start.strftime("%Y-%m-%d"), "date_end": end.strftime("%Y-%m-%d"), "limit": 2000}
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(f"{BASE}/archive", params=params, headers={"User-Agent": "Mozilla/5.0"})
            j = r.json()
            if j.get("status"):
                archive = [q for q in (normalize_afad(x) for x in j.get("result", [])) if q]
    except: pass
    seen = set()
    merged = []
    for q in live + archive:
        if q["id"] and q["id"] not in seen:
            seen.add(q["id"])
            merged.append(q)
    return sorted(merged, key=lambda q: q["time"], reverse=True)

# ── ENDPOINTLEr ───────────────────────────────────────────────────────────────
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
