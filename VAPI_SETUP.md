# Configurazione Vapi — Agente vocale Cavour

Tutto ciò che serve a far parlare Vapi con gli endpoint `/api/voice/`.
Base URL: `https://web-production-f9d5d.up.railway.app/api/voice`
Ogni tool invia l'header `X-Voice-Secret: <VOICE_SECRET>` (stesso valore impostato su Railway).

---

## 1. First message (saluto iniziale)

> "Cavour Immobiliare, buongiorno. Sono l'assistente virtuale dell'agenzia, la aiuto io. Come posso esserle utile?"

Dichiara subito di essere un assistente virtuale (obbligo AI Act art. 50), in modo naturale.

---

## 2. System prompt

```
Sei l'assistente vocale telefonico di Cavour Immobiliare, agenzia boutique di Milano.
Rispondi in italiano, del Lei, con frasi brevi e naturali. Una domanda alla volta.
Sei al telefono: niente elenchi lunghi, niente tecnicismi, tono calmo e cortese.

IDENTITÀ
- Sei un assistente virtuale dell'agenzia. Se te lo chiedono, dillo con naturalezza.
- Non dai il tuo nome come se fossi una persona reale; sei "l'assistente di Cavour".

REGOLA D'ORO — NON INVENTARE MAI
- Prezzi, metrature, indirizzi, disponibilità e appuntamenti li sai SOLO dai tool.
- Non stimare, non arrotondare, non ricordare a memoria. Se un dato non c'è o un
  tool non risponde: "Su questo le faccio richiamare da un collega" e usa il tool
  lead per salvare il contatto. Mai improvvisare.

RICONOSCIMENTO CHIAMANTE
- All'inizio chiama lookup_cliente con il numero del chiamante (caller ID).
- Se trovato=true NON leggere subito i suoi dati. Prima VERIFICA L'IDENTITÀ:
  chiedi di confermare il nome e un dato che già risulta (es. la zona che cercava).
  Solo se coincide puoi fare riferimento alla sua richiesta. Se non coincide,
  trattalo come chiamante generico e non leggere dati personali.
- Se trovato=false, prosegui normalmente come con un nuovo contatto.

COSA SAI FARE
- Dare informazioni su un immobile: usa immobile (per id o indirizzo).
- Proporre alternative: usa proposte (zona, budget, metratura, tipo). Al massimo
  TRE immobili a voce: oltre tre al telefono non si ricordano.
- Fissare una visita: usa appuntamento. Non confermare MAI uno slot senza che il
  tool risponda confermato=true. Se confermato=false, proponi un altro orario.
- Numero sconosciuto interessato: raccogli zona, budget di massima e cosa cerca,
  poi usa lead per salvarlo.

COME PRONUNCIARE I PREZZI
- Usa SEMPRE il campo prezzo_parlato così com'è ("270.000 euro", "1.300 euro al
  mese"). Non trasformarlo, non aggiungere cifre.

PASSA A UN COLLEGA UMANO SE
- Si tratta sul prezzo o sulla provvigione, o si parla di soldi fuori dalla scheda.
- Il cliente è infastidito o chiede espressamente di parlare con una persona / con Ilan.
- Richiesta fuori copione a cui non sai rispondere con i tool.
  In questi casi: "Le faccio richiamare subito dal Dott. Boni" e salva il contatto con lead.

LIMITI
- Non dai il tuo parere su convenienza o valore. Non prometti compratori o tempi.
- Il numero dell'agenzia, se serve, è 02 35981509.
```

---

## 3. Custom tools (5)

Per ciascuno: `type: function`, con `server.url` e `server.headers`.
Sostituisci `<VOICE_SECRET>` con il valore reale impostato su Railway.

### 3.1 lookup_cliente
```json
{
  "type": "function",
  "function": {
    "name": "lookup_cliente",
    "description": "Riconosce il chiamante dal numero di telefono. Chiamalo per primo a inizio conversazione con il caller ID. Ritorna trovato:false se sconosciuto.",
    "parameters": {
      "type": "object",
      "properties": {
        "telefono": { "type": "string", "description": "Numero del chiamante, es. +393331234567" }
      },
      "required": ["telefono"]
    }
  },
  "server": {
    "url": "https://web-production-f9d5d.up.railway.app/api/voice/lookup-cliente",
    "headers": { "X-Voice-Secret": "<VOICE_SECRET>" }
  }
}
```

### 3.2 immobile
```json
{
  "type": "function",
  "function": {
    "name": "immobile",
    "description": "Scheda di un singolo immobile per id o indirizzo. Ritorna prezzo_parlato già pronto da leggere. Se non è tra quelli proponibili, ritorna trovato:false.",
    "parameters": {
      "type": "object",
      "properties": {
        "id": { "type": "integer", "description": "id numerico dell'immobile" },
        "indirizzo": { "type": "string", "description": "parte dell'indirizzo, es. 'Filelfo'" }
      }
    }
  },
  "server": {
    "url": "https://web-production-f9d5d.up.railway.app/api/voice/immobile",
    "headers": { "X-Voice-Secret": "<VOICE_SECRET>" }
  }
}
```

### 3.3 proposte
```json
{
  "type": "function",
  "function": {
    "name": "proposte",
    "description": "Propone fino a 3 immobili in linea con la richiesta. Usa zona, budget massimo, metratura minima e tipo (vendita o locazione). Leggi al massimo 3 risultati.",
    "parameters": {
      "type": "object",
      "properties": {
        "zone": { "type": "array", "items": { "type": "string" }, "description": "Zone di interesse, es. ['Wagner','Pagano']" },
        "budget_max": { "type": "number", "description": "Budget massimo in euro (prezzo di vendita o canone mensile)" },
        "mq_min": { "type": "integer", "description": "Metratura minima in mq" },
        "tipo_contratto": { "type": "string", "enum": ["vendita", "locazione"], "description": "Vendita o locazione (affitto = locazione)" }
      }
    }
  },
  "server": {
    "url": "https://web-production-f9d5d.up.railway.app/api/voice/proposte",
    "headers": { "X-Voice-Secret": "<VOICE_SECRET>" }
  }
}
```

### 3.4 appuntamento
```json
{
  "type": "function",
  "function": {
    "name": "appuntamento",
    "description": "Fissa una visita. Non confermare a voce finché non ritorna confermato:true. Se confermato:false proponi un altro orario. Orari ammessi 9-20.",
    "parameters": {
      "type": "object",
      "properties": {
        "id_immobile": { "type": "integer", "description": "id dell'immobile da visitare" },
        "quando": { "type": "string", "description": "Data e ora ISO 8601 con fuso, es. 2026-08-14T17:00:00+02:00" },
        "cliente_id": { "type": "integer", "description": "id del cliente se già riconosciuto da lookup_cliente" },
        "telefono": { "type": "string", "description": "Numero del chiamante (se cliente_id non noto)" },
        "nome": { "type": "string", "description": "Nome del chiamante (se cliente da creare)" }
      },
      "required": ["id_immobile", "quando"]
    }
  },
  "server": {
    "url": "https://web-production-f9d5d.up.railway.app/api/voice/appuntamento",
    "headers": { "X-Voice-Secret": "<VOICE_SECRET>" }
  }
}
```

### 3.5 lead
```json
{
  "type": "function",
  "function": {
    "name": "lead",
    "description": "Salva un nuovo contatto interessato (numero sconosciuto). Raccogli prima zona, budget di massima e cosa cerca, poi chiama questo tool.",
    "parameters": {
      "type": "object",
      "properties": {
        "telefono": { "type": "string", "description": "Numero del chiamante" },
        "nome": { "type": "string", "description": "Nome e cognome se forniti" },
        "zone": { "type": "array", "items": { "type": "string" }, "description": "Zone di interesse" },
        "budget_max": { "type": "number", "description": "Budget massimo in euro" },
        "tipo_contratto": { "type": "string", "enum": ["vendita", "locazione"] },
        "note": { "type": "string", "description": "Cosa cerca, in breve" }
      },
      "required": ["telefono"]
    }
  },
  "server": {
    "url": "https://web-production-f9d5d.up.railway.app/api/voice/lead",
    "headers": { "X-Voice-Secret": "<VOICE_SECRET>" }
  }
}
```

---

## 4. Impostazioni voce/modello

- **Modello**: veloce (le risposte sono brevi, i dati vengono dai tool).
- **TTS**: ElevenLabs, voce italiana femminile. Provarne 2-3, scegliere quella che
  sbaglia meno vie e cognomi.
- **STT**: italiano, barge-in attivo.
- **Turn detection**: allungare la soglia di fine turno (l'italiano al telefono ha
  pause lunghe, il default taglia la parola).
- **Numero**: numero italiano Twilio dedicato.

---

## 5. Prova prima di mostrarla

20 chiamate "cattive" fatte da Ilan: parlando sopra, cambiando idea, in strada, con
il vivavoce, e testando apposta indirizzi e cognomi difficili (il TTS mangia i civici:
"Via Filelfo" può diventare una parola sola). La demo è pronta quando: si dichiara AI,
riconosce il numero e verifica l'identità, dà prezzo giusto (vendita e locazione),
propone due alternative in whitelist, fissa un appuntamento che compare in agenda, si
ferma se gli parli sopra, e su una domanda fuori copione passa a un umano invece di
inventare.

---

## Nota tecnica

Gli endpoint usano asyncpg (pool Postgres via `DATABASE_URL`), coerenti col resto del
backend. `/appuntamento` scrive sulla tabella `appuntamenti` e verifica lì la
disponibilità (±90 min sullo stesso immobile); la sincronizzazione su Google Calendar
è gestita dal job di Paolo. Whitelist mandato hardcoded: `[6,7,8,9,10,11,12,13,14]`.
