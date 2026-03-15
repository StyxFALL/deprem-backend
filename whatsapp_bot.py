import os
import asyncio
from fastapi import APIRouter
import httpx

INSTANCE_ID = os.getenv("GREEN_API_INSTANCE")
API_TOKEN   = os.getenv("GREEN_API_TOKEN")
GREEN_BASE  = f"https://api.green-api.com/waInstance{INSTANCE_ID}"
MIN_MAGNITUDE = float(os.getenv("MIN_MAGNITUDE", "4.0"))

aboneler: dict[str, dict] = {}

router = APIRouter(prefix="/whatsapp", tags=["WhatsApp Bot"])

async def mesaj_gonder(telefon: str, metin: str) -> dict:
    url = f"{GREEN_BASE}/sendMessage/{API_TOKEN}"
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(url, json={
            "chatId": f"{telefon}@c.us",
            "message": metin
        })
    return r.json()

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

async def yanit_uret(telefon: str, metin: str) -> str:
    m = metin.strip().lower()

    if any(k in m for k in ["son deprem", "deprem oldu mu", "deprem var mı", "listele"]):
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get("http://localhost:8000/kandilli")
                quakes = r.json().get("quakes", [])[:5]
            if not quakes:
                return "⚠️ Şu an deprem verisi alınamadı, lütfen tekrar deneyin."
            lines = ["🔍 *Son 5 Deprem (Kandilli)*\n"]
            for q in quakes:
                zaman = q["time"].replace("T", " ")
                lines.append(f"• {q['mag']} {q['magType']} — {q['place']}\n  🕐 {zaman} | 🔻 {q['depth']} km")
            return "\n".join(lines)
        except:
            return "⚠️ Deprem verisi alınamadı. Lütfen daha sonra tekrar deneyin."

    elif any(k in m for k in ["ne yapmalı", "depremde", "güvenlik", "tavsiye"]):
        return (
            "📋 *Depremde Yapılması Gerekenler*\n\n"
            "🏠 *İÇERİDEYSEN:*\n"
            "• Çök-Kapan-Tutun pozisyonu al\n"
            "• Sağlam masanın altına gir\n"
            "• Cam ve dolaplarda uzak dur\n\n"
            "🌳 *DIŞARIDAYSAN:*\n"
            "• Binalardan, direklerden uzaklaş\n"
            "• Açık alana geç\n\n"
            "🚗 *ARAÇTAYSANş*\n"
            "• Köprü/üstgeçitten uzakta dur\n\n"
            "📞 Acil: *112*"
        )

    elif any(k in m for k in ["abone ol", "bildirim al", "uyar beni"]):
        if telefon not in aboneler:
            aboneler[telefon] = {"sehir": None, "min_mag": MIN_MAGNITUDE}
        return (
            f"✅ Deprem bildirimlerine abone oldunuz!\n\n"
            f"📊 Varsayılan eşik: {MIN_MAGNITUDE}+ büyüklük\n\n"
            f"Şehir filtresi için:\n"
            f"*KONUM İstanbul* yaz\n\n"
            f"Eşik değiştirmek için:\n"
            f"*EŞİK 3.5* yaz\n\n"
            f"İptal: *ABONE İPTAL*"
        )

    elif m.startswith("konum "):
        sehir = metin.strip()[6:].strip().title()
        if telefon not in aboneler:
            aboneler[telefon] = {"sehir": sehir, "min_mag": MIN_MAGNITUDE}
        else:
            aboneler[telefon]["sehir"] = sehir
        return f"📍 *{sehir}* için filtre ayarlandı!\nSadece bu bölgedeki depremler bildirilecek."

    elif m.startswith("eşik "):
        try:
            esik = float(metin.strip().split()[1])
            if telefon not in aboneler:
                aboneler[telefon] = {"sehir": None, "min_mag": esik}
            else:
                aboneler[telefon]["min_mag"] = esik
            return f"⚙️ Bildirim eşiği *{esik}* olarak ayarlandı."
        except:
            return "❌ Geçersiz değer. Örnek: *EŞİK 3.5*"

    elif any(k in m for k in ["abone iptal", "iptal", "çık", "dur"]):
        aboneler.pop(telefon, None)
        return "🔕 Aboneliğiniz iptal edildi. Tekrar başlamak için *ABONE OL* yazın."

    else:
        return (
            "👋 *Deprem Bot'a Hoş Geldiniz!*\n\n"
            "📋 Komutlar:\n\n"
            "🔍 *SON DEPREM* — Son 5 depremi gör\n"
            "📋 *NE YAPMALI* — Güvenlik rehberi\n"
            "🔔 *ABONE OL* — Otomatik bildirim al\n"
            "📍 *KONUM [şehir]* — Bölge filtresi\n"
            "⚙️ *EŞİK [sayı]* — Büyüklük eşiği\n"
            "🔕 *ABONE İPTAL* — Bildirimleri durdur\n\n"
            "Veri kaynakları: Kandilli & AFAD"
        )

@router.post("/webhook")
async def webhook(payload: dict):
    body = payload.get("body", {})
    if body.get("type") != "incomingMessageReceived":
        return {"status": "ignored"}
    sender  = body.get("senderData", {}).get("sender", "")
    text    = body.get("messageData", {}).get("textMessageData", {}).get("textMessage", "")
    telefon = sender.replace("@c.us", "")
    if not telefon or not text:
        return {"status": "no_text"}
    yanit = await yanit_uret(telefon, text)
    await mesaj_gonder(telefon, yanit)
    return {"status": "ok"}

son_kontrol_zamani: str = ""

async def deprem_alarmcisi():
    global son_kontrol_zamani
    while True:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get("http://localhost:8000/kandilli")
                quakes = r.json().get("quakes", [])
            if quakes:
                en_yeni = quakes[0]["time"]
                if son_kontrol_zamani and en_yeni > son_kontrol_zamani:
                    yeniler = [q for q in quakes if q["time"] > son_kontrol_zamani]
                    for q in yeniler:
                        mag   = q.get("mag", 0)
                        place = q.get("place", "").lower()
                        metin = bildirim_metni(q)
                        for telefon, ayarlar in aboneler.items():
                            if mag < ayarlar.get("min_mag", MIN_MAGNITUDE):
                                continue
                            sehir = ayarlar.get("sehir")
                            if sehir and sehir.lower() not in place:
                                continue
                            await mesaj_gonder(telefon, metin)
                son_kontrol_zamani = en_yeni
        except Exception as e:
            print(f"[Alarmcı hata]: {e}")
        await asyncio.sleep(60)
