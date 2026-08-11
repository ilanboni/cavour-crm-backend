-- Fase B — tracciamento prenotazioni singole (per il poller dell'esito)
create table if not exists public.prenotazioni_vocali (
    id            bigserial primary key,
    call_id       text,
    tipo          text,
    luogo         text,
    oggetto       text,
    quando_label  text,
    stato         text not null default 'in_corso',   -- in_corso | fatto | fallito
    notificato    boolean not null default false,
    creato_at     timestamptz not null default now()
);
create index if not exists idx_prenot_stato on public.prenotazioni_vocali(stato);
create index if not exists idx_prenot_call  on public.prenotazioni_vocali(call_id);
alter table public.prenotazioni_vocali enable row level security;
