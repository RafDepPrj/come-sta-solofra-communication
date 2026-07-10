#!/usr/bin/env python3
"""
Come Sta Solofra — bollettino sulla qualità dell'aria con immagine.

- Scarica i dati della stazione "Solofra Zona Industriale" (ID 10922).
- Aggiunge il confronto con i comuni vicini (ricerca dinamica per nome).
- Genera una card grafica (PNG) con il semaforo AQI.
- La pubblica su un canale Telegram tramite la Bot API ufficiale (sendPhoto).

Fonte dati: World Air Quality Index Project (aqicn.org) — ARPA Campania / EEA.

Variabili d'ambiente richieste (GitHub Secrets):
  WAQI_TOKEN, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
"""

import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from PIL import Image, ImageDraw, ImageFont

# --- Configurazione ---------------------------------------------------------

STATION_ID = "@10922"  # Solofra Zona Industriale
FEED_URL = "https://api.waqi.info/feed/{}/"
SEARCH_URL = "https://api.waqi.info/search/"

# Comuni vicini per dare contesto. Vengono cercati per nome; se un comune
# non ha una centralina compare "n/d". Puoi aggiungere/togliere voci qui.
NEARBY = ["Serino", "Avellino", "Atripalda", "Montoro",
          "Mercato San Severino", "Salerno"]

WAQI_TOKEN = os.environ.get("WAQI_TOKEN")
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID")

POLLUTANTS = {
    "pm25": "PM2.5", "pm10": "PM10", "no2": "NO\u2082",
    "so2": "SO\u2082", "co": "CO", "o3": "O\u2083",
}

GIORNI = ["lunedì", "martedì", "mercoledì", "giovedì",
          "venerdì", "sabato", "domenica"]
MESI = ["", "gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
        "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"]


def check_env():
    for name, val in [("WAQI_TOKEN", WAQI_TOKEN),
                      ("TELEGRAM_BOT_TOKEN", TG_TOKEN),
                      ("TELEGRAM_CHAT_ID", TG_CHAT)]:
        if not val:
            sys.exit(f"Errore: variabile d'ambiente mancante: {name}")


def to_int(value):
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def banda(aqi):
    """Info di fascia per un valore AQI (standard US EPA).

    Ritorna un dict con: emoji, label, advice, color (RGB), text (RGB del
    testo dentro il cerchio).
    """
    if aqi is None:
        return {"emoji": "\u26aa", "label": "NON DISPONIBILE",
                "advice": "Dato non disponibile in questo momento.",
                "color": (150, 150, 150), "text": (255, 255, 255)}
    if aqi <= 50:
        return {"emoji": "\U0001F7E2", "label": "BUONA",
                "advice": "Aria buona. Attività all'aperto senza problemi "
                          "per tutti.",
                "color": (46, 158, 79), "text": (255, 255, 255)}
    if aqi <= 100:
        return {"emoji": "\U0001F7E1", "label": "ACCETTABILE",
                "advice": "Aria accettabile. Le persone molto sensibili "
                          "valutino di ridurre sforzi intensi e prolungati "
                          "all'aperto.",
                "color": (224, 168, 0), "text": (40, 40, 40)}
    if aqi <= 150:
        return {"emoji": "\U0001F7E0", "label": "INSALUBRE PER I SENSIBILI",
                "advice": "Bambini, anziani e chi ha problemi respiratori "
                          "riducano gli sforzi prolungati all'aperto.",
                "color": (233, 140, 20), "text": (255, 255, 255)}
    if aqi <= 200:
        return {"emoji": "\U0001F534", "label": "INSALUBRE",
                "advice": "Tutti dovrebbero limitare gli sforzi prolungati "
                          "all'aperto; i gruppi sensibili evitarli.",
                "color": (214, 64, 54), "text": (255, 255, 255)}
    if aqi <= 300:
        return {"emoji": "\U0001F7E3", "label": "MOLTO INSALUBRE",
                "advice": "Evitare le attività all'aperto. I gruppi "
                          "sensibili restino in casa.",
                "color": (142, 68, 173), "text": (255, 255, 255)}
    return {"emoji": "\U0001F7E4", "label": "PERICOLOSA",
            "advice": "Allerta sanitaria: evitare di uscire e tenere chiuse "
                      "le finestre.",
            "color": (126, 0, 35), "text": (255, 255, 255)}


# --- Recupero dati ----------------------------------------------------------

def fetch_solofra():
    resp = requests.get(FEED_URL.format(STATION_ID),
                        params={"token": WAQI_TOKEN}, timeout=30)
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("status") != "ok":
        sys.exit(f"Errore API WAQI: {payload.get('data')}")
    return payload["data"]


def cerca_aqi(keyword):
    """AQI del comune vicino via ricerca per nome; None se non trovato."""
    try:
        resp = requests.get(SEARCH_URL,
                            params={"token": WAQI_TOKEN, "keyword": keyword},
                            timeout=30)
        resp.raise_for_status()
        js = resp.json()
        if js.get("status") != "ok":
            return None
        primo = keyword.split()[0].lower()
        ripiego = None
        for item in js.get("data", []):
            nome = (item.get("station") or {}).get("name", "")
            aqi = to_int(item.get("aqi"))
            if aqi is None:
                continue
            if primo in nome.lower():   # match sul nome del comune
                return aqi
            if ripiego is None:
                ripiego = aqi
        return ripiego
    except requests.RequestException:
        return None


def fetch_nearby():
    return [{"nome": c, "aqi": cerca_aqi(c)} for c in NEARBY]


# --- Testo -----------------------------------------------------------------

def etichette_tempo():
    ora = datetime.now(ZoneInfo("Europe/Rome"))
    data_it = f"{GIORNI[ora.weekday()]} {ora.day} {MESI[ora.month]}"
    return data_it, ora.strftime("%H:%M")


def build_caption(data, nearby):
    aqi = to_int(data.get("aqi"))
    b = banda(aqi)
    data_it, ora = etichette_tempo()
    aqi_txt = str(aqi) if aqi is not None else "n/d"

    vicini = " \u00b7 ".join(
        f"{banda(v['aqi'])['emoji']} {v['nome']} "
        f"{v['aqi'] if v['aqi'] is not None else 'n/d'}"
        for v in nearby
    )

    return (
        f"{b['emoji']} <b>Come sta Solofra</b> \u2014 {data_it}, ore {ora}\n\n"
        f"<b>Qualità dell'aria: {b['label']}</b> (AQI {aqi_txt})\n"
        f"{b['advice']}\n\n"
        f"<b>Comuni vicini:</b> {vicini}\n\n"
        f"<i>Dati: World Air Quality Index Project \u00b7 ARPA Campania. "
        f"Dati grezzi non validati, a solo scopo informativo.</i>"
    )


# --- Immagine ---------------------------------------------------------------

def _font(size, bold=False):
    nome = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    for base in ("/usr/share/fonts/truetype/dejavu/", ""):
        try:
            return ImageFont.truetype(base + nome, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _wrap(draw, text, font, max_w):
    righe, cur = [], ""
    for parola in text.split():
        prova = (cur + " " + parola).strip()
        if draw.textlength(prova, font=font) <= max_w:
            cur = prova
        else:
            if cur:
                righe.append(cur)
            cur = parola
    if cur:
        righe.append(cur)
    return righe


def crea_immagine(data, nearby, out_path="card.png"):
    W = 1080
    BG = (245, 247, 250)
    GREY = (110, 118, 128)
    DARK = (35, 40, 46)

    aqi = to_int(data.get("aqi"))
    b = banda(aqi)
    data_it, ora = etichette_tempo()
    dom = data.get("dominentpol") or ""
    dom_label = POLLUTANTS.get(dom, dom.upper() if dom else "n/d")

    # Canvas alto, poi ritagliato all'altezza usata.
    img = Image.new("RGB", (W, 1700), BG)
    d = ImageDraw.Draw(img)

    # Barra colore in alto
    d.rectangle([0, 0, W, 14], fill=b["color"])

    # Titolo
    d.text((60, 60), "COME STA SOLOFRA", font=_font(56, bold=True), fill=DARK)
    d.text((60, 138), f"Aggiornato {data_it}, ore {ora}",
           font=_font(30), fill=GREY)

    # Cerchio semaforo
    cx, cy, r = W // 2, 400, 150
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=b["color"])
    aqi_str = str(aqi) if aqi is not None else "--"
    num_size = 150 if len(aqi_str) <= 2 else 118
    fnum = _font(num_size, bold=True)
    tb = d.textbbox((0, 0), aqi_str, font=fnum)
    d.text((cx - (tb[2] - tb[0]) / 2, cy - (tb[3] - tb[1]) / 2 - tb[1]),
           aqi_str, font=fnum, fill=b["text"])
    d.text((cx, cy + r - 34), "AQI", font=_font(26, bold=True),
           fill=b["text"], anchor="mm")

    # Etichetta fascia
    y = cy + r + 40
    flab = _font(48, bold=True)
    d.text((cx, y), b["label"], font=flab, fill=b["color"], anchor="ma")
    y += 70
    d.text((cx, y), f"Inquinante principale: {dom_label}",
           font=_font(30), fill=GREY, anchor="ma")
    y += 66

    # Consiglio
    for riga in _wrap(d, b["advice"], _font(34), W - 180):
        d.text((cx, y), riga, font=_font(34), fill=DARK, anchor="ma")
        y += 46
    y += 24

    # Divisore
    d.line([60, y, W - 60, y], fill=(225, 229, 234), width=2)
    y += 34

    # Comuni vicini
    d.text((60, y), "COMUNI VICINI", font=_font(34, bold=True), fill=DARK)
    y += 64
    for v in nearby:
        vb = banda(v["aqi"])
        d.ellipse([64, y + 6, 92, y + 34], fill=vb["color"])
        d.text((116, y), v["nome"], font=_font(34), fill=DARK)
        val = str(v["aqi"]) if v["aqi"] is not None else "n/d"
        d.text((W - 64, y), val, font=_font(34, bold=True),
               fill=vb["color"] if v["aqi"] is not None else GREY, anchor="ra")
        y += 56
    y += 20

    # Footer
    d.line([60, y, W - 60, y], fill=(225, 229, 234), width=2)
    y += 24
    footer = ("Dati: World Air Quality Index Project - ARPA Campania. "
              "Dati grezzi non validati, a solo scopo informativo.")
    for riga in _wrap(d, footer, _font(24), W - 120):
        d.text((60, y), riga, font=_font(24), fill=GREY)
        y += 32
    y += 20

    img = img.crop((0, 0, W, y))
    img.save(out_path)
    return out_path


# --- Invio ------------------------------------------------------------------

def send_photo(path, caption):
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto"
    with open(path, "rb") as f:
        resp = requests.post(url,
                             data={"chat_id": TG_CHAT, "caption": caption,
                                   "parse_mode": "HTML"},
                             files={"photo": f}, timeout=60)
    if not resp.ok:
        sys.exit(f"Errore invio Telegram ({resp.status_code}): {resp.text}")
    print("Bollettino inviato correttamente.")


def main():
    check_env()
    data = fetch_solofra()
    nearby = fetch_nearby()
    caption = build_caption(data, nearby)
    path = crea_immagine(data, nearby)
    print("--- Anteprima didascalia ---")
    print(caption)
    print("----------------------------")
    send_photo(path, caption)


if __name__ == "__main__":
    main()
