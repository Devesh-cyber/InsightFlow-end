-- =============================================================================
-- InsightFlow v2 — saved checks (custom rules that are re-checked on every version)
-- Run once in: Supabase Dashboard -> SQL Editor -> New query -> paste -> Run
-- Safe to re-run.
-- =============================================================================

alter table public.datasets
    add column if not exists checks jsonb not null default '[]'::jsonb;

do $$
begin
    if not exists (select 1 from pg_constraint where conname = 'datasets_checks_is_array') then
        alter table public.datasets
            add constraint datasets_checks_is_array check (jsonb_typeof(checks) = 'array');
    end if;
end $$;
