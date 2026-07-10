#!/usr/bin/env python3
"""
Come Sta Solofra — bollettino sulla qualità dell'aria con immagine.

- Stazione "Solofra Zona Industriale" (ID 10922), dati WAQI / ARPA Campania.
- Confronto coi comuni vicini (solo quelli con centralina attiva).
- Posizione di Solofra nella zona, previsione di domani, indice UV, meteo.
- Card grafica (PNG) con barra AQI a gradiente, pubblicata su un canale Telegram.

Variabili d'ambiente (GitHub Secrets):
  WAQI_TOKEN, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
"""

import os
import sys
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from PIL import Image, ImageDraw, ImageFont

# --- Configurazione ---------------------------------------------------------

STATION_ID = "@10922"
FEED_URL = "https://api.waqi.info/feed/{}/"
SEARCH_URL = "https://api.waqi.info/search/"
PAGINA_STAZIONE = ("https://aqicn.org/city/italy/campania/solofra/"
                   "solofra-zona-industriale/")

# Comuni vicini con centralina. Chi non ha una stazione (o è temporaneamente
# spento) viene nascosto in automatico. Per aggiungerne uno basta il nome.
NEARBY = ["Avellino", "Montoro", "Mercato San Severino", "Salerno"]

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
    """(emoji, label, advice, color RGB, text RGB) per un valore AQI."""
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
    """AQI del comune via ricerca per nome; None se non trovato/attivo."""
    try:
        resp = requests.get(SEARCH_URL,
                            params={"token": WAQI_TOKEN, "keyword": keyword},
                            timeout=30)
        resp.raise_for_status()
        js = resp.json()
        if js.get("status") != "ok":
            return None
        parole = [w for w in keyword.lower().split()
                  if len(w) > 3 and w not in ("san", "santa")]
        ripiego = None
        for item in js.get("data", []):
            nome = (item.get("station") or {}).get("name", "").lower()
            aqi = to_int(item.get("aqi"))
            if aqi is None:
                continue
            if any(p in nome for p in parole):
                return aqi
            if ripiego is None:
                ripiego = aqi
        return ripiego
    except requests.RequestException:
        return None


def fetch_nearby():
    """Solo comuni con un valore valido, ordinati dal più pulito."""
    risultati = [{"nome": c, "aqi": cerca_aqi(c)} for c in NEARBY]
    validi = [r for r in risultati if r["aqi"] is not None]
    return sorted(validi, key=lambda r: r["aqi"])


# --- Previsione, UV, meteo, posizione --------------------------------------

def _forecast_val(data, chiavi, giorno_offset, campo):
    fc = (data.get("forecast") or {}).get("daily") or {}
    target = (datetime.now(ZoneInfo("Europe/Rome")).date()
              + timedelta(days=giorno_offset)).isoformat()
    valori = []
    for k in chiavi:
        for entry in fc.get(k, []):
            if entry.get("day") == target:
                v = to_int(entry.get(campo))
                if v is not None:
                    valori.append(v)
    return max(valori) if valori else None


def previsione_domani(data):
    return _forecast_val(data, ("pm25", "pm10"), 1, "avg")


def uv_oggi(data):
    return _forecast_val(data, ("uvi",), 0, "max")


def uv_label(uv):
    if uv is None:
        return None
    if uv <= 2:
        return "basso"
    if uv <= 5:
        return "moderato"
    if uv <= 7:
        return "alto"
    if uv <= 10:
        return "molto alto"
    return "estremo"


def posizione(solofra_aqi, nearby):
    """(posizione, totale) di Solofra tra le stazioni con dato valido."""
    if solofra_aqi is None:
        return None
    valori = sorted([solofra_aqi] + [r["aqi"] for r in nearby])
    return valori.index(solofra_aqi) + 1, len(valori)


def meteo_str(data):
    iaqi = data.get("iaqi", {})
    pezzi = []
    t = to_int((iaqi.get("t") or {}).get("v"))
    if t is not None:
        pezzi.append(f"{t}\u00b0C")
    h = to_int((iaqi.get("h") or {}).get("v"))
    if h is not None:
        pezzi.append(f"umidità {h}%")
    return " \u00b7 ".join(pezzi)


def etichette_tempo():
    ora = datetime.now(ZoneInfo("Europe/Rome"))
    return f"{GIORNI[ora.weekday()]} {ora.day} {MESI[ora.month]}", \
        ora.strftime("%H:%M")


# --- Stato e tendenza -------------------------------------------------------

STATO_PATH = "stato.json"


def leggi_stato_aqi():
    """AQI dell'ultimo bollettino pubblicato; None se non c'e'."""
    try:
        with open(STATO_PATH, encoding="utf-8") as f:
            return to_int(json.load(f).get("aqi"))
    except (FileNotFoundError, ValueError, OSError):
        return None


def salva_stato_aqi(aqi):
    ora = datetime.now(ZoneInfo("Europe/Rome")).isoformat(timespec="minutes")
    with open(STATO_PATH, "w", encoding="utf-8") as f:
        json.dump({"aqi": aqi, "iso": ora}, f)


def tendenza(corrente, precedente, soglia=3):
    """Confronto col post precedente. soglia = margine per dirlo 'stabile'."""
    if corrente is None or precedente is None:
        return None
    delta = corrente - precedente
    if abs(delta) <= soglia:
        return {"freccia": "\u2192", "testo": "stabile", "delta": delta,
                "color": (110, 118, 128)}
    if delta > 0:  # AQI piu' alto = aria peggiore
        return {"freccia": "\u2197", "testo": "in peggioramento",
                "delta": delta, "color": (214, 64, 54)}
    return {"freccia": "\u2198", "testo": "in miglioramento",
            "delta": delta, "color": (46, 158, 79)}


# --- Didascalia -------------------------------------------------------------

def build_caption(data, nearby, trend=None):
    aqi = to_int(data.get("aqi"))
    b = banda(aqi)
    data_it, ora = etichette_tempo()
    aqi_txt = str(aqi) if aqi is not None else "n/d"

    righe = [
        f"{b['emoji']} <b>Come sta Solofra</b> \u2014 {data_it}, ore {ora}\n",
        f"<b>Qualità dell'aria: {b['label']}</b> (AQI {aqi_txt})",
        b["advice"],
    ]

    extra = []
    if trend:
        extra.append(f"{trend['freccia']} {trend['testo'].capitalize()} "
                     f"rispetto al post precedente ({trend['delta']:+d}).")

    pos = posizione(aqi, nearby)
    if pos:
        p, tot = pos
        if p == 1:
            extra.append("\U0001F3C6 Oggi è la zona più pulita tra le "
                         "stazioni vicine.")
        else:
            extra.append(f"\U0001F4CA Oggi è {p}\u00aa su {tot} nella zona "
                         "(dalla più pulita).")

    dom = previsione_domani(data)
    if dom is not None:
        bd = banda(dom)
        extra.append(f"\U0001F52E Domani: previsto {bd['label'].lower()} "
                     f"{bd['emoji']}")

    uv = uv_oggi(data)
    if uv is not None:
        extra.append(f"\u2600\ufe0f Indice UV oggi: {uv} ({uv_label(uv)})")

    meteo = meteo_str(data)
    if meteo:
        extra.append(f"\U0001F321\ufe0f {meteo}")

    if extra:
        righe.append("\n" + "\n".join(extra))

    righe.append(f"\n\U0001F517 Dettagli e storico: {PAGINA_STAZIONE}")
    righe.append("\n<i>Dati: World Air Quality Index Project \u00b7 ARPA "
                 "Campania. Dati grezzi non validati, a scopo informativo.</i>")
    return "\n".join(righe)


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


def _barra_aqi(d, x, y, w, h, aqi):
    """Barra orizzontale a gradiente con marcatore sul valore corrente."""
    segmenti = [(50, (46, 158, 79)), (100, (224, 168, 0)),
                (150, (233, 140, 20)), (200, (214, 64, 54)),
                (300, (142, 68, 173))]
    scala = 300
    prev = 0
    for soglia, colore in segmenti:
        x0 = x + w * prev / scala
        x1 = x + w * soglia / scala
        d.rectangle([x0, y, x1, y + h], fill=colore)
        prev = soglia
    # marcatore (triangolo) sopra la barra
    val = min(max(aqi or 0, 0), scala)
    mx = x + w * val / scala
    d.polygon([(mx - 13, y - 20), (mx + 13, y - 20), (mx, y - 3)],
              fill=(35, 40, 46))
    # estremi
    g = (150, 150, 150)
    d.text((x, y + h + 8), "0", font=_font(24), fill=g)
    d.text((x + w, y + h + 8), "300+", font=_font(24), fill=g, anchor="ra")


def crea_immagine(data, nearby, out_path="card.png", trend=None):
    W, BG = 1080, (245, 247, 250)
    GREY, DARK = (110, 118, 128), (35, 40, 46)

    aqi = to_int(data.get("aqi"))
    b = banda(aqi)
    data_it, ora = etichette_tempo()
    dom = data.get("dominentpol") or ""
    dom_label = POLLUTANTS.get(dom, dom.upper() if dom else "n/d")

    img = Image.new("RGB", (W, 1800), BG)
    d = ImageDraw.Draw(img)

    d.rectangle([0, 0, W, 14], fill=b["color"])
    d.text((60, 58), "COME STA SOLOFRA", font=_font(56, bold=True), fill=DARK)
    d.text((60, 136), f"Aggiornato {data_it}, ore {ora}",
           font=_font(30), fill=GREY)
    if trend:
        d.text((60, 182),
               f"{trend['freccia']} {trend['testo'].capitalize()} "
               f"({trend['delta']:+d})",
               font=_font(30, bold=True), fill=trend["color"])

    # Cerchio semaforo
    cx, cy, r = W // 2, 388, 148
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=b["color"])
    aqi_str = str(aqi) if aqi is not None else "--"
    fnum = _font(150 if len(aqi_str) <= 2 else 116, bold=True)
    tb = d.textbbox((0, 0), aqi_str, font=fnum)
    d.text((cx - (tb[2] - tb[0]) / 2, cy - (tb[3] - tb[1]) / 2 - tb[1]),
           aqi_str, font=fnum, fill=b["text"])
    d.text((cx, cy + r - 32), "AQI", font=_font(26, bold=True),
           fill=b["text"], anchor="mm")

    # Pill etichetta fascia
    y = cy + r + 34
    fl = _font(42, bold=True)
    lw = d.textlength(b["label"], font=fl)
    d.rounded_rectangle([cx - lw / 2 - 30, y, cx + lw / 2 + 30, y + 64],
                        radius=32, fill=b["color"])
    d.text((cx, y + 32), b["label"], font=fl, fill=b["text"], anchor="mm")
    y += 96

    # Barra a gradiente
    _barra_aqi(d, 140, y, W - 280, 26, aqi)
    y += 74

    d.text((cx, y), f"Inquinante principale: {dom_label}",
           font=_font(30), fill=GREY, anchor="ma")
    y += 60

    for riga in _wrap(d, b["advice"], _font(34), W - 180):
        d.text((cx, y), riga, font=_font(34), fill=DARK, anchor="ma")
        y += 46

    meteo = meteo_str(data)
    uv = uv_oggi(data)
    riga_meteo = meteo
    if uv is not None:
        riga_meteo += (" \u00b7 " if riga_meteo else "") + \
            f"UV {uv} ({uv_label(uv)})"
    if riga_meteo:
        y += 12
        d.text((cx, y), riga_meteo, font=_font(28), fill=GREY, anchor="ma")
        y += 44
    y += 20

    d.line([60, y, W - 60, y], fill=(225, 229, 234), width=2)
    y += 32

    d.text((60, y), "COMUNI VICINI", font=_font(34, bold=True), fill=DARK)
    pos = posizione(aqi, nearby)
    if pos:
        testo_pos = ("Solofra la più pulita" if pos[0] == 1
                     else f"Solofra {pos[0]}\u00aa su {pos[1]}")
        d.text((W - 60, y + 6), testo_pos, font=_font(26, bold=True),
               fill=b["color"], anchor="ra")
    y += 64

    if nearby:
        for v in nearby:
            vb = banda(v["aqi"])
            d.ellipse([64, y + 6, 92, y + 34], fill=vb["color"])
            d.text((116, y), v["nome"], font=_font(34), fill=DARK)
            d.text((W - 64, y), str(v["aqi"]), font=_font(34, bold=True),
                   fill=vb["color"], anchor="ra")
            y += 56
    else:
        d.text((116, y), "Dati dei comuni vicini non disponibili ora.",
               font=_font(30), fill=GREY)
        y += 56
    y += 16

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
    aqi = to_int(data.get("aqi"))

    precedente = leggi_stato_aqi()
    trend = tendenza(aqi, precedente)

    caption = build_caption(data, nearby, trend)
    path = crea_immagine(data, nearby, trend=trend)
    print("--- Anteprima didascalia ---")
    print(caption)
    print("----------------------------")
    send_photo(path, caption)

    # Salva il valore solo dopo un invio riuscito, per il confronto successivo.
    if aqi is not None:
        salva_stato_aqi(aqi)


if __name__ == "__main__":
    main()
