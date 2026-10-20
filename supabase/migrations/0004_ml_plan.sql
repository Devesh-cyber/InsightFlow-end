-- =============================================================================
-- InsightFlow v2 — the user's ML-ready rules for a dataset (roles, per-column steps, features, split)
-- Run once in: Supabase Dashboard -> SQL Editor -> New query -> paste -> Run
-- Needs 0002 and 0003 first. Safe to re-run.
-- =============================================================================

alter table public.datasets
    add column if not exists ml_plan jsonb not null default '{}'::jsonb;

do $$
begin
    if not exists (select 1 from pg_constraint where conname = 'datasets_ml_plan_is_object') then
        alter table public.datasets
            add constraint datasets_ml_plan_is_object check (jsonb_typeof(ml_plan) = 'object');
    end if;
end $$;
