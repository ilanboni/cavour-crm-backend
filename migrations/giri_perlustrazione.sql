-- Fase B — Giro di perlustrazione ristoranti
-- Stato condiviso tra CRM backend (asyncpg) e Paolo Flask (supabase-py).

create table if not exists public.giri_perlustrazione (
    id          text primary key,
    creato_at   timestamptz not null default now(),
    persone     text,
    quando      text,
    attesi      int  not null default 0,          -- chiamate effettivamente avviate (webhook attesi)
    stato       text not null default 'in_corso', -- in_corso | notificato
    notificato  boolean not null default false
);

create table if not exists public.giro_chiamate (
    id               bigserial primary key,
    giro_id          text not null,
    ordine           int  not null,
    ristorante       text,
    numero           text,
    call_id          text,
    stato            text not null default 'in_corso', -- in_corso | disponibile | non_disponibile | fallita
    orario_proposto  text,
    note             text,
    aggiornato_at    timestamptz not null default now()
);

create index if not exists idx_giro_chiamate_giro on public.giro_chiamate(giro_id);
create index if not exists idx_giro_chiamate_call on public.giro_chiamate(call_id);
create index if not exists idx_giri_stato          on public.giri_perlustrazione(stato);
