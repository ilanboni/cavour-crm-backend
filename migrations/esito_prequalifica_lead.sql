-- Esito della chiamata di prequalifica di Paolo, persistito sul lead.
-- Serve al report giornaliero (funnel: chiamati → risposto → caldi/freddi)
-- e a fermare i follow-up sui lead risultati freddi.
-- Lanciare nel SQL Editor di Supabase.

ALTER TABLE leads ADD COLUMN IF NOT EXISTS esito_prequalifica    text;         -- 'caldo' | 'freddo' | 'non_risposto'
ALTER TABLE leads ADD COLUMN IF NOT EXISTS prequalifica_esito_at timestamptz;  -- quando è arrivato l'esito
ALTER TABLE leads ADD COLUMN IF NOT EXISTS qualificato           boolean;      -- true se caldo (interessato + vende a Milano)
ALTER TABLE leads ADD COLUMN IF NOT EXISTS vende_a_milano        boolean;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS tempistica            text;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS vuole_appuntamento    boolean;

-- Indice per il report giornaliero (lead per esito nel giorno)
CREATE INDEX IF NOT EXISTS idx_leads_prequalifica_esito_at ON leads (prequalifica_esito_at);
