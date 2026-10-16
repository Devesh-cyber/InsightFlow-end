-- =============================================================================
-- InsightFlow v2 — initial schema
-- Run once in: Supabase Dashboard -> SQL Editor -> New query -> paste -> Run
-- Safe to re-run (uses IF NOT EXISTS / ON CONFLICT).
-- =============================================================================

-- -----------------------------------------------------------------------------
-- datasets: one row per uploaded dataset
-- -----------------------------------------------------------------------------
create table if not exists public.datasets (
    id                 uuid primary key default gen_random_uuid(),
    user_id            uuid not null references auth.users (id) on delete cascade,
    name               text not null check (char_length(name) between 1 and 255),
    source_filename    text not null check (char_length(source_filename) between 1 and 255),
    source_format      text not null check (source_format in ('csv', 'xlsx')),
    source_path        text not null,          -- original upload in Storage (never modified)
    source_size_bytes  bigint not null check (source_size_bytes > 0),
    sheet_name         text,                   -- xlsx only: the sheet currently loaded
    sheet_names        text[],                 -- xlsx only: all sheets in the workbook
    current_version    integer not null default 1 check (current_version >= 1),
    status             text not null default 'ready' check (status in ('processing', 'ready', 'failed')),
    row_count          bigint not null default 0,
    column_count       integer not null default 0,
    created_at         timestamptz not null default now(),
    updated_at         timestamptz not null default now()
);

create index if not exists datasets_user_updated_idx
    on public.datasets (user_id, updated_at desc);

-- -----------------------------------------------------------------------------
-- dataset_versions: immutable history. Each version points at a Parquet file.
-- Reverting creates a NEW version that reuses an older file (no copying).
-- -----------------------------------------------------------------------------
create table if not exists public.dataset_versions (
    id            uuid primary key default gen_random_uuid(),
    dataset_id    uuid not null references public.datasets (id) on delete cascade,
    version       integer not null check (version >= 1),
    parquet_path  text not null,
    row_count     bigint not null,
    column_count  integer not null,
    size_bytes    bigint not null,
    operation     jsonb not null,   -- what created it: {"type": "upload"} or {"type": "fill_missing", "params": {...}}
    summary       jsonb,            -- before/after counts, affected rows/cells, warnings
    created_at    timestamptz not null default now(),
    unique (dataset_id, version)
);

-- -----------------------------------------------------------------------------
-- analysis_cache: computed results (overview, health, column stats...)
-- Keyed by version, so a result can never be stale: a new version = new key.
-- -----------------------------------------------------------------------------
create table if not exists public.analysis_cache (
    dataset_id   uuid not null references public.datasets (id) on delete cascade,
    version      integer not null,
    kind         text not null,
    params_hash  text not null default '',
    result       jsonb not null,
    created_at   timestamptz not null default now(),
    primary key (dataset_id, version, kind, params_hash)
);

-- -----------------------------------------------------------------------------
-- Keep updated_at current
-- -----------------------------------------------------------------------------
create or replace function public.set_updated_at() returns trigger
language plpgsql as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists datasets_set_updated_at on public.datasets;
create trigger datasets_set_updated_at
    before update on public.datasets
    for each row execute function public.set_updated_at();

-- -----------------------------------------------------------------------------
-- SECURITY
-- These tables are accessed ONLY by our backend (which connects as the
-- database owner and enforces ownership in code). We enable Row Level Security
-- with NO policies, which blocks every request coming through Supabase's
-- public REST API with the publishable/anon key. This closes the hole that
-- the old project left open. We also revoke the default grants as a second lock.
-- -----------------------------------------------------------------------------
alter table public.datasets          enable row level security;
alter table public.dataset_versions  enable row level security;
alter table public.analysis_cache    enable row level security;

revoke all on public.datasets         from anon, authenticated;
revoke all on public.dataset_versions from anon, authenticated;
revoke all on public.analysis_cache   from anon, authenticated;

-- -----------------------------------------------------------------------------
-- Storage bucket: PRIVATE. Only the backend (secret key) reads/writes it.
-- No storage policies are created, so browsers cannot touch it directly.
-- 100 MB hard ceiling at the storage layer; the app enforces its own lower limit.
-- -----------------------------------------------------------------------------
insert into storage.buckets (id, name, public, file_size_limit)
values ('datasets', 'datasets', false, 104857600)
on conflict (id) do update set public = false, file_size_limit = excluded.file_size_limit;

-- -----------------------------------------------------------------------------
-- Verification: run this after the script; every row should say true.
-- select relname, relrowsecurity from pg_class
-- where relname in ('datasets', 'dataset_versions', 'analysis_cache');
-- -----------------------------------------------------------------------------
