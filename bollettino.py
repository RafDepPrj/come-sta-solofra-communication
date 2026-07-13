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
# NOTA: verifica che questo sia l'URL reale della pagina "Aria" sulla tua
# dashboard — se il dominio è diverso, aggiorna qui.
DASHBOARD_URL = "https://rafdepprj.github.io/comestasolofra/aria.html"

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

# Traduzione in linguaggio semplice + icona di categoria, per rendere
# comprensibile "quale inquinante è più alto ora" senza dover conoscere le
# sigle tecniche. Mostrata sempre, con parole semplici — non è un giudizio
# di gravità (quello lo dice già la fascia colorata sopra).
CAUSA_PROBABILE = {
    "pm25": ("\U0001F697", "traffico e riscaldamento"),
    "pm10": ("\U0001F32B\ufe0f", "polveri diffuse (traffico, cantieri)"),
    "no2":  ("\U0001F697", "traffico veicolare"),
    "so2":  ("\U0001F3ED", "attività industriali"),
    "co":   ("\U0001F697", "traffico e combustione"),
    "o3":   ("\u2600\ufe0f", "sole e caldo"),
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
    """AQI del comune via ricerca per nome; None se non c'è una stazione
    il cui nome corrisponde davvero. NIENT'ALTRO: mai restituire il dato
    di una stazione diversa spacciandolo per quella cercata — meglio "n/d"
    che un dato falso attribuito al comune sbagliato."""
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
        for item in js.get("data", []):
            nome = (item.get("station") or {}).get("name", "").lower()
            aqi = to_int(item.get("aqi"))
            if aqi is None:
                continue
            if any(p in nome for p in parole):
                return aqi
        return None  # nessuna stazione con questo nome: non inventare un dato
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


def riga_contesto(data):
    """Meteo + UV + previsione di domani, compressi in un'unica riga.
    Sempre presente, ma un solo rigo — mai tre righe separate."""
    iaqi = data.get("iaqi", {})
    pezzi = []

    t = to_int((iaqi.get("t") or {}).get("v"))
    if t is not None:
        pezzi.append(f"{t}\u00b0C")

    uv = uv_oggi(data)
    if uv is not None:
        pezzi.append(f"UV {uv_label(uv)}")

    dom = previsione_domani(data)
    if dom is not None:
        bd = banda(dom)
        pezzi.append(f"domani {bd['emoji']} {bd['label'].lower()}")

    return " \u00b7 ".join(pezzi)


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

ORE_MINIME_TRA_POST = 4       # margine anti-rimbalzo tra due fasce vicine
GIORNI_MAX_SILENZIO = 7        # oltre questo, un post "tutto ok" di controllo


def leggi_stato():
    """Stato dell'ultimo bollettino PUBBLICATO (non ogni controllo)."""
    try:
        with open(STATO_PATH, encoding="utf-8") as f:
            s = json.load(f)
        return {"aqi": to_int(s.get("aqi")), "banda": s.get("banda"),
                "quando": s.get("quando")}
    except (FileNotFoundError, ValueError, OSError):
        return None


def salva_stato(aqi, banda_label):
    ora = datetime.now(ZoneInfo("Europe/Rome")).isoformat(timespec="minutes")
    with open(STATO_PATH, "w", encoding="utf-8") as f:
        json.dump({"aqi": aqi, "banda": banda_label, "quando": ora}, f)


def valuta_se_pubblicare(aqi_corrente, stato):
    """Decide se pubblicare ORA. Criterio: solo quando cambia la fascia
    (non per piccole oscillazioni interne alla stessa fascia), con un
    margine minimo tra un post e l'altro per evitare rimbalzi sul confine
    tra due fasce. Se il canale resta silenzioso troppo a lungo, un post
    di controllo anche senza variazioni — per non sembrare abbandonato."""
    if stato is None or not stato.get("quando"):
        return True, "primo post"

    banda_corrente = banda(aqi_corrente)["label"]
    try:
        ultimo = datetime.fromisoformat(stato["quando"])
        if ultimo.tzinfo is None:
            ultimo = ultimo.replace(tzinfo=ZoneInfo("Europe/Rome"))
    except ValueError:
        return True, "stato illeggibile"

    ore_trascorse = (datetime.now(ZoneInfo("Europe/Rome")) - ultimo) \
        .total_seconds() / 3600

    cambio_fascia = banda_corrente != stato.get("banda")
    if cambio_fascia and ore_trascorse >= ORE_MINIME_TRA_POST:
        return True, "cambio fascia"

    if ore_trascorse >= GIORNI_MAX_SILENZIO * 24:
        return True, "nessun aggiornamento da 7 giorni"

    return False, None


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


# --- Storico per il riepilogo settimanale -----------------------------------

STORICO_PATH = "storico.json"
STORICO_GIORNI_RETENTION = 9  # un filo più di 7, per assorbire ritardi cron


def registra_storico(aqi, banda_label, nearby):
    """Aggiunge UN record ad ogni controllo (pubblichi o no il bollettino
    giornaliero) — è la materia prima per il grafico di fine settimana.
    Pota da sola i record più vecchi di STORICO_GIORNI_RETENTION giorni,
    così il file non cresce all'infinito."""
    try:
        with open(STORICO_PATH, encoding="utf-8") as f:
            record = json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        record = []

    ora = datetime.now(ZoneInfo("Europe/Rome"))
    record.append({
        "ts": ora.isoformat(timespec="minutes"),
        "aqi": aqi,
        "banda": banda_label,
        "nearby": {v["nome"]: v["aqi"] for v in nearby} if nearby else {},
    })

    soglia = ora - timedelta(days=STORICO_GIORNI_RETENTION)
    record = [r for r in record
             if datetime.fromisoformat(r["ts"]) >= soglia]

    with open(STORICO_PATH, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=1)


# --- Didascalia -------------------------------------------------------------

def build_caption(data, nearby, trend=None):
    """Ogni riga deve guadagnarsi il posto: solo ciò che aiuta a capire o
    decidere, niente paragrafi. Struttura fissa, sempre le stesse 7-8 righe
    corte — la lunghezza non varia, cambia solo cosa dicono le righe."""
    aqi = to_int(data.get("aqi"))
    b = banda(aqi)
    _, ora = etichette_tempo()
    aqi_txt = str(aqi) if aqi is not None else "n/d"

    righe = [f"{b['emoji']} <b>Come sta Solofra</b> \u2014 ore {ora}",
            f"<b>AQI {aqi_txt} \u2014 {b['label']}</b>",
            b["advice"]]

    if trend:
        righe.append(f"{trend['freccia']} {trend['testo'].capitalize()}"
                     f" rispetto al post precedente")

    # Causa probabile, sempre presente ma in linguaggio semplice — mai la
    # sigla tecnica da sola, sempre con l'icona di categoria.
    dom = data.get("dominentpol") or ""
    if dom in CAUSA_PROBABILE:
        icona, causa = CAUSA_PROBABILE[dom]
        righe.append(f"{icona} Il motivo principale: {causa}")

    if nearby:
        confronto = " \u00b7 ".join(
            f"{banda(v['aqi'])['emoji']} {v['nome']} {v['aqi']}"
            for v in nearby[:4]
        )
        righe.append(f"\U0001F4CD {confronto}")

    contesto = riga_contesto(data)
    if contesto:
        righe.append(f"\U0001F321\ufe0f {contesto}")

    righe.append(f"Il dato: {PAGINA_STAZIONE}")
    righe.append(f"Come funziona questo canale: {DASHBOARD_URL}")
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


POLLUTANT_ORDER = ["pm25", "pm10", "no2", "so2", "co", "o3"]


def sub_indici(data):
    """Lista ordinata (chiave, etichetta, valore) per gli inquinanti con
    una lettura valida in questo momento."""
    iaqi = data.get("iaqi", {})
    righe = []
    for k in POLLUTANT_ORDER:
        cell = iaqi.get(k)
        if cell and "v" in cell:
            v = to_int(cell["v"])
            if v is not None:
                righe.append((k, POLLUTANTS[k], v))
    return righe


def _barra_aqi(d, x, y, w, h, aqi):
    """Barra orizzontale a gradiente con marcatore sul valore corrente.
    Etichette in parole, non numeri tecnici: risponde a "il numero cosa
    vuol dire?" mostrando subito dove si trova sulla scala pulito→pericoloso."""
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
    # etichette in parole, non "0"/"300+"
    g = (150, 150, 150)
    d.text((x, y + h + 8), "aria pulita", font=_font(22), fill=g)
    d.text((x + w, y + h + 8), "aria pericolosa", font=_font(22), fill=g,
          anchor="ra")


def crea_immagine(data, nearby, out_path="card.png", trend=None):
    """Card minimale: solo ciò che serve per un colpo d'occhio.
    Il dettaglio per inquinante, meteo e UV vivono nella pagina dashboard,
    non nel post ricorrente — qui contano solo numero, colore, una frase,
    tendenza e il confronto coi comuni vicini."""
    W, BG = 1080, (245, 247, 250)
    GREY, DARK = (110, 118, 128), (35, 40, 46)

    aqi = to_int(data.get("aqi"))
    b = banda(aqi)
    data_it, ora = etichette_tempo()

    img = Image.new("RGB", (W, 1300), BG)
    d = ImageDraw.Draw(img)

    d.rectangle([0, 0, W, 14], fill=b["color"])
    d.text((60, 50), "COME STA SOLOFRA", font=_font(38, bold=True), fill=GREY)
    d.text((W - 60, 44), f"ore {ora}", font=_font(34, bold=True), fill=GREY,
          anchor="ra")
    d.text((W - 60, 84), data_it, font=_font(24), fill=GREY, anchor="ra")

    # Cerchio semaforo — l'elemento dominante della card
    cx, cy, r = W // 2, 330, 190
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=b["color"])
    aqi_str = str(aqi) if aqi is not None else "--"
    fnum = _font(190 if len(aqi_str) <= 2 else 150, bold=True)
    tb = d.textbbox((0, 0), aqi_str, font=fnum)
    d.text((cx - (tb[2] - tb[0]) / 2, cy - (tb[3] - tb[1]) / 2 - tb[1] - 16),
           aqi_str, font=fnum, fill=b["text"])
    d.text((cx, cy + r - 44), "AQI", font=_font(30, bold=True),
           fill=b["text"], anchor="mm")

    y = cy + r + 36
    fl = _font(52, bold=True)
    d.text((cx, y), b["label"], font=fl, fill=b["color"], anchor="ma")
    y += 72

    # Barra di riferimento: risponde a "53 è tanto o poco?" in un'occhiata,
    # senza dover leggere o capire la scala numerica.
    y += 8
    _barra_aqi(d, 140, y, W - 280, 22, aqi)
    y += 60

    if trend:
        d.text((cx, y), f"{trend['freccia']} {trend['testo'].capitalize()}",
               font=_font(32, bold=True), fill=trend["color"], anchor="ma")
        y += 50

    y += 14
    for riga in _wrap(d, b["advice"], _font(32), W - 200):
        d.text((cx, y), riga, font=_font(32), fill=DARK, anchor="ma")
        y += 42
    y += 20

    # Causa probabile, sempre presente, tradotta in linguaggio semplice.
    dom = data.get("dominentpol") or ""
    if dom in CAUSA_PROBABILE:
        icona, causa = CAUSA_PROBABILE[dom]
        d.text((cx, y), f"{icona} Il motivo principale: {causa}",
               font=_font(27), fill=GREY, anchor="ma")
        y += 40
    y += 20

    d.line([80, y, W - 80, y], fill=(225, 229, 234), width=2)
    y += 34

    # Comuni vicini: solo la riga essenziale, un pallino colore + numero
    pos = posizione(aqi, nearby)
    d.text((60, y), "COME SIAMO RISPETTO AI VICINI",
           font=_font(26, bold=True), fill=DARK)
    if pos and pos[0] == 1:
        d.text((W - 60, y), "Solofra è la più pulita", font=_font(22, bold=True),
               fill=b["color"], anchor="ra")
    y += 50

    if nearby:
        for v in nearby[:4]:
            vb = banda(v["aqi"])
            d.ellipse([64, y + 4, 88, y + 28], fill=vb["color"])
            d.text((104, y), v["nome"], font=_font(28), fill=DARK)
            d.text((W - 60, y), str(v["aqi"]), font=_font(28, bold=True),
                   fill=vb["color"], anchor="ra")
            y += 44
    y += 20

    # Contesto: meteo + UV + previsione di domani, in un'unica riga.
    contesto = riga_contesto(data)
    if contesto:
        d.line([80, y, W - 80, y], fill=(225, 229, 234), width=2)
        y += 30
        d.text((cx, y), contesto, font=_font(27), fill=DARK, anchor="ma")
        y += 40
    y += 10

    d.line([80, y, W - 80, y], fill=(225, 229, 234), width=2)
    y += 22
    d.text((cx, y), "Fonte: ARPA Campania · World Air Quality Index Project",
           font=_font(20), fill=GREY, anchor="ma")
    y += 34

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


MINUTI_MINIMI_TRA_CONTROLLI = 110  # ~2 ore, con margine di sicurezza


def tempo_da_ultimo_controllo():
    """Minuti trascorsi dall'ultimo controllo registrato in storico.json
    (che si aggiorna ad OGNI esecuzione, pubblichi o no). None se non c'è
    ancora nessuno storico (primissima esecuzione in assoluto)."""
    try:
        with open(STORICO_PATH, encoding="utf-8") as f:
            record = json.load(f)
        if not record:
            return None
        ultimo_ts = max(datetime.fromisoformat(r["ts"]) for r in record)
        ora = datetime.now(ZoneInfo("Europe/Rome"))
        return (ora - ultimo_ts).total_seconds() / 60
    except (FileNotFoundError, ValueError, OSError, KeyError):
        return None


def main():
    # Il workflow ora gira molto più spesso di quanto serva (per assorbire
    # il fatto che GitHub può saltare dei trigger programmati, come
    # osservato più volte) — è qui, non nel cron, che decidiamo se è
    # davvero il momento di controllare l'aria. Un tentativo perso da
    # GitHub non lascia più un buco di 2 ore: ne arriva un altro pochi
    # minuti dopo, e QUESTO controllo decide se è già tempo o è troppo
    # presto rispetto all'ultimo vero controllo fatto.
    minuti_trascorsi = tempo_da_ultimo_controllo()
    if minuti_trascorsi is not None and minuti_trascorsi < MINUTI_MINIMI_TRA_CONTROLLI:
        print(f"Ultimo controllo {minuti_trascorsi:.0f} minuti fa "
             f"(< {MINUTI_MINIMI_TRA_CONTROLLI}): troppo presto, nessuna "
             f"chiamata a WAQI questa volta.")
        return

    check_env()
    data = fetch_solofra()
    aqi = to_int(data.get("aqi"))
    nearby = fetch_nearby()  # sempre: serve anche quando non pubblichiamo

    # Registra SEMPRE questo controllo nello storico, a prescindere dal
    # fatto che si pubblichi o meno — è la base dati per il riepilogo
    # settimanale del lunedì.
    registra_storico(aqi, banda(aqi)["label"], nearby)

    stato = leggi_stato()
    pubblica, motivo = valuta_se_pubblicare(aqi, stato)

    if not pubblica:
        fascia_ora = banda(aqi)["label"]
        print(f"Nessuna pubblicazione: fascia invariata ({fascia_ora}), "
             f"o troppo presto dall'ultimo post. Storico comunque aggiornato.")
        return

    print(f"Pubblico: {motivo}")
    precedente = stato["aqi"] if stato else None
    trend = tendenza(aqi, precedente)

    caption = build_caption(data, nearby, trend)
    path = crea_immagine(data, nearby, trend=trend)
    print("--- Anteprima didascalia ---")
    print(caption)
    print("----------------------------")
    send_photo(path, caption)

    # Salva lo stato solo dopo un invio riuscito, per il confronto successivo.
    if aqi is not None:
        salva_stato(aqi, banda(aqi)["label"])


if __name__ == "__main__":
    main()
