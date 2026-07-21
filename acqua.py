#!/usr/bin/env python3
"""
Come Sta Solofra — controllo qualità dell'acqua potabile.

Fonte: Solofra Servizi S.p.A. (https://www.solofraservizi.it/la-tua-acqua/),
che pubblica periodicamente un archivio RAR con i rapporti di prova (PDF) di
un laboratorio accreditato, uno per ogni punto di prelievo della rete idrica
(pozzi, sorgenti, serbatoi, fontanini pubblici).

A differenza delle altre fonti (WAQI, INGV) qui NON c'è un'API: bisogna far
scraping della pagina per trovare il link all'ultimo archivio
("Certificati del DD-MM-YYYY.rar"), scaricarlo, estrarlo (richiede il
comando di sistema `unrar`, non incluso in Python) e leggere il testo di
ogni PDF per capire se il campione è dichiarato conforme al D.Lgs. 18/2023.

Logica del messaggio (deliberatamente asimmetrica, come bollettino.py):
- se TUTTI i punti sono conformi -> un solo messaggio breve di conferma;
- se anche un solo punto NON è conforme, o un referto non si riesce a
  leggere automaticamente -> messaggio con il dettaglio di cosa e dove,
  perché un problema sull'acqua potabile non va mai nascosto in un numero
  aggregato.
Non proviamo mai a dedurre un esito quando il testo non è chiaro: meglio
segnalare "referto da controllare a mano" che sbagliare in un senso o
nell'altro su un dato sanitario.

Il servizio è dotato dello stesso selettore a TRE STATI degli altri servizi
"delicati" (vedi funzione `ambiente()`):
  - "spento"     -> nessun messaggio, da nessuna parte
  - "test"       -> messaggi mandati solo sul canale privato di prova
  - "produzione" -> messaggi mandati sul canale vero, con gli iscritti
Il valore di default (variabile assente o vuota) è "test". Qualsiasi valore
non riconosciuto viene trattato come "spento", per sicurezza.

Variabili d'ambiente (riusa gli stessi Secrets del bollettino aria):
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_CHAT_ID_TEST
Variabile di repository (non segreta, il selettore a tre stati):
  ACQUA_AMBIENTE = "spento" | "test" | "produzione"

Dipendenza di sistema: il comando `unrar` deve essere installato sul runner
(vedi .github/workflows/acqua.yml — `apt-get install unrar`). Senza, lo
script si ferma con un errore chiaro invece di fallire in modo oscuro.
"""

import os
import re
import sys
import json
import subprocess
import tempfile
from datetime import date, datetime
from glob import glob
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from pypdf import PdfReader

PAGINA_URL = "https://www.solofraservizi.it/la-tua-acqua/"
BASE_URL = "https://www.solofraservizi.it"

STATO_PATH = "stato_acqua.json"

TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TG_CHAT_PRODUZIONE = os.environ.get("TELEGRAM_CHAT_ID")
TG_CHAT_TEST = os.environ.get("TELEGRAM_CHAT_ID_TEST")


def ambiente():
    grezzo = os.environ.get("ACQUA_AMBIENTE", "").strip().lower()
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


# --- Individuazione dell'ultimo archivio ------------------------------------

ARCHIVIO_RE = re.compile(
    r'href="(/doc/analisi/Certificati%20del%20'
    r'(\d{2})-(\d{2})-(\d{4})\.rar)"'
)


def trova_ultimo_archivio(html):
    """Cerca tutti i link agli archivi nella pagina e ritorna (data, url)
    di quello più recente. None se non trova nessun link riconoscibile
    (es. la pagina ha cambiato struttura) — meglio fermarsi con un errore
    chiaro che indovinare."""
    trovati = []
    for m in ARCHIVIO_RE.finditer(html):
        path, gg, mm, aaaa = m.groups()
        try:
            d = date(int(aaaa), int(mm), int(gg))
        except ValueError:
            continue
        trovati.append((d, BASE_URL + path))
    if not trovati:
        return None
    return max(trovati, key=lambda t: t[0])


def fetch_pagina():
    resp = requests.get(PAGINA_URL, timeout=30)
    resp.raise_for_status()
    return resp.text


# --- Stato: ultimo archivio già processato -----------------------------------

def leggi_stato():
    try:
        with open(STATO_PATH, encoding="utf-8") as f:
            s = json.load(f)
        return date.fromisoformat(s["ultima_data"])
    except (FileNotFoundError, ValueError, OSError, KeyError):
        return None


def salva_stato(ultima_data):
    ora = datetime.now(ZoneInfo("Europe/Rome")).isoformat(timespec="minutes")
    with open(STATO_PATH, "w", encoding="utf-8") as f:
        json.dump({"ultima_data": ultima_data.isoformat(), "aggiornato": ora}, f)


# --- Download, estrazione RAR e parsing dei PDF ------------------------------

def scarica_ed_estrai_analizza(url):
    """Scarica l'archivio, lo estrae con `unrar` ed analizza ogni PDF.
    Tutto dentro una directory temporanea che si autodistrugge: non
    lasciamo mai i referti (dati di terzi) sul filesystem del runner più
    del necessario."""
    with tempfile.TemporaryDirectory() as tmp:
        rar_path = Path(tmp) / "archivio.rar"
        with requests.get(url, timeout=60, stream=True) as resp:
            resp.raise_for_status()
            with open(rar_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)

        estratti_dir = Path(tmp) / "estratti"
        estratti_dir.mkdir()
        try:
            subprocess.run(
                ["unrar", "x", "-o+", str(rar_path), str(estratti_dir) + "/"],
                check=True, capture_output=True, text=True, timeout=120,
            )
        except FileNotFoundError:
            sys.exit("Errore: comando 'unrar' non trovato sul sistema. "
                     "Va installato nel workflow (apt-get install unrar).")
        except subprocess.CalledProcessError as e:
            sys.exit(f"Errore nell'estrazione dell'archivio RAR: {e.stderr}")
        except subprocess.TimeoutExpired:
            sys.exit("Errore: estrazione dell'archivio RAR troppo lenta "
                     "(timeout).")

        pdf_paths = sorted(glob(str(estratti_dir / "*.pdf")))
        if not pdf_paths:
            sys.exit("Errore: l'archivio non contiene nessun PDF. "
                     "Il formato pubblicato potrebbe essere cambiato.")

        return [analizza_pdf(p) for p in pdf_paths]


def _normalizza(testo):
    """Rimuove tutti gli spazi/a-capo: il testo estratto da questi PDF ha
    spazi spuri infilati a caso in mezzo alle parole (es. 'confo rme'),
    probabilmente un artefatto del generatore del PDF stesso — capita in
    punti diversi da un referto all'altro, quindi l'unico modo affidabile
    di cercare una frase è confrontarla senza spazi."""
    return re.sub(r"\s+", "", testo)


def analizza_pdf(path):
    """Estrae luogo, punto di prelievo ed esito da un singolo referto.
    'conforme' è True/False solo se il testo lo dichiara esplicitamente;
    None se non troviamo la frase attesa — mai indovinato dai valori dei
    singoli parametri (troppo eterogenei tra loro per un confronto
    generico e affidabile)."""
    testo = "\n".join(p.extract_text() or "" for p in PdfReader(path).pages)
    testo_una_riga = re.sub(r"\s+", " ", testo)
    norm = _normalizza(testo)

    m_luogo = re.search(
        r"Luogo di prelievo:\s*1?\s*(.+?)\s+Punto di prelievo",
        testo_una_riga)
    m_punto = re.search(r"Punto di prelievo:\s*1?\s*(.+?)\s{2}", testo)
    m_esito = re.search(r"risulta(nonconforme|conforme)aquantoprevisto",
                        norm, re.IGNORECASE)

    if m_esito is None:
        conforme = None
    else:
        conforme = m_esito.group(1).lower() == "conforme"

    return {
        "file": Path(path).name,
        "luogo": m_luogo.group(1).strip() if m_luogo else None,
        "punto": m_punto.group(1).strip() if m_punto else None,
        "conforme": conforme,
    }


# --- Messaggio ----------------------------------------------------------

def descrizione(r):
    if r["luogo"] and r["punto"]:
        return f"{r['luogo']} — {r['punto']}"
    return r["luogo"] or r["punto"] or r["file"]


def build_message(data_archivio, risultati):
    data_it = data_archivio.strftime("%d/%m/%Y")
    totale = len(risultati)
    non_conformi = [r for r in risultati if r["conforme"] is False]
    non_determinati = [r for r in risultati if r["conforme"] is None]
    conformi = totale - len(non_conformi) - len(non_determinati)

    tutto_ok = not non_conformi and not non_determinati

    if tutto_ok:
        righe = [
            f"\U0001F4A7 <b>Come sta Solofra</b> — acqua potabile",
            f"Controllo del {data_it}",
            "",
            f"✅ Tutti i {totale} punti di prelievo controllati sono "
            f"conformi al D.Lgs. 18/2023.",
        ]
    else:
        icona = "\U0001F534" if non_conformi else "\U0001F7E1"
        righe = [
            f"{icona} <b>Come sta Solofra</b> — acqua potabile",
            f"Controllo del {data_it}",
            "",
            f"{conformi}/{totale} punti di prelievo conformi al D.Lgs. "
            f"18/2023.",
        ]
        if non_conformi:
            righe.append("")
            righe.append(f"⚠️ <b>NON conformi "
                         f"({len(non_conformi)}):</b>")
            for r in non_conformi:
                righe.append(f"• {descrizione(r)}")
        if non_determinati:
            righe.append("")
            righe.append(f"❓ <b>Referti non leggibili in automatico "
                         f"({len(non_determinati)}), da controllare a "
                         f"mano:</b>")
            for r in non_determinati:
                righe.append(f"• {descrizione(r)} ({r['file']})")

    righe.append("")
    righe.append(f"Referti completi: {PAGINA_URL}")
    righe.append("Fonte: Solofra Servizi S.p.A. — laboratorio "
                 "accreditato terzo.")
    righe.append("<i>Non è un canale ufficiale: per informazioni e "
                 "reclami sull'erogazione fai riferimento a Solofra "
                 "Servizi S.p.A.</i>")
    return "\n".join(righe)


def send_message(text, chat_id):
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    resp = requests.post(url, json={
        "chat_id": chat_id, "text": text, "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }, timeout=30)
    if not resp.ok:
        sys.exit(f"Errore invio Telegram ({resp.status_code}): {resp.text}")
    print("Messaggio acqua inviato correttamente.")


def main():
    amb = ambiente()
    if amb == "spento":
        print("Ambiente 'spento' (o valore non riconosciuto in "
             "ACQUA_AMBIENTE): nessun controllo inviato.")
        return

    check_env(amb)
    chat_id = chat_destinazione(amb)
    print(f"Ambiente attivo: {amb.upper()}")

    html = fetch_pagina()
    trovato = trova_ultimo_archivio(html)
    if trovato is None:
        sys.exit("Errore: nessun link ad un archivio trovato nella pagina. "
                 "La struttura della pagina potrebbe essere cambiata.")
    ultima_data, url = trovato
    print(f"Ultimo archivio trovato sul sito: {ultima_data.isoformat()} "
         f"({url})")

    gia_processato = leggi_stato()
    if gia_processato is not None and ultima_data <= gia_processato:
        print(f"Nessun archivio nuovo (ultimo processato: "
             f"{gia_processato.isoformat()}). Nessun messaggio inviato.")
        return

    risultati = scarica_ed_estrai_analizza(url)
    print(f"Analizzati {len(risultati)} referti.")
    for r in risultati:
        print(f"  - {r['file']}: conforme={r['conforme']} "
             f"({descrizione(r)})")

    msg = build_message(ultima_data, risultati)
    print("--- Anteprima ---")
    print(msg)
    print("-----------------")
    send_message(msg, chat_id)

    salva_stato(ultima_data)


if __name__ == "__main__":
    main()
