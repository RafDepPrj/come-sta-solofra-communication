#!/usr/bin/env python3
"""
Come Sta Solofra — promemoria raccolta differenziata.

A differenza del bollettino aria (che tace quando non cambia nulla), qui la
logica è opposta: il dato utile CAMBIA ogni giorno, quindi il promemoria
esce ogni sera — è la costanza quotidiana a essere il servizio.

Calendario fisso (utenze domestiche, fonte: Comune di Solofra / app "La
settimana", verificato il 12/07/2026):
  Lunedì    -> Indifferenziato
  Martedì   -> Plastica e Alluminio
  Mercoledì -> Umido (Organico)
  Giovedì   -> Carta e Cartone
  Venerdì   -> Umido (Organico) [sera] + Vetro [mattina, solo 1a/3a/5a
               settimana del mese, esposizione entro le 12:00 — quindi ha
               orario e giorno-tipo diversi da tutto il resto]
  Sabato    -> nessuna raccolta
  Domenica  -> Umido (Organico)

I pannolini/pannoloni del giovedì sono un servizio SU REGISTRAZIONE, non
per tutti i cittadini: il promemoria del giovedì include una riga separata
e chiaramente etichettata "se sei iscritto...", così chi non è registrato
non si confonde e chi lo è non se lo dimentica.

⚠️ Il calendario è cambiato almeno una volta (1° luglio 2025). Se il Comune
lo aggiorna di nuovo, va corretto qui a mano — non c'è nessuno scraping
automatico, il calendario è scritto nel codice apposta perché è un dato
fisso, non ce n'è uno "live" da controllare.

Lo script gira due volte al giorno (vedi workflow):
  - la sera (~19:30 Roma): promemoria per l'esposizione di stasera
  - il venerdì mattina (~8:00 Roma): promemoria vetro, solo se è la
    settimana giusta

Il servizio è dotato di un INTERRUTTORE (vedi funzione `abilitato()`): finché
non viene attivato esplicitamente, lo script si limita a controllare ed
uscire senza mandare nulla — così puoi caricare questi file oggi e
accendere il servizio quando vorrai, senza dover più toccare il codice.

Variabili d'ambiente (riusa gli stessi Secrets del bollettino aria):
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
Variabile di repository (non segreta, per accendere/spegnere il servizio):
  DIFFERENZIATA_ABILITATA = "true" per attivarlo (qualsiasi altro valore,
  o l'assenza della variabile, lo tiene spento)
"""

import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID")


def abilitato():
    """Interruttore del servizio. Finché la variabile di repository
    DIFFERENZIATA_ABILITATA non vale esattamente "true", lo script non
    manda nulla — anche se il workflow gira regolarmente. Serve a poter
    caricare tutto ora e accendere il servizio più avanti, cambiando un
    solo valore su GitHub invece di ricaricare file."""
    return os.environ.get("DIFFERENZIATA_ABILITATA", "").strip().lower() \
        == "true"

# Lun=0 ... Dom=6 (standard Python weekday())
MATERIALE_SERALE = {
    0: ("\U0001F5D1\ufe0f", "Indifferenziato"),
    1: ("\u267b\ufe0f", "Plastica e Alluminio"),
    2: ("\U0001F955", "Umido (Organico)"),
    3: ("\U0001F4E6", "Carta e Cartone"),
    4: ("\U0001F955", "Umido (Organico)"),  # venerdì sera: solo organico
    5: None,                                # sabato: nessuna raccolta
    6: ("\U0001F955", "Umido (Organico)"),
}


def check_env():
    for name, val in [("TELEGRAM_BOT_TOKEN", TG_TOKEN),
                      ("TELEGRAM_CHAT_ID", TG_CHAT)]:
        if not val:
            sys.exit(f"Errore: variabile d'ambiente mancante: {name}")


def settimana_del_mese(data):
    """1 se e' il primo venerdi' del mese, 2 il secondo, ecc."""
    primo_del_mese = data.replace(day=1)
    conteggio = 0
    giorno = primo_del_mese
    while giorno <= data:
        if giorno.weekday() == data.weekday():
            conteggio += 1
        giorno += timedelta(days=1)
    return conteggio


def send_message(text):
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    resp = requests.post(url, json={
        "chat_id": TG_CHAT, "text": text, "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }, timeout=30)
    if not resp.ok:
        sys.exit(f"Errore invio Telegram ({resp.status_code}): {resp.text}")
    print("Promemoria inviato correttamente.")


# --- Le due modalità -------------------------------------------------------

def promemoria_serale(oggi):
    voce = MATERIALE_SERALE[oggi.weekday()]
    if voce is None:
        testo = ("\u23F8\ufe0f <b>Come sta Solofra</b>\n\n"
                "Sabato: nessuna raccolta prevista.")
    else:
        icona, materiale = voce
        testo = (f"{icona} <b>Come sta Solofra</b>\n\n"
                f"Stasera metti fuori: <b>{materiale}</b>\n"
                f"Esposizione dalle 22:00 alle 23:00.")
        if oggi.weekday() == 3:  # giovedì: nota pannolini per gli iscritti
            testo += ("\n\n\U0001F476 Se sei iscritto al servizio "
                     "pannolini/pannoloni, oggi tocca anche a te.")
    print("--- Anteprima (sera) ---")
    print(testo)
    print("------------------------")
    send_message(testo)


def promemoria_vetro_venerdi(oggi):
    if oggi.weekday() != 4:  # non e' venerdi': non succede nulla
        print("Non è venerdì: nessun controllo vetro da fare.")
        return
    settimana = settimana_del_mese(oggi)
    if settimana not in (1, 3, 5):
        print(f"Venerdì n.{settimana} del mese: non è settimana del vetro, "
             f"nessun messaggio.")
        return
    testo = ("\U0001F37E <b>Come sta Solofra</b>\n\n"
            "Oggi è giornata di vetro: mettilo fuori entro le 12:00.")
    print("--- Anteprima (mattina venerdì) ---")
    print(testo)
    print("------------------------------------")
    send_message(testo)


def main():
    if not abilitato():
        print("Servizio non ancora abilitato (DIFFERENZIATA_ABILITATA "
             "!= 'true'): nessun messaggio inviato. Per accenderlo, "
             "imposta quella variabile di repository su 'true'.")
        return

    check_env()
    ora = datetime.now(ZoneInfo("Europe/Rome"))
    if ora.hour < 12:
        promemoria_vetro_venerdi(ora.date())
    else:
        promemoria_serale(ora.date())


if __name__ == "__main__":
    main()
