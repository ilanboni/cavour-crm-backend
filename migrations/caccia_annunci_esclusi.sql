-- Un annuncio che Ilan ha guardato e che NON e' lo stesso immobile.
--
-- Serve perche' il raggruppamento lavora su via, civico, metri e prezzo: due
-- appartamenti dello stesso stabile con la stessa metratura finiscono insieme
-- e nessun automatismo puo' distinguerli. Solo chi apre i due annunci lo vede.
--
-- L'esclusione sta sull'URL, non sulla riga, perche' il job notturno
-- ricostruisce i gruppi da zero: se stesse sulla riga, l'annuncio tornerebbe
-- dentro la notte successiva, o peggio formerebbe una riga nuova. Cosi' invece
-- sparisce dalla materia prima e non si riforma piu'.
create table if not exists public.caccia_annunci_esclusi (
  url                text primary key,
  pluricondiviso_id  bigint references public.immobili_pluricondivisi(id) on delete set null,
  motivo             text,
  created_at         timestamptz not null default now()
);

comment on table public.caccia_annunci_esclusi is
  'Annunci che Ilan ha dichiarato non essere lo stesso immobile della scheda. Il job di raggruppamento li salta.';

create index if not exists idx_caccia_esclusi_pluri
  on public.caccia_annunci_esclusi (pluricondiviso_id);
