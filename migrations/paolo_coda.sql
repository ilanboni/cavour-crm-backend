-- La coda dei messaggi di Paolo smette di essere un campo JSON.
--
-- Fino al 18/11/2026 stava tutta in system_config.paolo_azioni_programmate:
-- UN solo campo di testo da 80 KB. Ogni lettura se lo tirava giu' per intero e
-- ogni scrittura lo riscriveva tutto. Nei log di un giorno solo: 744 letture di
-- quella chiave, su 28.129 richieste totali al progetto. Insieme alle altre
-- chiavi di system_config lette in ciclo faceva circa 4.800 letture al giorno
-- per chiedere "c'e' qualcosa da fare?", e il database rispondeva con 2.495
-- "Authentication timeout" e 1.460 "unable to check out connection from the
-- pool": il pooler non riusciva piu' a dare connessioni a nessuno.
--
-- C'e' un secondo motivo, piu' serio del carico. Il "claim atomico" che doveva
-- impedire i doppi invii non lo era: due esecuzioni sovrapposte leggevano
-- entrambe l'array, vedevano entrambe stato='in_attesa' e riscrivevano
-- entrambe, e vinceva l'ultima. Con una riga per messaggio il claim diventa
-- UPDATE ... WHERE id = $1 AND stato = 'in_attesa', che o la vinci o non la
-- vinci: il doppio invio non e' piu' possibile.
create table if not exists public.paolo_coda (
  id                      bigserial primary key,
  -- L'identificativo testuale che Ilan usa nei comandi "manda <id>" / "scarta <id>".
  rif                     text unique,
  tipo                    text not null default '',
  stato                   text not null default 'in_attesa',
  origine                 text,
  telefono                text not null,
  nome_lead               text,
  messaggio               text not null,
  documento_url           text,
  nome_file               text,
  richiede_approvazione   boolean not null default false,
  -- Resta testo e non timestamp: viene confrontato cosi' com'e' con
  -- whatsapp_log.created_at, e cambiargli tipo cambierebbe quel confronto.
  salta_se_risponde_dopo  text,
  scheduled_at            timestamptz not null,
  creato_at               timestamptz not null default now(),
  inviato_at              timestamptz,
  notificato_ilan_at      timestamptz,
  errore                  text,
  tentativi               smallint not null default 0,
  updated_at              timestamptz not null default now()
);

comment on table public.paolo_coda is
  'Messaggi WhatsApp programmati di Paolo, una riga per messaggio. Sostituisce system_config.paolo_azioni_programmate.';

-- L'indice che fa la differenza: il job che spedisce chiede solo le righe
-- scadute e ancora da mandare, che sono quasi sempre zero o una. Parziale
-- apposta: le migliaia di righe gia' inviate non entrano nell'indice.
create index if not exists idx_paolo_coda_da_inviare
  on public.paolo_coda (scheduled_at)
  where stato = 'in_attesa';

create index if not exists idx_paolo_coda_telefono on public.paolo_coda (telefono);
create index if not exists idx_paolo_coda_stato on public.paolo_coda (stato, scheduled_at desc);
