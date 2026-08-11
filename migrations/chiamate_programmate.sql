-- Fase B — chiamate programmate (con eventuale ripetizione)
create table if not exists public.chiamate_programmate (
    id             bigserial primary key,
    esegui_alle    timestamptz not null,
    azione         text not null default 'prenota',   -- prenota | giro
    payload        jsonb not null default '{}'::jsonb,
    stato          text not null default 'in_attesa',  -- in_attesa | in_corso | eseguita | errore
    ripeti         boolean not null default false,
    intervallo_min int not null default 15,
    max_tentativi  int not null default 1,
    tentativi      int not null default 0,
    creato_at      timestamptz not null default now(),
    eseguito_at    timestamptz
);
create index if not exists idx_chiam_prog_stato on public.chiamate_programmate(stato, esegui_alle);
alter table public.chiamate_programmate enable row level security;

-- Collega le prenotazioni alla campagna programmata e registra l'esito
alter table public.prenotazioni_vocali add column if not exists programmata_id bigint;
alter table public.prenotazioni_vocali add column if not exists esito text;
