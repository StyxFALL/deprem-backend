from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timedelta
import httpx, re, asyncio, os
from collections import defaultdict, Counter
import asyncpg

# ── AYARLAR ───────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_API   = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_URL     = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent?key={GEMINI_API_KEY}"
RENDER_URL     = "https://deprem-backend-kvmk.onrender.com"
DATABASE_URL   = os.getenv("DATABASE_URL")

SISTEM_PROMPT = """Sen bir deprem ve doğal afet uzmanısın. Türkiye'nin jeolojik yapısını,
fay hatlarını ve deprem tarihini çok iyi biliyorsun. Kullanıcılara:
- Deprem bilimi hakkında bilimsel ama anlaşılır açıklamalar yaparsın
- Türkiye'deki fay hatları ve riskli bölgeler hakkında bilgi verirsin
- Deprem hazırlığı ve güvenlik konularında pratik tavsiyeler verirsin
- Panik yaratmadan, sakin ve güven verici bir dille konuşursun
- Yanıtların kısa ve öz olsun, Telegram mesajına uygun
Sadece deprem, afet, jeoloji ve güvenlik konularında yardımcı ol.
Konu dışı sorularda kibarca konuya yönlendir."""

db_pool = None
bolge_gecmis: dict[str, list] = defaultdict(list)
artci_takip: dict[str, dict] = {}
son_kontrol_zamani_kandilli = ""
son_kontrol_zamani_afad = ""

# ── VERİTABANI ────────────────────────────────────────────────────────────────
async def db_baglat():
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL)
    await db_pool.execute("""
        CREATE TABLE IF NOT EXISTS aboneler (
            chat_id BIGINT PRIMARY KEY,
            sehir TEXT,
            min_mag FLOAT DEFAULT 3.5
        )
    """)

async def abone_ekle(chat_id: int, sehir=None, min_mag=3.5):
    await db_pool.execute("""
        INSERT INTO aboneler (chat_id, sehir, min_mag)
        VALUES ($1, $2, $3)
        ON CONFLICT (chat_id) DO NOTHING
    """, chat_id, sehir, min_mag)

async def abone_guncelle_sehir(chat_id: int, sehir: str):
    await db_pool.execute("""
        INSERT INTO aboneler (chat_id, sehir, min_mag)
        VALUES ($1, $2, 3.5)
        ON CONFLICT (chat_id) DO UPDATE SET sehir = $2
    """, chat_id, sehir)

async def abone_guncelle_esik(chat_id: int, esik: float):
    await db_pool.execute("""
        INSERT INTO aboneler (chat_id, sehir, min_mag)
        VALUES ($1, NULL, $2)
        ON CONFLICT (chat_id) DO UPDATE SET min_mag = $2
    """, chat_id, esik)

async def abone_sil(chat_id: int):
    await db_pool.execute("DELETE FROM aboneler WHERE chat_id = $1", chat_id)

async def abone_listesi():
    rows = await db_pool.fetch("SELECT chat_id, sehir, min_mag FROM aboneler")
    return [{"chat_id": r["chat_id"], "sehir": r["sehir"], "min_mag": r["min_mag"]} for r in rows]

# ── TELEGRAM ──────────────────────────────────────────────────────────────────
async def telegram_gonder(chat_id: int, metin: str):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(f"{TELEGRAM_API}/sendMessage", json={
                "chat_id": chat_id,
                "text": metin,
                "parse_mode": "Markdown"
            })
    except Exception as e:
        print(f"[Telegram gönderim hata]: {e}")

async def telegram_herkese_gonder(metin: str):
    aboneler = await abone_listesi()
    for a in aboneler:
        await telegram_gonder(a["chat_id"], metin)

# ── GEMİNİ ────────────────────────────────────────────────────────────────────
async def gemini_sor(soru: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=25) as client:
            r = await client.post(GEMINI_URL, json={
                "contents": [
                    {"role": "user", "parts": [{"text": SISTEM_PROMPT}]},
                    {"role": "model", "parts": [{"text": "Anlaşıldı, deprem uzmanı olarak yardımcı olacağım."}]},
                    {"role": "user", "parts": [{"text": soru}]}
                ]
            })
        data = r.json()
        candidates = data.get("candidates", [])
        if not candidates:
            print(f"[Gemini engel]: {data}")
            return "Bu soruya yanıt veremiyorum. Depremle ilgili başka bir soru sorabilirsiniz."
        parts = candidates[0].get("content", {}).get("parts", [])
        metin = " ".join(p.get("text", "") for p in parts if "text" in p)
        metin = metin.replace("**", "*").replace("##", "").replace("###", "")
        return metin.strip() or "Yanıt alınamadı."
    except Exception as e:
        print(f"[Gemini hata]: {e}")
        return "Şu an yanıt veremiyorum, lütfen tekrar deneyin."

# ── BİLDİRİM METİNLERİ ───────────────────────────────────────────────────────
def bildirim_metni(q: dict, kaynak: str = "Kandilli") -> str:
    zaman = q.get("time", "").replace("T", " ")
    simge = "🔵" if kaynak == "Kandilli" else "🟠"
    return (
        f"🚨 *DEPREM BİLDİRİMİ*\n"
        f"{simge} _Kaynak: {kaynak}_\n\n"
        f"📍 Yer: {q.get('place', 'Bilinmiyor')}\n"
        f"💥 Büyüklük: {q.get('mag', '?')} {q.get('magType', '')}\n"
        f"🕐 Zaman: {zaman}\n"
        f"🔻 Derinlik: {q.get('depth', '?')} km\n\n"
        f"⚠️ Güvende olun, panik yapmayın.\n"
        f"📞 Acil: 112"
    )

def buyuk_deprem_metni(q: dict, kaynak: str = "Kandilli") -> str:
    simge = "🔵" if kaynak == "Kandilli" else "🟠"
    return (
        f"🆘🆘🆘 *BÜYÜK DEPREM* 🆘🆘🆘\n"
        f"{simge} _Kaynak: {kaynak}_\n\n"
        f"📍 Yer: {q.get('place', 'Bilinmiyor')}\n"
        f"💥 Büyüklük: *{q.get('mag', '?')} {q.get('magType', '')}*\n"
        f"🕐 Zaman: {q.get('time', '').replace('T', ' ')}\n"
        f"🔻 Derinlik: {q.get('depth', '?')} km\n\n"
        f"⚠️ HEMEN GÜVENLİ ALANA GEÇİN!\n"
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
            "🔍 /sondeprem — Son 10 deprem + uzman yorumu\n"
            "📊 /analiz — Son 24 saatin deprem analizi\n"
            "📈 /istatistik — Deprem istatistikleri\n"
            "🌍 /dunya — Dünya geneli büyük depremler\n"
            "🗺 /risk — Türkiye deprem risk haritası\n"
            "🎒 /canta — Deprem çantası rehberi\n"
            "📍 /toplanma İstanbul — Toplanma alanları\n"
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
            if not quakes:
                yanit = "⚠️ Deprem verisi alınamadı, lütfen tekrar deneyin."
            else:
                lines = ["🔍 *SON 10 DEPREM*\n_Kaynak: Kandilli Rasathanesi_\n"]
                for q in quakes:
                    mag = q["mag"]
                    renk = "🔴" if mag >= 4.0 else "🟡" if mag >= 3.0 else "🟢"
                    tarih = q["time"].replace("T", " ")[5:16]
                    lines.append(
                        f"━━━━━━━━━━━━━━━\n"
                        f"{renk} *{mag} {q['magType']}* — {q['place']}\n"
                        f"📅 {tarih}  |  🔻 {q['depth']} km"
                    )
                lines.append("━━━━━━━━━━━━━━━")
                await telegram_gonder(chat_id, "\n".join(lines))
                await telegram_gonder(chat_id, "🤔 Uzman yorumu hazırlanıyor...")
                deprem_listesi = "\n".join([
                    f"- {q['mag']} {q['magType']}, {q['place']}, derinlik {q['depth']} km, zaman {q['time']}"
                    for q in quakes
                ])
                yorum = await gemini_sor(
                    f"Aşağıdaki son depremleri bir deprem uzmanı olarak kısaca yorumla. "
                    f"Dikkat çeken bir durum var mı, hangi bölgeler aktif, risk var mı? "
                    f"Kısa ve anlaşılır yaz:\n\n{deprem_listesi}"
                )
                yanit = f"🤖 *Uzman Yorumu:*\n\n{yorum}"
        except Exception as e:
            print(f"[sondeprem hata]: {e}")
            yanit = "⚠️ Deprem verisi alınamadı, lütfen tekrar deneyin."

    elif m == "/analiz":
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(f"{RENDER_URL}/kandilli")
                quakes = r.json().get("quakes", [])
            yirmi_dort_saat = (datetime.utcnow() - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S")
            son_24 = [q for q in quakes if q["time"] > yirmi_dort_saat]
            if not son_24:
                yanit = "⚠️ Son 24 saatte kayıt bulunamadı."
            else:
                await telegram_gonder(chat_id, f"🔎 Son 24 saatte *{len(son_24)} deprem* tespit edildi. Analiz yapılıyor...")
                # En büyük 30 depremi gönder, listeyi kısa tut
                analiz_listesi = sorted(son_24, key=lambda q: q["mag"], reverse=True)[:30]
                deprem_listesi = "\n".join([
                    f"- {q['mag']} {q['magType']}, {q['place']}, derinlik {q['depth']} km, zaman {q['time']}"
                    for q in analiz_listesi
                ])
                yorum = await gemini_sor(
                    f"Aşağıdaki son 24 saatteki {len(son_24)} depremi bir deprem uzmanı olarak analiz et. "
                    f"En aktif bölgeler hangileri? Kümelenme var mı? Risk değerlendirmesi yap. "
                    f"Kısa, net ve anlaşılır yaz:\n\n{deprem_listesi}"
                )
                yanit = f"📊 *24 Saat Deprem Analizi*\n\n{yorum}"
        except Exception as e:
            print(f"[analiz hata]: {e}")
            yanit = "⚠️ Analiz yapılamadı, lütfen tekrar deneyin."

    elif m == "/istatistik":
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(f"{RENDER_URL}/kandilli")
                quakes = r.json().get("quakes", [])
            yirmi_dort_saat = (datetime.utcnow() - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S")
            yedi_gun = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")
            son_24 = [q for q in quakes if q["time"] > yirmi_dort_saat]
            son_7  = [q for q in quakes if q["time"] > yedi_gun]
            bolgeler = Counter(q["place"].split("(")[-1].replace(")", "").strip() for q in son_7)
            en_aktif = bolgeler.most_common(3)
            en_buyuk = max(son_7, key=lambda q: q["mag"]) if son_7 else None
            lines = [
                "📊 *DEPREM İSTATİSTİKLERİ*\n",
                f"🕐 Son 24 saat: *{len(son_24)} deprem*",
                f"📅 Son 7 gün: *{len(son_7)} deprem*",
            ]
            if en_buyuk:
                lines.append(f"💥 Bu haftanın en büyüğü: *{en_buyuk['mag']} {en_buyuk['magType']}* — {en_buyuk['place']}")
            lines.append("\n🗺 *En Aktif Bölgeler (7 gün):*")
            for bolge, sayi in en_aktif:
                lines.append(f"• {bolge}: {sayi} deprem")
            yanit = "\n".join(lines)
        except Exception as e:
            print(f"[istatistik hata]: {e}")
            yanit = "⚠️ İstatistik verisi alınamadı."

    elif m == "/dunya":
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(
                    "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_day.geojson"
                )
                data = r.json()
                quakes = data.get("features", [])[:8]
            lines = ["🌍 *DÜNYA GENELİ SON BÜYÜK DEPREMLER*\n_Kaynak: USGS (4.5+)_\n"]
            for q in quakes:
                props = q.get("properties", {})
                mag   = props.get("mag", "?")
                yer   = props.get("place", "Bilinmiyor")
                zaman = datetime.utcfromtimestamp(props.get("time", 0) / 1000).strftime("%m-%d %H:%M")
                renk  = "🔴" if mag >= 6.0 else "🟠" if mag >= 5.0 else "🟡"
                lines.append(f"━━━━━━━━━━━━━━━\n{renk} *{mag} Mw* — {yer}\n📅 {zaman} UTC")
            lines.append("━━━━━━━━━━━━━━━")
            yanit = "\n".join(lines)
        except Exception as e:
            print(f"[dunya hata]: {e}")
            yanit = "⚠️ Dünya deprem verisi alınamadı, lütfen tekrar deneyin."

    elif m == "/risk":
        await telegram_gonder(chat_id, "🗺 Risk haritası hazırlanıyor...")
        yorum = await gemini_sor(
            "Türkiye'nin deprem risk haritasını açıkla. En riskli bölgeler hangileri, "
            "hangi fay hatları üzerindeler, geçmişte büyük depremler nerede oldu? "
            "Bölgeleri risk seviyesine göre sırala. Kısa ve anlaşılır yaz."
        )
        yanit = f"🗺 *TÜRKİYE DEPREM RİSK HARİTASI*\n\n{yorum}"

    elif m == "/canta":
        yanit = (
            "🎒 *DEPREM ÇANTASI HAZIRLIK REHBERİ*\n\n"
            "💧 *Su ve Gıda:*\n"
            "• 3 günlük su (kişi başı 3L/gün)\n"
            "• Konserve ve uzun ömürlü gıdalar\n"
            "• Bebek maması (gerekiyorsa)\n\n"
            "🏥 *Sağlık:*\n"
            "• İlk yardım kiti\n"
            "• Düzenli kullanılan ilaçlar (7 günlük)\n"
            "• Maske ve eldiven\n\n"
            "📄 *Belgeler:*\n"
            "• Kimlik fotokopisi\n"
            "• Sigorta belgeleri\n"
            "• Acil iletişim listesi\n\n"
            "🔦 *Ekipman:*\n"
            "• El feneri + yedek pil\n"
            "• Düdük\n"
            "• Battaniye\n"
            "• Powerbank\n"
            "• Nakit para\n\n"
            "⚠️ Çantanızı her 6 ayda bir kontrol edin!"
        )

    elif m.startswith("/toplanma "):
        sehir = metin.strip().split(" ", 1)[1].strip().title()
        await telegram_gonder(chat_id, f"📍 {sehir} için toplanma alanları araştırılıyor...")
        yorum = await gemini_sor(
            f"{sehir} ilinde deprem toplanma alanları nelerdir? "
            f"Bilinen toplanma noktalarını, parkları ve açık alanları listele. "
            f"AFAD tarafından belirlenen resmi toplanma alanları varsa belirt. Kısa ve net yaz."
        )
        yanit = f"📍 *{sehir.upper()} TOPLANMA ALANLARI*\n\n{yorum}"

    elif m == "/abone":
        await abone_ekle(chat_id)
        yanit = (
            "✅ *Abone oldunuz!*\n\n"
            "📊 Varsayılan eşik: 3.5+ büyüklük\n\n"
            "Şehir filtresi: /konum İstanbul\n"
            "Eşik değiştir: /esik 4.0\n"
            "İptal: /iptal"
        )

    elif m.startswith("/konum "):
        sehir = metin.strip().split(" ", 1)[1].strip().title()
        await abone_guncelle_sehir(chat_id, sehir)
        yanit = f"📍 *{sehir}* için filtre ayarlandı!\nSadece bu bölgedeki depremler bildirilecek."

    elif m.startswith("/esik "):
        try:
            esik = float(metin.strip().split()[1])
            await abone_guncelle_esik(chat_id, esik)
            yanit = f"⚙️ Bildirim eşiği *{esik}* olarak ayarlandı."
        except:
            yanit = "❌ Geçersiz değer. Örnek: /esik 3.5"

    elif m == "/iptal":
        await abone_sil(chat_id)
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

# ── SABAH ÖZETİ ───────────────────────────────────────────────────────────────
async def sabah_ozeti():
    while True:
        now = datetime.utcnow()
        hedef = now.replace(hour=5, minute=0, second=0, microsecond=0)
        if now >= hedef:
            hedef += timedelta(days=1)
        await asyncio.sleep((hedef - now).total_seconds())
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(f"{RENDER_URL}/kandilli")
                quakes = r.json().get("quakes", [])
            yirmi_dort_saat = (datetime.utcnow() - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S")
            son_24 = [q for q in quakes if q["time"] > yirmi_dort_saat]
            if not son_24:
                continue
            en_buyuk = max(son_24, key=lambda q: q["mag"])
            deprem_listesi = "\n".join([
                f"- {q['mag']} {q['magType']}, {q['place']}, derinlik {q['depth']} km"
                for q in son_24
            ])
            yorum = await gemini_sor(
                f"Dün Türkiye'de {len(son_24)} deprem oldu. "
                f"Sabah özeti olarak kısa bir değerlendirme yap:\n\n{deprem_listesi}"
            )
            mesaj = (
                f"🌅 *GÜNLÜK DEPREM ÖZETİ*\n"
                f"_{datetime.utcnow().strftime('%d.%m.%Y')}_\n\n"
                f"📊 Son 24 saatte: *{len(son_24)} deprem*\n"
                f"💥 En büyük: *{en_buyuk['mag']} {en_buyuk['magType']}* — {en_buyuk['place']}\n\n"
                f"🤖 *Uzman Yorumu:*\n{yorum}"
            )
            await telegram_herkese_gonder(mesaj)
        except Exception as e:
            print(f"[Sabah özeti hata]: {e}")

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
async def kaynak_kontrol(quakes: list, kaynak: str, son_zaman: str) -> str:
    """Verilen kaynaktaki yeni depremleri abonelere bildirir, yeni son_zaman döner."""
    if not quakes:
        return son_zaman

    en_yeni = quakes[0]["time"]

    if son_zaman and en_yeni > son_zaman:
        yeniler = [q for q in quakes if q["time"] > son_zaman]
        aboneler = await abone_listesi()

        for q in yeniler:
            mag   = q.get("mag", 0)
            place = q.get("place", "")

            # Küme tespiti (sadece Kandilli için)
            if kaynak == "Kandilli":
                bolge_gecmis[place].append({"time": q["time"], "mag": mag})
                bir_saat_once = (datetime.utcnow() - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
                bolge_gecmis[place] = [d for d in bolge_gecmis[place] if d["time"] > bir_saat_once]
                kucukler = [d for d in bolge_gecmis[place] if d["mag"] < 3.5]
                if len(kucukler) == 5:
                    await telegram_herkese_gonder(kume_uyari_metni(place, kucukler))

            # Büyük deprem / artçı takibi
            if mag >= 5.0:
                artci_takip[q["id"]] = {"ana_mag": mag, "place": place, "time": q["time"], "artcilar": []}
                bildirim = buyuk_deprem_metni(q, kaynak)
            else:
                for ana_id, ana in list(artci_takip.items()):
                    if place.lower() in ana["place"].lower() or ana["place"].lower() in place.lower():
                        ana["artcilar"].append(q)
                        if len(ana["artcilar"]) in [3, 5, 10]:
                            await telegram_herkese_gonder(
                                f"🔄 *ARTÇI DEPREM UYARISI*\n\n"
                                f"📍 {ana['place']} bölgesinde {ana['ana_mag']} büyüklüğündeki depremin ardından\n"
                                f"*{len(ana['artcilar'])} artçı deprem* kaydedildi.\n\n"
                                f"Son artçı: {mag} {q.get('magType', '')} — {q['time'].replace('T', ' ')}\n\n"
                                f"⚠️ Dikkatli olmaya devam edin."
                            )
                bildirim = bildirim_metni(q, kaynak)

            # Abonelere gönder
            for a in aboneler:
                if mag < a.get("min_mag", 3.5):
                    continue
                sehir = a.get("sehir")
                if sehir and sehir.lower() not in place.lower():
                    continue
                await telegram_gonder(a["chat_id"], bildirim)

    return en_yeni

async def deprem_alarmcisi():
    global son_kontrol_zamani_kandilli, son_kontrol_zamani_afad
    while True:
        try:
            # Eski artçı kayıtlarını temizle
            yirmi_dort_saat = (datetime.utcnow() - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S")
            for ana_id in list(artci_takip.keys()):
                if artci_takip[ana_id]["time"] < yirmi_dort_saat:
                    del artci_takip[ana_id]

            async with httpx.AsyncClient(timeout=15) as client:
                # Kandilli
                try:
                    r = await client.get(f"{RENDER_URL}/kandilli")
                    kandilli_quakes = r.json().get("quakes", [])
                    son_kontrol_zamani_kandilli = await kaynak_kontrol(
                        kandilli_quakes, "Kandilli", son_kontrol_zamani_kandilli
                    )
                except Exception as e:
                    print(f"[Kandilli kontrol hata]: {e}")

                # AFAD
                try:
                    r = await client.get(f"{RENDER_URL}/afad")
                    afad_quakes = r.json().get("quakes", [])
                    son_kontrol_zamani_afad = await kaynak_kontrol(
                        afad_quakes, "AFAD", son_kontrol_zamani_afad
                    )
                except Exception as e:
                    print(f"[AFAD kontrol hata]: {e}")

        except Exception as e:
            print(f"[Alarmcı hata]: {e}")
        await asyncio.sleep(60)

# ── FASTAPI ───────────────────────────────────────────────────────────────────
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])

@app.on_event("startup")
async def startup():
    await db_baglat()
    asyncio.create_task(telegram_dinle())
    asyncio.create_task(deprem_alarmcisi())
    asyncio.create_task(uyku_onleyici())
    asyncio.create_task(sabah_ozeti())

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
