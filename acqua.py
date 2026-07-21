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

Oltre all'esito, il messaggio riporta sempre quattro parametri "chiave" in
forma AGGREGATA su tutti i punti (non ripetuti punto per punto, per non
fare overload): esito batteriologico, durezza, solventi industriali
tetracloroetilene/tricloroetilene, nitrati. Ogni riga porta già con sé la
sua spiegazione in linguaggio semplice (a cosa serve, perché conta) —
niente riga didattica separata, sarebbe di nuovo la stessa ridondanza che
volevamo evitare.

I solventi non vengono mai omessi anche quando il valore è irrilevante,
perché sono stati storicamente un tema sensibile per l'acqua di Solofra —
il silenzio su questo parametro specifico sarebbe più sospetto che
informativo. Il D.Lgs. 18/2023 fissa un limite sulla SOMMA dei due (non su
ognuno singolarmente), e non tutti i referti includono questo pannello
analitico (vedi `_estrai_solventi`) — mai extrapolato quando assente.

Il messaggio cita anche il punto con più margine dai limiti di legge
("il migliore"): sempre, perché una notizia positiva non fa mai danno
anche quando il margine è risicato — ma se il divario con gli altri punti
è sotto una soglia minima (`SOGLIA_DIVARIO`), lo dice esplicitamente
("è rumore statistico, non un giudizio di qualità") invece di lasciar
intendere una differenza che non esiste. Il punto "peggiore" viene invece
nominato esplicitamente solo se si avvicina davvero a un limite
(`SOGLIA_RILEVANTE`) — nominarlo sotto quella soglia allarmerebbe senza
motivo.

I numeri riportati nel messaggio sono sempre
letti dal testo del referto, mai scritti a mano.

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


def _num(s):
    """'0,6' o '0.6' -> 0.6"""
    return float(s.replace(",", "."))


def _estrai_durezza(testo_una_riga):
    """°F, parametro solo informativo: il D.Lgs. 18/2023 non fissa un
    limite per la durezza (per questo la riga finisce con '-')."""
    m = re.search(r"Durezza[^-]*?°F\s+\d+\s+(\d+)", testo_una_riga)
    return int(m.group(1)) if m else None


def _estrai_solventi(testo_una_riga):
    """Tetracloroetilene + Tricloroetilene: il D.Lgs. 18/2023 fissa un
    limite sulla SOMMA dei due (effetti considerati cumulativi), non su
    ognuno singolarmente — per questo il referto ha una riga dedicata a
    parte con la somma, ed è quella che leggiamo. Non tutti i referti
    includono questo pannello (vedi docstring del modulo): None se assente,
    mai un valore indovinato."""
    m = re.search(
        r"Tricloroetilene e Tetracloroetilene.*?µg/L\s+([\d.,]+)\s+"
        r"(<\s*LdQ|[\d.,]+)\s+(\d+)",
        testo_una_riga)
    if not m:
        return None
    ldq, valore_grezzo, limite = m.groups()
    sotto_ldq = "ldq" in valore_grezzo.lower().replace(" ", "")
    return {
        "ldq": _num(ldq),
        "limite": int(limite),
        "sotto_ldq": sotto_ldq,
        "valore": None if sotto_ldq else _num(valore_grezzo),
    }


def _estrai_nitrato(testo_una_riga):
    """mg/L NO3, limite di legge 50 — parametro rilevante in particolare
    per la preparazione di alimenti per lattanti sotto i 6 mesi."""
    m = re.search(r"Nitrato.*?mg/L\s*NO\s*3\s+([\d.,]+)\s+([\d.,]+)\s+(\d+)",
                 testo_una_riga)
    if not m:
        return None
    return {"valore": _num(m.group(2)), "limite": int(m.group(3))}


def _estrai_conta(testo_una_riga, etichetta):
    """UFC/100ml, il limite di legge per tutti e tre i parametri
    batteriologici è 0 — qualunque conteggio > 0 è già un'anomalia."""
    m = re.search(rf"{etichetta}.*?Ufc\s*/\s*100ml\s*-\s*(\d+)\s+(\d+)",
                 testo_una_riga)
    return int(m.group(1)) if m else None


def _estrai_batteriologico(testo_una_riga):
    """Coliformi, E. Coli, enterococchi: None se anche solo uno dei tre
    manca dal referto, per non mostrare un 'tutto pulito' basato su un
    conteggio parziale."""
    coliformi = _estrai_conta(testo_una_riga, "batteri coliformi")
    ecoli = _estrai_conta(testo_una_riga, "Escherichia Coli")
    entero = _estrai_conta(testo_una_riga, "Enterococchi intestinali")
    if coliformi is None or ecoli is None or entero is None:
        return None
    return {"coliformi": coliformi, "ecoli": ecoli, "entero": entero}


def analizza_pdf(path):
    """Estrae luogo, punto di prelievo, esito, durezza, solventi
    (tetracloroetilene/tricloroetilene), nitrati e batteriologico da un
    singolo referto. 'conforme' è True/False solo se il testo lo dichiara
    esplicitamente; None se non troviamo la frase attesa — mai indovinato
    dai valori dei singoli parametri (troppo eterogenei tra loro per un
    confronto generico e affidabile)."""
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
        "durezza": _estrai_durezza(testo_una_riga),
        "solventi": _estrai_solventi(testo_una_riga),
        "nitrato": _estrai_nitrato(testo_una_riga),
        "batteriologico": _estrai_batteriologico(testo_una_riga),
    }


# --- Messaggio ----------------------------------------------------------

def descrizione(r):
    if r["luogo"] and r["punto"]:
        return f"{r['luogo']} — {r['punto']}"
    return r["luogo"] or r["punto"] or r["file"]


def _riga_durezza(risultati):
    valori = [r["durezza"] for r in risultati if r["durezza"] is not None]
    if not valori:
        return None
    if min(valori) == max(valori):
        gamma = f"{valori[0]} °F in tutti i punti"
    else:
        gamma = f"da {min(valori)} a {max(valori)} °F a seconda del punto"
    return (f"\U0001F4A7 Durezza: {gamma} (nessun limite di legge per "
           f"questo parametro, è solo informativo — utile per "
           f"lavatrice/caldaia)")


def _riga_solventi(risultati):
    testati = [r for r in risultati if r["solventi"] is not None]
    if not testati:
        return None
    totale = len(risultati)
    limite = testati[0]["solventi"]["limite"]
    non_sotto_ldq = [r for r in testati if not r["solventi"]["sotto_ldq"]]

    if not non_sotto_ldq:
        ldq = testati[0]["solventi"]["ldq"]
        return (f"\U0001F9EA Solventi industriali (tetracloroetilene/"
               f"tricloroetilene): presenza quasi impercettibile — sotto "
               f"la soglia minima che il laboratorio riesce a misurare — "
               f"nei {len(testati)}/{totale} punti dove viene testato. Il "
               f"limite di legge è {limite} µg/L, qui il laboratorio non "
               f"ne rileva nemmeno {ldq:g}.")

    picco = max(r["solventi"]["valore"] for r in non_sotto_ldq)
    return (f"\U0001F9EA Solventi industriali (tetracloroetilene/"
           f"tricloroetilene): rilevati (non solo tracce) in "
           f"{len(non_sotto_ldq)}/{len(testati)} punti testati, valore "
           f"più alto {picco:g} µg/L (limite di legge {limite} µg/L).")


def _riga_batteriologico(risultati):
    testati = [r for r in risultati if r["batteriologico"] is not None]
    if not testati:
        return None
    totale = len(risultati)
    positivi = [r for r in testati
               if any(r["batteriologico"].values())]
    if not positivi:
        return (f"\U0001F9A0 Batteriologico: 0 batteri rilevati "
               f"(coliformi, E. Coli, enterococchi) in tutti i "
               f"{len(testati)}/{totale} punti testati — il test più "
               f"importante per la sicurezza immediata dell'acqua.")
    return (f"\U0001F9A0 Batteriologico: batteri rilevati in "
           f"{len(positivi)}/{len(testati)} punti testati (coliformi, "
           f"E. Coli o enterococchi) — vedi dettaglio punti non "
           f"conformi sopra.")


def _riga_nitrati(risultati):
    """Il D.Lgs. 18/2023 fissa lo stesso limite (50 mg/L) usato anche come
    riferimento per l'acqua destinata alla preparazione di alimenti per
    lattanti — lo citiamo come contesto sempre vero. La rassicurazione
    "ben al di sotto" invece la aggiungiamo SOLO se il valore più alto
    trovato è davvero basso rispetto al limite: altrimenti sarebbe
    un'affermazione fissa scollegata dal dato reale di questo controllo."""
    valori = [r["nitrato"]["valore"] for r in risultati
             if r["nitrato"] is not None]
    if not valori:
        return None
    limite = next(r["nitrato"]["limite"] for r in risultati
                 if r["nitrato"] is not None)
    if min(valori) == max(valori):
        gamma = f"{valori[0]:g} mg/L in tutti i punti"
    else:
        gamma = f"da {min(valori):g} a {max(valori):g} mg/L a seconda del punto"

    riga = (f"\U0001F37C Nitrati: {gamma} (limite di legge: {limite:g} "
           f"mg/L — è anche il riferimento usato per l'acqua destinata "
           f"alla preparazione del latte artificiale ai lattanti)")
    if max(valori) / limite < 0.5:
        riga += ", qui ben al di sotto."
    return riga


SOGLIA_RILEVANTE = 0.20   # 20% di un limite: sotto, è rumore statistico
SOGLIA_DIVARIO = 0.10     # differenza minima tra migliore e peggiore


def _margine(r):
    """Rapporto valore/limite più alto tra i parametri numerici disponibili
    per questo punto (0-1, più basso = più margine dai limiti). None se
    non c'è nessun dato numerico utile per questo punto."""
    rapporti = []
    if r["nitrato"] is not None:
        rapporti.append(r["nitrato"]["valore"] / r["nitrato"]["limite"])
    if r["solventi"] is not None and not r["solventi"]["sotto_ldq"]:
        rapporti.append(r["solventi"]["valore"] / r["solventi"]["limite"])
    return max(rapporti) if rapporti else None


def _nomi_punti(righe_risultati, limite=3):
    nomi = [r["luogo"] or r["file"] for r in righe_risultati]
    if len(nomi) <= limite:
        return ", ".join(nomi)
    return ", ".join(nomi[:limite]) + f" e altri {len(nomi) - limite}"


def _riga_margine(risultati):
    """'Il migliore' è sempre citato (non fa mai danno dirlo); 'il
    peggiore' solo se il divario è abbastanza ampio da non essere rumore
    statistico — altrimenti diremmo che un punto è 'peggiore' per una
    differenza che non significa niente."""
    con_margine = [(r, _margine(r)) for r in risultati]
    con_margine = [(r, m) for r, m in con_margine if m is not None]
    if not con_margine:
        return None

    m_min = min(m for _, m in con_margine)
    m_max = max(m for _, m in con_margine)
    migliori = [r for r, m in con_margine if m == m_min]
    nomi_migliori = _nomi_punti(migliori)

    if m_max >= SOGLIA_RILEVANTE and (m_max - m_min) >= SOGLIA_DIVARIO:
        peggiori = [r for r, m in con_margine if m == m_max]
        return (f"⭐ Punto con più margine dai limiti: {nomi_migliori}. "
               f"Punto da tenere d'occhio: {_nomi_punti(peggiori)} "
               f"(al {m_max * 100:.0f}% di un limite di legge).")

    return (f"⭐ Punto con più margine dai limiti: {nomi_migliori}. Il "
           f"distacco dagli altri punti è minimo — normale variazione "
           f"tra un prelievo e l'altro, non un giudizio di qualità.")


def build_message(data_archivio, risultati):
    data_it = data_archivio.strftime("%d/%m/%Y")
    totale = len(risultati)
    non_conformi = [r for r in risultati if r["conforme"] is False]
    non_determinati = [r for r in risultati if r["conforme"] is None]
    conformi = totale - len(non_conformi) - len(non_determinati)
    tutto_ok = not non_conformi and not non_determinati

    icona = "✅" if tutto_ok else ("\U0001F534" if non_conformi
                                  else "\U0001F7E1")
    righe = [
        f"\U0001F4A7 <b>Come sta Solofra</b> — acqua potabile",
        f"Controllo del {data_it}",
        "",
    ]

    if tutto_ok:
        righe.append(f"{icona} Tutti i {totale} punti di prelievo "
                     f"controllati sono conformi al D.Lgs. 18/2023.")
    else:
        righe.append(f"{icona} {conformi}/{totale} punti di prelievo "
                     f"conformi al D.Lgs. 18/2023.")
        if non_conformi:
            righe.append("")
            righe.append(f"⚠️ <b>NON conformi ({len(non_conformi)}):</b>")
            for r in non_conformi:
                righe.append(f"• {descrizione(r)}")
        if non_determinati:
            righe.append("")
            righe.append(f"❓ <b>Referti non leggibili in automatico "
                         f"({len(non_determinati)}), da controllare a "
                         f"mano:</b>")
            for r in non_determinati:
                righe.append(f"• {descrizione(r)} ({r['file']})")

    blocchi = (
        _riga_batteriologico(risultati),
        _riga_durezza(risultati),
        _riga_solventi(risultati),
        _riga_nitrati(risultati),
        _riga_margine(risultati),
    )
    for riga in blocchi:
        if riga:
            righe.append("")
            righe.append(riga)

    righe.append("")
    righe.append(f"Fonte: rapporti di un laboratorio accreditato terzo, "
                 f"referti del {data_it}.")
    righe.append(f"⚠️ <i>Servizio gratuito e non ufficiale, può contenere "
                 f"errori di lettura automatica dei referti. Dati "
                 f"ufficiali e referti completi: {PAGINA_URL}</i>")
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

    # FORZA_CONTROLLO=true salta il controllo "è già stato processato?" e
    # ripubblica l'archivio più recente anche se è lo stesso di prima —
    # serve per verificare a mano un cambio di formato del messaggio senza
    # dover aspettare che esca un archivio realmente nuovo sul sito (vedi
    # bollettino.py per lo stesso pattern sull'aria).
    forza = os.environ.get("FORZA_CONTROLLO", "").strip().lower() == "true"

    html = fetch_pagina()
    trovato = trova_ultimo_archivio(html)
    if trovato is None:
        sys.exit("Errore: nessun link ad un archivio trovato nella pagina. "
                 "La struttura della pagina potrebbe essere cambiata.")
    ultima_data, url = trovato
    print(f"Ultimo archivio trovato sul sito: {ultima_data.isoformat()} "
         f"({url})")

    gia_processato = leggi_stato()
    if not forza and gia_processato is not None \
            and ultima_data <= gia_processato:
        print(f"Nessun archivio nuovo (ultimo processato: "
             f"{gia_processato.isoformat()}). Nessun messaggio inviato. "
             f"(Imposta FORZA_CONTROLLO=true per ripubblicarlo comunque "
             f"in un lancio manuale.)")
        return
    if forza:
        print("FORZA_CONTROLLO attivo: ripubblico l'ultimo archivio anche "
             "se già processato in precedenza.")

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
