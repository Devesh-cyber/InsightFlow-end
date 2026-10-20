-- =============================================================================
-- InsightFlow v2 — issues the user marked "Looks fine, keep it"
-- Run once in: Supabase Dashboard -> SQL Editor -> New query -> paste -> Run
-- Needs 0002_checks.sql first. Safe to re-run.
-- =============================================================================

alter table public.datasets
    add column if not exists reviewed jsonb not null default '[]'::jsonb;

do $$
begin
    if not exists (select 1 from pg_constraint where conname = 'datasets_reviewed_is_array') then
        alter table public.datasets
            add constraint datasets_reviewed_is_array check (jsonb_typeof(reviewed) = 'array');
    end if;
end $$;
