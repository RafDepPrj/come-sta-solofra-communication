#!/usr/bin/env python3
"""
Come Sta Solofra — riepilogo settimanale della qualità dell'aria.

Legge storico.json (scritto ad ogni controllo da bollettino.py, anche nei
run che non pubblicano) e ogni lunedì mattina pubblica un grafico a barre
giornaliere della settimana appena conclusa, con:
- una barra per giorno (Lun-Dom), altezza = picco AQI del giorno, colore
  = fascia raggiunta in quel picco;
- gli "eventi" della settimana (finestre in cui l'aria è peggiorata e poi
  tornata buona), descritti in linguaggio semplice;
- il confronto con i comuni vicini sulla media settimanale.

Esce SEMPRE il lunedì, anche in una settimana piatta (in quel caso il
messaggio lo dice esplicitamente, invece di sparire).

Variabili d'ambiente (GitHub Secrets):
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
(non serve WAQI_TOKEN: lavora sui dati già raccolti da bollettino.py)
"""

import os
import sys
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from collections import defaultdict

import requests
from PIL import Image, ImageDraw, ImageFont

# Riusa le stesse definizioni di fascia/colore del bollettino giornaliero,
# per coerenza visiva — se banda() cambia là, cambia anche qui.
from bollettino import banda, to_int, _font, _wrap  # noqa: E402

STORICO_PATH = "storico.json"
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID")

GIORNI_SETT = ["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"]
GIORNI_LUNGHI = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì",
                 "sabato", "domenica"]


def check_env():
    for name, val in [("TELEGRAM_BOT_TOKEN", TG_TOKEN),
                      ("TELEGRAM_CHAT_ID", TG_CHAT)]:
        if not val:
            sys.exit(f"Errore: variabile d'ambiente mancante: {name}")


# --- Lettura e finestra settimanale -----------------------------------------

def carica_storico():
    try:
        with open(STORICO_PATH, encoding="utf-8") as f:
            record = json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        return []
    for r in record:
        r["_dt"] = datetime.fromisoformat(r["ts"])
    return record


def finestra_settimana_scorsa(oggi=None):
    """[lunedì scorso 00:00, oggi 00:00) — la settimana appena conclusa,
    assumendo che lo script giri il lunedì mattina successivo."""
    oggi = oggi or datetime.now(ZoneInfo("Europe/Rome"))
    fine = oggi.replace(hour=0, minute=0, second=0, microsecond=0)
    inizio = fine - timedelta(days=7)
    return inizio, fine


# --- Aggregazione per giorno -------------------------------------------------

def aggrega_per_giorno(record, inizio, fine):
    """{0: [record lunedì], 1: [record martedì], ...} — chiave = giorno
    della settimana (0=lunedì), solo per i record nella finestra."""
    per_giorno = defaultdict(list)
    for r in record:
        if inizio <= r["_dt"] < fine:
            idx = (r["_dt"] - inizio).days
            if 0 <= idx <= 6:
                per_giorno[idx].append(r)
    return per_giorno


def statistiche_giorno(record_giorno):
    valori = [r["aqi"] for r in record_giorno if r.get("aqi") is not None]
    if not valori:
        return None
    return {"min": min(valori), "max": max(valori),
           "media": round(sum(valori) / len(valori))}


# --- Rilevamento eventi ------------------------------------------------------

def rileva_eventi(record, inizio, fine):
    """Individua le finestre in cui la fascia è peggiore di 'BUONA' e poi
    torna a 'BUONA' — restituisce una lista di eventi in linguaggio
    naturale, dal più rilevante (picco più alto) al meno rilevante."""
    seq = sorted([r for r in record if inizio <= r["_dt"] < fine],
                key=lambda r: r["_dt"])

    eventi = []
    corrente = None
    for r in seq:
        peggiore = r.get("banda") != "BUONA"
        if peggiore and corrente is None:
            corrente = {"inizio": r["_dt"], "fine": r["_dt"],
                       "picco": r["aqi"], "banda": r["banda"]}
        elif peggiore and corrente is not None:
            corrente["fine"] = r["_dt"]
            if r["aqi"] and r["aqi"] > corrente["picco"]:
                corrente["picco"] = r["aqi"]
                corrente["banda"] = r["banda"]
        elif not peggiore and corrente is not None:
            eventi.append(corrente)
            corrente = None
    if corrente is not None:
        eventi.append(corrente)

    frasi = []
    for e in sorted(eventi, key=lambda x: x["picco"] or 0, reverse=True)[:3]:
        giorno = GIORNI_LUNGHI[e["inizio"].weekday()]
        durata_ore = max(1, round(
            (e["fine"] - e["inizio"]).total_seconds() / 3600) + 2)
        b = banda(e["picco"])
        frasi.append(
            f"{giorno.capitalize()}: aria peggiorata per circa {durata_ore}h "
            f"(fascia {b['label'].lower()}, picco {e['picco']}), poi tornata "
            f"buona."
        )
    return frasi


# --- Confronto settimanale coi comuni vicini ---------------------------------

def medie_settimanali_vicini(record, inizio, fine):
    per_comune = defaultdict(list)
    for r in record:
        if inizio <= r["_dt"] < fine:
            for nome, aqi in (r.get("nearby") or {}).items():
                if aqi is not None:
                    per_comune[nome].append(aqi)
    medie = {nome: round(sum(v) / len(v)) for nome, v in per_comune.items()
            if v}
    return medie


# --- Grafico a barre ----------------------------------------------------------

def crea_grafico(stat_giorni, medie_vicini, media_solofra, out_path):
    W, H = 1080, 1000
    BG = (245, 247, 250)
    DARK = (35, 40, 46)
    GREY = (110, 118, 128)

    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 14], fill=(46, 158, 79))

    d.text((60, 50), "COME STA SOLOFRA", font=_font(36, bold=True), fill=GREY)
    d.text((60, 92), "Riepilogo della settimana", font=_font(26), fill=GREY)

    # --- area del grafico a barre ---
    area_x0, area_x1 = 110, W - 60
    area_y0, area_y1 = 170, 560
    area_h = area_y1 - area_y0
    scala_max = 160  # oltre questo valore la barra tocca il tetto dell'area

    # griglie di riferimento alle soglie di fascia
    for soglia, etichetta in [(50, "50"), (100, "100"), (150, "150")]:
        y = area_y1 - area_h * min(soglia, scala_max) / scala_max
        d.line([area_x0, y, area_x1, y], fill=(225, 229, 234), width=1)
        d.text((area_x0 - 10, y), etichetta, font=_font(18), fill=GREY,
              anchor="rm")

    n = 7
    bar_w = (area_x1 - area_x0) / n * 0.55
    step = (area_x1 - area_x0) / n
    for i in range(n):
        cx = area_x0 + step * i + step / 2
        stat = stat_giorni.get(i)
        label_giorno = GIORNI_SETT[i]
        if stat is None:
            d.text((cx, area_y1 + 14), label_giorno, font=_font(22),
                  fill=GREY, anchor="ma")
            d.text((cx, area_y1 - 10), "n/d", font=_font(18), fill=GREY,
                  anchor="mb")
            continue
        picco = stat["max"]
        b = banda(picco)
        h = area_h * min(picco, scala_max) / scala_max
        y0, y1 = area_y1 - h, area_y1
        d.rounded_rectangle([cx - bar_w / 2, y0, cx + bar_w / 2, y1],
                            radius=8, fill=b["color"])
        d.text((cx, y0 - 10), str(picco), font=_font(22, bold=True),
              fill=DARK, anchor="mb")
        d.text((cx, area_y1 + 14), label_giorno, font=_font(22, bold=True),
              fill=DARK, anchor="ma")

    y = area_y1 + 60
    d.line([60, y, W - 60, y], fill=(225, 229, 234), width=2)
    y += 30
    d.text((60, y), "MEDIA SETTIMANALE NELLA ZONA", font=_font(26, bold=True),
           fill=DARK)
    y += 46

    righe = [("Solofra", media_solofra)] + sorted(medie_vicini.items(),
                                                   key=lambda kv: kv[1])
    for nome, val in righe[:5]:
        bcol = banda(val)["color"]
        peso = "bold" if nome == "Solofra" else ""
        d.ellipse([64, y + 4, 88, y + 28], fill=bcol)
        d.text((104, y), nome, font=_font(28, bold=(nome == "Solofra")),
              fill=DARK)
        d.text((W - 60, y), str(val), font=_font(28, bold=True), fill=bcol,
              anchor="ra")
        y += 44

    y += 16
    d.line([60, y, W - 60, y], fill=(225, 229, 234), width=2)
    y += 24
    d.text((W / 2, y), "Le barre mostrano il picco (valore peggiore) di "
          "ogni giorno, non la media", font=_font(20), fill=GREY,
          anchor="ma")
    y += 30
    d.text((W / 2, y), "Fonte: ARPA Campania · World Air Quality Index Project",
          font=_font(20), fill=GREY, anchor="ma")
    y += 34

    img = img.crop((0, 0, W, y))
    img.save(out_path)
    return out_path


# --- Messaggio -----------------------------------------------------------

def build_caption(inizio, fine, stat_giorni, eventi, medie_vicini,
                  media_solofra):
    data_inizio = inizio.strftime("%d/%m")
    data_fine = (fine - timedelta(days=1)).strftime("%d/%m")

    righe = [f"\U0001F4C8 <b>Come sta Solofra</b> \u2014 riepilogo settimanale",
            f"{data_inizio} \u2013 {data_fine}", ""]

    if eventi:
        righe.append("<b>Cosa è successo:</b>")
        righe.extend(eventi)
    else:
        righe.append("Settimana stabile: aria sempre in fascia buona, "
                     "nessun evento da segnalare.")

    righe.append("")
    if medie_vicini:
        posizione = 1 + sum(1 for v in medie_vicini.values()
                            if v < media_solofra)
        totale = len(medie_vicini) + 1
        if posizione == 1:
            righe.append(f"\U0001F3C6 Solofra ha avuto l'aria più pulita "
                         f"della zona questa settimana (media {media_solofra}).")
        else:
            righe.append(f"\U0001F4CD Solofra: {posizione}\u00aa su {totale} "
                         f"nella zona questa settimana (media {media_solofra}).")

    giorni_con_dati = sum(1 for v in stat_giorni.values() if v)
    if giorni_con_dati < 7:
        righe.append("")
        righe.append(f"<i>Nota: dati disponibili per {giorni_con_dati} "
                     f"giorni su 7 questa settimana.</i>")

    return "\n".join(righe)


def send_photo(path, caption):
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto"
    with open(path, "rb") as f:
        resp = requests.post(url,
                             data={"chat_id": TG_CHAT, "caption": caption,
                                   "parse_mode": "HTML"},
                             files={"photo": f}, timeout=60)
    if not resp.ok:
        sys.exit(f"Errore invio Telegram ({resp.status_code}): {resp.text}")
    print("Riepilogo settimanale inviato correttamente.")


def main():
    check_env()
    record = carica_storico()
    inizio, fine = finestra_settimana_scorsa()

    per_giorno = aggrega_per_giorno(record, inizio, fine)
    stat_giorni = {i: statistiche_giorno(v) for i, v in per_giorno.items()}

    valori_settimana = [r["aqi"] for r in record
                        if inizio <= r["_dt"] < fine and r.get("aqi")]
    if not valori_settimana:
        print("Nessun dato per la settimana scorsa: nessun riepilogo da "
             "pubblicare (probabilmente il logging è appena partito).")
        return
    media_solofra = round(sum(valori_settimana) / len(valori_settimana))

    eventi = rileva_eventi(record, inizio, fine)
    medie_vicini = medie_settimanali_vicini(record, inizio, fine)

    caption = build_caption(inizio, fine, stat_giorni, eventi, medie_vicini,
                            media_solofra)
    path = crea_grafico(stat_giorni, medie_vicini, media_solofra,
                        "riepilogo.png")

    print("--- Anteprima didascalia ---")
    print(caption)
    print("----------------------------")
    send_photo(path, caption)


if __name__ == "__main__":
    main()
