# Come Sta Solofra — bollettini automatici su Telegram

Pubblica in automatico, a costo zero, su un canale Telegram (`t.me/comestasolofra`)
**cinque servizi indipendenti**: qualità dell'aria, riepilogo settimanale
dell'aria, promemoria raccolta differenziata, avvisi sismici, controllo
qualità dell'acqua potabile.

Gira interamente su **GitHub Actions**: nessun server da mantenere, i token
restano nascosti nei *Secrets*.

## I cinque servizi

| Script | Workflow | Cosa fa | Cadenza reale |
|---|---|---|---|
| `bollettino.py` | `.github/workflows/aria.yml` | Card AQI + confronto comuni vicini | Trigger ogni 20 min, **ma pubblica solo quando cambia fascia** (min. 4h tra un post e l'altro) o dopo 7 giorni di silenzio |
| `settimanale.py` | `.github/workflows/riepilogo-settimanale.yml` | Grafico a barre della settimana appena conclusa | Ogni lunedì alle 6:30 UTC, sempre (anche se piatta) |
| `differenziata.py` | `.github/workflows/differenziata.yml` | Promemoria cosa esporre stasera + vetro il venerdì | Ogni sera alle 17:30 UTC + venerdì mattina 6:00 UTC (solo 1ª/3ª/5ª settimana del mese) |
| `sismico.py` | `.github/workflows/sismico.yml` | Avviso se un evento sismico INGV supera la soglia di rilevanza | Trigger ogni 15 min, **ma pubblica solo se un evento nuovo supera la soglia** |
| `acqua.py` | `.github/workflows/acqua.yml` | Controllo conformità analisi acqua potabile (Solofra Servizi) | Trigger una volta al giorno, **ma pubblica solo quando esce un nuovo archivio** sul sito |

Punto importante per `aria.yml` e `sismico.yml`: il **cron gira molto più
spesso di quanto pubblichino**. GitHub dichiara i trigger `schedule` "best
effort" (possono ritardare o essere saltati silenziosamente), quindi questi
due workflow interrogano frequentemente ma è **lo script Python**, non il
cron, a decidere se è davvero il momento di pubblicare — leggendo lo stato
salvato nel repository. Un trigger saltato da GitHub non lascia più un buco:
il tentativo successivo arriva pochi minuti dopo.

## File

- `bollettino.py` — scarica i dati WAQI, cerca i comuni vicini, decide se
  pubblicare, genera l'immagine e la invia.
- `settimanale.py` — legge `storico.json` (scritto da `bollettino.py` ad
  ogni controllo, anche quando non pubblica) e genera il riepilogo del
  lunedì. Riusa `banda()`/`to_int()`/`_font()`/`_wrap()` da `bollettino.py`.
- `differenziata.py` — calendario fisso della raccolta, scritto nel codice
  (nessuno scraping automatico dal Comune).
- `sismico.py` — interroga il catalogo pubblico INGV (ISIDe, servizio FDSN),
  filtra per distanza/magnitudo, deduplica con `stato_sismico.json`.
- `acqua.py` — fa scraping di `solofraservizi.it` per trovare l'ultimo
  archivio RAR di analisi, lo estrae (richiede `unrar`) e legge il testo di
  ogni PDF per determinare l'esito di conformità, deduplica con
  `stato_acqua.json`.
- `.github/workflows/` — un workflow per script, orari e permessi descritti
  sopra.

## File di stato (letti e scritti dai workflow, committati nel repo)

| File | Scritto da | A cosa serve |
|---|---|---|
| `stato.json` | `bollettino.py` | Ultimo bollettino **pubblicato** (aqi, fascia, timestamp) — usato per decidere il prossimo post e calcolare la tendenza |
| `storico.json` | `bollettino.py` | Un record per **ogni controllo** (pubblichi o no), retention 9 giorni — materia prima del riepilogo settimanale |
| `stato_sismico.json` | `sismico.py` | ID degli eventi sismici già notificati, per non ripetere lo stesso avviso |
| `stato_acqua.json` | `acqua.py` | Data dell'ultimo archivio analisi già processato, per non ripubblicare lo stesso controllo |

`aria.yml`, `sismico.yml` e `acqua.yml` hanno `permissions: contents: write`
e fanno commit automatico di questi file a fine esecuzione
(`git commit -m "... [skip ci]"`).

## Chi "installa" cosa

Due ruoli diversi:

- **Il cittadino** non installa niente. Apre Telegram, cerca il canale (o apre
  il link `t.me/comestasolofra`) e tocca **Segui**. Da quel momento riceve
  tutti i bollettini. Può silenziare le notifiche ma restare iscritto.
- **Tu (chi gestisce)** fai il setup qui sotto una volta sola.

## Setup in 5 passi

### 1. Crea il bot Telegram
Su Telegram apri **@BotFather**, invia `/newbot`, scegli nome e username.
BotFather ti dà un **token** tipo `123456:ABC-...`. Tienilo da parte.

### 2. Crea il canale e rendi il bot amministratore
Crea un **canale** (non un gruppo), es. pubblico `@comestasolofra`. Poi:
impostazioni canale → *Amministratori* → aggiungi il tuo bot con permesso di
**pubblicare messaggi**. Senza questo, il bot non può postare.

Per `differenziata.py`, `sismico.py` e `acqua.py` conviene anche un
**canale/chat di prova privato**, usato di default finché non attivi
consapevolmente la produzione (vedi passo 4).

### 3. Trova il `chat_id`
- Canale **pubblico**: il chat_id è direttamente `@nomecanale`.
- Canale **privato**: serve l'id numerico (`-100...`); inoltra un post del
  canale a **@userinfobot** per ottenerlo.

### 4. Metti i segreti e le variabili nel repository
Repo → **Settings → Secrets and variables → Actions**.

**Secrets** (`New repository secret`):

| Nome | Valore | Usato da |
|------|--------|---|
| `WAQI_TOKEN` | il tuo token dell'API aqicn/WAQI | `bollettino.py` |
| `TELEGRAM_BOT_TOKEN` | il token del bot (passo 1) | tutti |
| `TELEGRAM_CHAT_ID` | il canale vero, es. `@comestasolofra` | tutti |
| `TELEGRAM_CHAT_ID_TEST` | il canale/chat di prova (passo 2) | `differenziata.py`, `sismico.py`, `acqua.py` |

**Variables** (`New repository variable`, non segrete — selettori a tre
stati `spento` / `test` / `produzione`, default `test` se assenti):

| Nome | Valore | Effetto |
|------|--------|---|
| `DIFFERENZIATA_AMBIENTE` | `spento` \| `test` \| `produzione` | dove va il promemoria differenziata |
| `SISMICO_AMBIENTE` | `spento` \| `test` \| `produzione` | dove va l'avviso sismico |
| `SISMICO_MESSAGGIO_DI_PROVA` | `true` | manda un messaggio con dati finti, per vedere il formato |
| `SISMICO_DIAGNOSTICA` | `true` | interroga INGV su una finestra larga (30gg/100km) e stampa solo nei log, senza inviare nulla |
| `ACQUA_AMBIENTE` | `spento` \| `test` \| `produzione` | dove va il controllo acqua potabile |

Qualsiasi valore non riconosciuto nei selettori a tre stati viene trattato
come `spento`, per sicurezza: un refuso non deve mai risultare in un invio
sul canale vero.

`bollettino.py` e `settimanale.py` non hanno questo selettore: pubblicano
sempre sul canale in `TELEGRAM_CHAT_ID` quando decidono di farlo.

### 5. Prova
**Actions → (nome workflow) → Run workflow** per lanciarlo subito.
- Per `aria.yml` puoi passare `forza_controllo: true` per bypassare
  l'auto-limite delle ~2 ore tra un controllo vero e l'altro.
- Per `sismico.yml`, prova prima con `SISMICO_DIAGNOSTICA=true` (nessun
  invio, solo log) per confermare che la connessione a INGV funzioni, poi
  con `SISMICO_MESSAGGIO_DI_PROVA=true` per vedere il formato del messaggio.
- Per `acqua.yml`, il primo lancio pubblica sempre (non c'è ancora uno
  `stato_acqua.json`): usalo per verificare che `unrar` si installi
  correttamente e che il parsing dei referti funzioni.

## Orari e frequenza

- **Aria**: cron ogni 20 minuti (`3,23,43 * * * *`), ma pubblica solo al
  cambio di fascia AQI (min. 4h dall'ultimo post) o dopo 7 giorni senza
  aggiornamenti. In pratica, in media, capita circa 4 volte al giorno.
- **Riepilogo settimanale**: `30 6 * * 1` → lunedì ~7:30/8:30 ora italiana
  (a seconda di ora legale/solare).
- **Differenziata**: `30 17 * * *` (ogni sera, ~19:30 CEST/18:30 CET) +
  `0 6 * * 5` (venerdì mattina ~8:00 CEST/7:00 CET, solo per il vetro).
- **Sismico**: cron ogni 15 minuti, ma invia solo se trova un evento nuovo
  sopra soglia. INGV impiega comunque qualche minuto a validare un evento,
  quindi controllare più spesso non lo farebbe comparire prima.
- **Acqua**: `0 8 * * *` (una volta al giorno, ~10:00 CEST/9:00 CET), ma
  pubblica solo se sul sito compare un archivio più recente dell'ultimo già
  processato. Gli archivi escono con cadenza settimanale irregolare, un
  controllo giornaliero è sufficiente a non perderne uno.

GitHub può ritardare i trigger di qualche minuto o saltarli del tutto sotto
carico: normale, è per questo che aria e sismico usano il polling frequente
con decisione a valle nello script invece di un cron a bassa frequenza.

⚠️ **Nota per chi tocca `aria.yml`**: il file ha già mostrato in passato il
comportamento di GitHub che ignora un cron modificato troppo spesso in poco
tempo. Evitare modifiche frequenti e ravvicinate a questo workflow;
raggruppare eventuali cambi futuri in un solo commit.

## Comuni vicini
Vengono cercati per nome tramite l'API WAQI. **Non tutti hanno una
centralina**: chi non ce l'ha viene escluso in automatico (mai un dato
falso attribuito al comune sbagliato). Elenco attuale in `NEARBY` in cima a
`bollettino.py`: Avellino, Montoro, Mercato San Severino, Salerno. Per
aggiungerne uno basta il nome; se un abbinamento è sbagliato si può
"fissare" la stazione giusta con l'ID esatto al posto del nome.

## Calendario raccolta differenziata
Scritto a mano in `MATERIALE_SERALE` dentro `differenziata.py` (fonte:
Comune di Solofra / app "La settimana", verificato il 12/07/2026). Non c'è
nessuno scraping automatico: se il Comune cambia il calendario, va
aggiornato a mano nel codice. Il vetro segue una regola diversa da tutto il
resto (mattina, solo 1ª/3ª/5ª venerdì del mese, esposizione entro le 12:00).
I pannolini/pannoloni del giovedì sono un servizio su registrazione: il
promemoria lo segnala con una riga separata, non lo dà per scontato per
tutti.

## Soglie avviso sismico
In `sismico.py`, lista `SOGLIE`: entro 20 km da Solofra magnitudo ≥ 2.0,
entro 60 km magnitudo ≥ 3.0 (abbassata da 3.5 il 14/07/2026 dopo il caso
reale dell'evento di Apice (BN), ML 3.3 a 26 km, avvertito distintamente in
Irpinia ma sotto la vecchia soglia per un margine di appena 0.2).

⚠️ **Non ancora verificato con una chiamata reale in produzione**: il
dominio `webservices.ingv.it` blocca l'accesso automatico da alcuni
ambienti di sviluppo. Il formato FDSN interrogato è uno standard
documentato (stesso usato da USGS), ma la prima esecuzione vera va
controllata nei log di GitHub Actions (o con `SISMICO_DIAGNOSTICA=true`)
prima di fidarsi ciecamente.

## Come funziona il controllo acqua
A differenza delle altre fonti, `solofraservizi.it` non ha un'API: la
pagina `la-tua-acqua/` pubblica link a archivi RAR
(`Certificati del DD-MM-YYYY.rar`), uno per settimana circa, ognuno con un
PDF (rapporto di prova di un laboratorio accreditato terzo) per ogni punto
di prelievo della rete idrica di Solofra (pozzi, sorgenti, serbatoi,
fontanini pubblici — 18 nell'archivio dell'8/07/2026 usato per validare il
parser).

`acqua.py`:
1. Fa scraping della pagina per trovare il link all'archivio più recente.
2. Se la data è più recente di quella salvata in `stato_acqua.json`, scarica
   l'archivio ed estrae i PDF con il comando di sistema `unrar` (installato
   dal workflow via `apt-get`, non è nativo in Python — vedi
   `.github/workflows/acqua.yml`).
3. Legge il testo di ogni PDF (libreria `pypdf`) e cerca la frase esplicita
   con cui il laboratorio dichiara l'esito ("il campione risulta conforme/
   non conforme..."). **Non deduce mai la conformità dai singoli valori dei
   parametri**: se la frase non si trova (referto in un formato diverso dal
   solito), il punto viene segnalato come "non leggibile automaticamente",
   mai dato per conforme o non conforme per omissione.
4. Pubblica: un messaggio breve se tutti i punti sono conformi, un messaggio
   con il dettaglio di quali punti (e quali referti non letti
   automaticamente) altrimenti — mai un numero aggregato che nasconde
   un'anomalia.

⚠️ **Fragilità nota**: il parsing dipende dal formato dei PDF del
laboratorio attualmente incaricato (Migliore Lab S.r.l.). Se cambia
laboratorio o il generatore dei referti cambia formato, il parser potrebbe
non trovare più i campi attesi — in quel caso il servizio segnala i referti
come "non leggibili" invece di sbagliare, ma va comunque controllato a
mano se succede spesso.

## Note e limiti
- **Attribuzione** a World Air Quality Index Project + ARPA Campania (aria)
  e a INGV (sismico): già presente nell'immagine/messaggio. Non rimuoverla.
- **Uso non-profit**: le condizioni dell'API WAQI chiedono di avvisare via
  email il team WAQI per l'uso pubblico non commerciale. Fallo prima di
  partire.
- **Dati non validati**: grezzi e provvisori (sia aria che sismico); il
  disclaimer è già nei rispettivi messaggi.
- **Indici, non concentrazioni**: i valori AQI (0–500) non sono microgrammi.
- **Il canale sismico non è un servizio di emergenza ufficiale**: per
  allerte e indicazioni di sicurezza, il messaggio stesso rimanda a INGV e
  Protezione Civile.
- **Il canale acqua non è un canale ufficiale di Solofra Servizi**: per
  reclami o informazioni sull'erogazione, il messaggio stesso rimanda a
  Solofra Servizi S.p.A. I dati riportati sono le dichiarazioni di
  conformità del laboratorio terzo, non un'elaborazione indipendente.
- **Font**: le card usano DejaVu, presente di default sui runner di GitHub.
