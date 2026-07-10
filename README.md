# Come Sta Solofra — bollettino aria su Telegram (con immagine)

Pubblica **4 volte al giorno**, in automatico e a costo zero, la qualità
dell'aria della stazione **Solofra Zona Industriale** (dati World Air Quality
Index Project / ARPA Campania) su un canale Telegram, sotto forma di **card
grafica** con il semaforo AQI e il **confronto con i comuni vicini**
(Serino, Avellino, Atripalda, Montoro, Mercato San Severino, Salerno).

Gira su **GitHub Actions**: nessun server da mantenere, il token resta nascosto
nei *Secrets*.

## File
- `bollettino.py` — scarica i dati, cerca i comuni vicini, genera l'immagine e
  la invia al canale.
- `.github/workflows/bollettino-aria.yml` — lancia lo script 4 volte al giorno.

## Chi "installa" cosa

Due ruoli diversi:

- **Il cittadino** non installa niente. Apre Telegram, cerca il canale (o apre
  il tuo link `t.me/comestasolofra`) e tocca **Segui**. Da quel momento riceve
  la card 4 volte al giorno. Può silenziare le notifiche ma restare iscritto.
- **Tu (chi gestisce)** fai il setup qui sotto una volta sola.

## Setup in 5 passi

### 1. Crea il bot Telegram
Su Telegram apri **@BotFather**, invia `/newbot`, scegli nome e username.
BotFather ti dà un **token** tipo `123456:ABC-...`. Tienilo da parte.

### 2. Crea il canale e rendi il bot amministratore
Crea un **canale** (non un gruppo), es. pubblico `@comestasolofra`. Poi:
impostazioni canale → *Amministratori* → aggiungi il tuo bot con permesso di
**pubblicare messaggi**. Senza questo, il bot non può postare.

### 3. Trova il `chat_id`
- Canale **pubblico**: il chat_id è direttamente `@nomecanale`.
- Canale **privato**: serve l'id numerico (`-100...`); inoltra un post del
  canale a **@userinfobot** per ottenerlo.

### 4. Metti i segreti nel repository
Repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Nome | Valore |
|------|--------|
| `WAQI_TOKEN` | il tuo token dell'API aqicn/WAQI |
| `TELEGRAM_BOT_TOKEN` | il token del bot (passo 1) |
| `TELEGRAM_CHAT_ID` | il canale, es. `@comestasolofra` |

### 5. Prova
**Actions → Bollettino aria Solofra → Run workflow** per lanciarlo subito. Se
tutto è a posto, la card compare nel canale. Da lì parte da solo, 4 volte al
giorno.

## Orari e frequenza
Il cron è `0 6,10,14,18 * * *` = **06:00 / 10:00 / 14:00 / 18:00 UTC**, cioè
circa 07–08, 11–12, 15–16, 19–20 ora italiana (varia con l'ora legale). Per
cambiare orari o numero di uscite, modifica la riga `cron`. GitHub può ritardare
di qualche minuto: normale. La stazione si aggiorna circa ogni ora, quindi 4
uscite al giorno hanno senso senza essere ripetitive.

## Comuni vicini
Vengono cercati per nome tramite l'API. **Non tutti hanno una centralina**: chi
non ce l'ha compare come `n/d` (pallino grigio). Puoi modificare l'elenco nella
lista `NEARBY` in cima a `bollettino.py`. Se noti un abbinamento sbagliato,
puoi "fissare" la stazione giusta usando l'ID esatto al posto del nome.

## Note e limiti
- **Attribuzione** a World Air Quality Index Project + ARPA Campania: già
  presente nell'immagine e nella didascalia. Non rimuoverla.
- **Uso non-profit**: le condizioni dell'API chiedono di avvisare via email il
  team WAQI per l'uso pubblico non commerciale. Fallo prima di partire.
- **Dati non validati**: grezzi e provvisori; il disclaimer è già nel post.
- **Indici, non concentrazioni**: i valori AQI (0–500) non sono microgrammi.
- **Font**: la card usa DejaVu, presente di default sui runner di GitHub.
