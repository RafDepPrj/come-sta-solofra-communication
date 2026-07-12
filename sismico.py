#!/usr/bin/env python3
"""
Come Sta Solofra — avviso sismico.

Controlla periodicamente il catalogo pubblico INGV (ISIDe, via il servizio
standard FDSN) per eventi sismici vicino a Solofra, e pubblica un messaggio
SOLO quando c'è un evento che supera una soglia di rilevanza — mai per
le microscosse che l'Irpinia registra di continuo e che nessuno sente.

Soglie (due livelli, più vicino = basta meno magnitudo per essere rilevante):
  - entro 20 km da Solofra: magnitudo >= 2.0
  - entro 60 km da Solofra: magnitudo >= 3.5

Fonte: INGV — Istituto Nazionale di Geofisica e Vulcanologia, catalogo
ISIDe, servizio pubblico FDSN event, nessuna autenticazione richiesta.

⚠️ NON verificato con una chiamata reale da questo ambiente (il dominio
webservices.ingv.it blocca l'accesso automatico anche in sola lettura).
Il formato testo interrogato qui è lo standard FDSN, documentato e stabile
(lo stesso usato da USGS e altri osservatori), ma la prima esecuzione VERA
va controllata nei log di GitHub Actions prima di fidarsi ciecamente.

Il servizio è dotato di un selettore a TRE STATI (vedi funzione `ambiente()`),
non un semplice acceso/spento:
  - "spento"     -> nessun messaggio, da nessuna parte
  - "test"       -> messaggi mandati solo sul canale privato di prova
  - "produzione" -> messaggi mandati sul canale vero, con gli iscritti
Il valore di default (variabile assente o vuota) è "test". Qualsiasi valore
non riconosciuto viene trattato come "spento", per sicurezza — un refuso
non deve mai risultare in un invio sul canale vero.

Variabili d'ambiente (riusa gli stessi Secrets del bollettino aria):
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_CHAT_ID_TEST
Variabile di repository (non segreta, il selettore a tre stati):
  SISMICO_AMBIENTE = "spento" | "test" | "produzione"
Variabili di repository per la verifica (non inviano nulla su Telegram):
  SISMICO_MESSAGGIO_DI_PROVA = "true" manda un messaggio con dati finti,
      per vedere il formato senza aspettare un evento vero
  SISMICO_DIAGNOSTICA = "true" interroga INGV su una finestra larga (30
      giorni, 100 km) e stampa solo nei log cosa ha trovato — serve a
      dimostrare che la connessione e il formato funzionano DAVVERO,
      perché "zero eventi rilevanti" e "richiesta rotta" altrimenti
      producono lo stesso identico output nei log
"""

import os
import sys
import json
import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

# Coordinate approssimative del centro di Solofra (AV).
SOLOFRA_LAT = 40.836
SOLOFRA_LON = 14.841

INGV_URL = "http://webservices.ingv.it/fdsnws/event/1/query"

STATO_PATH = "stato_sismico.json"
STATO_RETENTION_GIORNI = 30       # quanto tenere gli ID già notificati
FINESTRA_CONTROLLO_ORE = 4        # margine sulla frequenza di controllo,
                                   # per non perdere eventi tra un run e l'altro

# (raggio_km, magnitudo_minima) — dal più vicino/permissivo al più lontano/esigente
SOGLIE = [(20, 2.0), (60, 3.5)]

TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TG_CHAT_PRODUZIONE = os.environ.get("TELEGRAM_CHAT_ID")
TG_CHAT_TEST = os.environ.get("TELEGRAM_CHAT_ID_TEST")


def ambiente():
    grezzo = os.environ.get("SISMICO_AMBIENTE", "").strip().lower()
    if grezzo == "":
        return "test"
    if grezzo in ("test", "produzione"):
        return grezzo
    return "spento"


def chat_destinazione(amb):
    if amb == "produzione":
        return TG_CHAT_PRODUZIONE
    if amb == "test":
        return TG_CHAT_TEST
    return None


def check_env(amb):
    mancanti = []
    if not TG_TOKEN:
        mancanti.append("TELEGRAM_BOT_TOKEN")
    if amb == "produzione" and not TG_CHAT_PRODUZIONE:
        mancanti.append("TELEGRAM_CHAT_ID")
    if amb == "test" and not TG_CHAT_TEST:
        mancanti.append("TELEGRAM_CHAT_ID_TEST")
    if mancanti:
        sys.exit(f"Errore: variabili d'ambiente mancanti: {', '.join(mancanti)}")


# --- Geografia ---------------------------------------------------------------

def distanza_km(lat1, lon1, lat2, lon2):
    """Distanza approssimata in km (formula di Haversine)."""
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(a))


def rilevante(distanza, magnitudo):
    for raggio, mag_min in SOGLIE:
        if distanza <= raggio and magnitudo >= mag_min:
            return True
    return False


# --- Stato: quali eventi abbiamo già notificato -------------------------------

def leggi_notificati():
    try:
        with open(STATO_PATH, encoding="utf-8") as f:
            dati = json.load(f)
        return set(dati.get("ids", []))
    except (FileNotFoundError, ValueError, OSError):
        return set()


def salva_notificati(ids):
    with open(STATO_PATH, "w", encoding="utf-8") as f:
        json.dump({"ids": sorted(ids),
                  "aggiornato": datetime.now(ZoneInfo("Europe/Rome"))
                  .isoformat(timespec="minutes")}, f)


# --- Recupero e parsing eventi -------------------------------------------------

def interroga_ingv(inizio, fine, raggio_km, mag_minima):
    """Chiamata generica a INGV — usata sia dal controllo normale sia dalla
    diagnostica, così testiamo davvero lo stesso identico percorso di
    codice e non una copia parallela che potrebbe comportarsi diversamente."""
    params = {
        "starttime": inizio.strftime("%Y-%m-%dT%H:%M:%S"),
        "endtime": fine.strftime("%Y-%m-%dT%H:%M:%S"),
        "lat": SOLOFRA_LAT,
        "lon": SOLOFRA_LON,
        "maxradiuskm": raggio_km,
        "minmagnitude": mag_minima,
        "format": "text",
    }
    resp = requests.get(INGV_URL, params=params, timeout=30)
    print(f"[diagnostica] URL: {resp.url}")
    print(f"[diagnostica] Codice HTTP: {resp.status_code}")
    resp.raise_for_status()
    print(f"[diagnostica] Lunghezza risposta grezza: {len(resp.text)} "
         f"caratteri")
    return resp.text


def fetch_eventi():
    """Interroga l'API INGV per gli eventi delle ultime FINESTRA_CONTROLLO_ORE
    ore entro il raggio più ampio delle soglie, poi filtra localmente."""
    ora = datetime.now(ZoneInfo("UTC"))
    inizio = ora - timedelta(hours=FINESTRA_CONTROLLO_ORE)
    raggio_max = max(r for r, _ in SOGLIE)
    mag_min = min(m for _, m in SOGLIE) - 0.5  # margine, filtriamo dopo

    testo = interroga_ingv(inizio, ora, raggio_max, mag_min)
    return parse_testo_fdsn(testo)


def diagnostica():
    """NON manda nulla su Telegram: interroga INGV con una finestra molto
    più larga (30 giorni, 100 km, qualunque magnitudo) per dimostrare se la
    connessione e il formato funzionano davvero, indipendentemente dal
    fatto che negli ultimi eventi non ci fosse nulla di rilevante da
    segnalare — "zero eventi rilevanti" e "la richiesta è rotta" hanno
    altrimenti lo stesso identico output nei log."""
    print("=== DIAGNOSTICA: nessun messaggio verrà inviato, solo lettura ===\n")
    ora = datetime.now(ZoneInfo("UTC"))
    inizio = ora - timedelta(days=30)
    try:
        testo = interroga_ingv(inizio, ora, 100, 0.0)
    except requests.RequestException as e:
        print(f"\n❌ ERRORE: la chiamata a INGV è fallita: {e}")
        print("Questo conferma un problema di connessione o di formato, "
             "non semplicemente 'nessun evento'.")
        return

    eventi = parse_testo_fdsn(testo)
    print(f"\nEventi trovati negli ultimi 30 giorni entro 100 km: "
         f"{len(eventi)}")

    if not eventi:
        print("\n⚠️ ZERO eventi in 30 giorni entro 100 km è molto "
             "improbabile per l'Irpinia. Probabile problema di parametri "
             "o di formato — incolla queste righe di log per farle "
             "controllare, prima di fidarti dei controlli regolari.")
        print("\nPrime righe della risposta grezza, per capire cosa ha "
             "risposto INGV davvero:")
        print(testo[:500])
        return

    print("✅ La connessione e il formato funzionano: questi sono eventi "
         "reali letti da INGV.\n")
    print("Alcuni esempi (fino a 5, i più vicini a Solofra):")
    eventi_con_distanza = [
        (distanza_km(SOLOFRA_LAT, SOLOFRA_LON, e["lat"], e["lon"]), e)
        for e in eventi if e["magnitudo"] is not None
    ]
    eventi_con_distanza.sort(key=lambda x: x[0])
    for dist, e in eventi_con_distanza[:5]:
        print(f"  - mag {e['magnitudo']:.1f}, {dist:.0f} km da Solofra, "
             f"zona: {e['zona']}, ora: {e['tempo']}")


def parse_testo_fdsn(testo):
    """Formato FDSN 'text': righe pipe-delimited, prima colonna intestazione
    con '#'. Colonne standard:
    EventID|Time|Latitude|Longitude|Depth/Km|Author|Catalog|Contributor|
    ContributorID|MagType|Magnitude|MagAuthor|EventLocationName"""
    eventi = []
    for riga in testo.strip().splitlines():
        riga = riga.strip()
        if not riga or riga.startswith("#"):
            continue
        campi = riga.split("|")
        if len(campi) < 13:
            continue
        try:
            eventi.append({
                "id": campi[0],
                "tempo": campi[1],
                "lat": float(campi[2]),
                "lon": float(campi[3]),
                "profondita_km": float(campi[4]) if campi[4] else None,
                "magnitudo": float(campi[10]) if campi[10] else None,
                "zona": campi[12] or "zona non specificata",
            })
        except (ValueError, IndexError):
            continue  # riga malformata: salta senza bloccare il resto
    return eventi


# --- Messaggio -----------------------------------------------------------------

def build_message(evento, distanza):
    mag = evento["magnitudo"]
    icona = "\U0001F534" if mag >= 3.5 else "\U0001F7E1"
    try:
        t = datetime.fromisoformat(evento["tempo"].replace("Z", "+00:00"))
        ora_it = t.astimezone(ZoneInfo("Europe/Rome")).strftime("%H:%M")
    except ValueError:
        ora_it = evento["tempo"]

    righe = [
        f"{icona} <b>Come sta Solofra</b> \u2014 evento sismico",
        "",
        f"Magnitudo <b>{mag:.1f}</b>, zona: {evento['zona']}",
        f"A circa {distanza:.0f} km da Solofra \u00b7 ore {ora_it}",
    ]
    if evento["profondita_km"] is not None:
        righe.append(f"Profondità: {evento['profondita_km']:.0f} km")
    righe.append("")
    righe.append("Fonte: INGV \u2014 Istituto Nazionale di Geofisica e "
                 "Vulcanologia (catalogo ISIDe).")
    righe.append("<i>Dato preliminare, può essere rivisto nelle ore "
                 "successive dall'INGV.</i>")
    righe.append("")
    righe.append("\u26A0\ufe0f <i>Questo non è un canale di emergenza "
                 "ufficiale. Per allerte e indicazioni di sicurezza, fai "
                 "sempre riferimento a INGV e Protezione Civile.</i>")
    return "\n".join(righe)


def send_message(text, chat_id):
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    resp = requests.post(url, json={
        "chat_id": chat_id, "text": text, "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }, timeout=30)
    if not resp.ok:
        sys.exit(f"Errore invio Telegram ({resp.status_code}): {resp.text}")
    print("Avviso sismico inviato correttamente.")


# --- Main ------------------------------------------------------------------

def invia_esempio(chat_id):
    """Manda un messaggio con dati di esempio (non un evento reale), per
    vedere subito il formato nel canale senza aspettare un vero terremoto.
    Attivato con SISMICO_MESSAGGIO_DI_PROVA=true. Passa dallo stesso
    build_message()/send_message() della modalità normale, quindi il
    formato è identico a quello reale — cambia solo il contenuto, ed è
    chiaramente etichettato come prova in cima al messaggio."""
    evento = {
        "id": "esempio-non-reale",
        "tempo": datetime.now(ZoneInfo("UTC")).isoformat(),
        "lat": SOLOFRA_LAT + 0.05,
        "lon": SOLOFRA_LON + 0.03,
        "profondita_km": 9.0,
        "magnitudo": 2.7,
        "zona": "4 km NE Solofra (AV) \u2014 dato di esempio",
    }
    distanza = distanza_km(SOLOFRA_LAT, SOLOFRA_LON, evento["lat"], evento["lon"])
    msg = ("\u26A0\ufe0f <b>MESSAGGIO DI PROVA \u2014 nessun evento reale "
          "è avvenuto</b>\n\n" + build_message(evento, distanza))
    print("--- Anteprima (messaggio di prova) ---")
    print(msg)
    print("----------------------------------------")
    send_message(msg, chat_id)


def main():
    if os.environ.get("SISMICO_DIAGNOSTICA", "").strip().lower() == "true":
        diagnostica()
        return  # la diagnostica non manda nulla: si ferma sempre qui

    amb = ambiente()
    if amb == "spento":
        print("Ambiente 'spento' (o valore non riconosciuto in "
             "SISMICO_AMBIENTE): nessun controllo inviato.")
        return

    check_env(amb)
    chat_id = chat_destinazione(amb)
    print(f"Ambiente attivo: {amb.upper()}")

    if os.environ.get("SISMICO_MESSAGGIO_DI_PROVA", "").strip().lower() \
            == "true":
        invia_esempio(chat_id)
        return

    già_notificati = leggi_notificati()

    try:
        eventi = fetch_eventi()
    except requests.RequestException as e:
        print(f"Errore nel contattare INGV: {e}. Nessun invio, riproverò "
             f"al prossimo controllo.")
        return

    nuovi_notificati = set(già_notificati)
    inviati = 0
    for ev in eventi:
        if ev["id"] in già_notificati or ev["magnitudo"] is None:
            continue
        distanza = distanza_km(SOLOFRA_LAT, SOLOFRA_LON, ev["lat"], ev["lon"])
        if not rilevante(distanza, ev["magnitudo"]):
            continue
        msg = build_message(ev, distanza)
        print("--- Anteprima ---")
        print(msg)
        print("-----------------")
        send_message(msg, chat_id)
        nuovi_notificati.add(ev["id"])
        inviati += 1

    if inviati == 0:
        print(f"Controllati {len(eventi)} eventi nella finestra: nessuno "
             f"supera le soglie di rilevanza. Nessun messaggio inviato.")

    # Pota gli ID più vecchi di STATO_RETENTION_GIORNI equivalenti (qui,
    # semplicemente limitiamo la dimensione tenendo gli ultimi 500 — gli
    # eventi rilevanti non sono mai così frequenti da renderlo un problema)
    salva_notificati(set(list(nuovi_notificati)[-500:]))


if __name__ == "__main__":
    main()
