-- Mumble Cloud Sync — Supabase Schema Migration
-- Run against your Supabase project SQL editor (one-time setup).
-- Creates 6 user-data tables with RLS policies scoped to the
-- authenticated user via auth.uid() = user_id.
--
-- Pre-requisites:
--   1. Create a Supabase project (free tier).
--   2. Run this script in the Supabase SQL editor.
--   3. Enable Data API access: after RLS + policies are created,
--      grant the required permissions (included below).
--
-- The anon/publishable key goes in the desktop app; the service_role
-- key stays on the server side and is NEVER shipped or committed.

-- =========================================================================
-- 1. user_settings — app configuration (minus API keys, which stay local)
-- =========================================================================
create table if not exists public.user_settings (
    id            uuid primary key default gen_random_uuid(),
    user_id       uuid not null references auth.users on delete cascade,
    -- Stored as a JSONB blob of the syncable subset of settings.json.
    -- API keys (cerebras_api_key, openai_api_key, etc.) are NEVER included.
    payload       jsonb not null default '{}'::jsonb,
    updated_at    timestamptz not null default now(),
    created_at    timestamptz not null default now()
);

-- One row per user (no version history — last-write-wins)
create unique index if not exists user_settings_user_idx
    on public.user_settings (user_id);

alter table public.user_settings enable row level security;

create policy "owner select" on public.user_settings
    for select to authenticated
    using ((select auth.uid()) = user_id);

create policy "owner insert" on public.user_settings
    for insert to authenticated
    with check ((select auth.uid()) = user_id);

create policy "owner update" on public.user_settings
    for update to authenticated
    using ((select auth.uid()) = user_id)
    with check ((select auth.uid()) = user_id);

create policy "owner delete" on public.user_settings
    for delete to authenticated
    using ((select auth.uid()) = user_id);

-- =========================================================================
-- 2. user_stats — independent statistics
-- =========================================================================
create table if not exists public.user_stats (
    id            uuid primary key default gen_random_uuid(),
    user_id       uuid not null references auth.users on delete cascade,
    payload       jsonb not null default '{}'::jsonb,
    updated_at    timestamptz not null default now(),
    created_at    timestamptz not null default now()
);

create unique index if not exists user_stats_user_idx
    on public.user_stats (user_id);
create index if not exists user_stats_updated_idx
    on public.user_stats (user_id, updated_at desc);

alter table public.user_stats enable row level security;

create policy "owner select" on public.user_stats
    for select to authenticated
    using ((select auth.uid()) = user_id);

create policy "owner insert" on public.user_stats
    for insert to authenticated
    with check ((select auth.uid()) = user_id);

create policy "owner update" on public.user_stats
    for update to authenticated
    using ((select auth.uid()) = user_id)
    with check ((select auth.uid()) = user_id);

create policy "owner delete" on public.user_stats
    for delete to authenticated
    using ((select auth.uid()) = user_id);

-- =========================================================================
-- 3. user_history — transcript history (last 100)
-- =========================================================================
create table if not exists public.user_history (
    id            uuid primary key default gen_random_uuid(),
    user_id       uuid not null references auth.users on delete cascade,
    -- Each row is one transcript entry from history.json
    payload       jsonb not null default '{}'::jsonb,
    -- For efficient per-user pagination and LWW sync
    entry_time    text,            -- ISO timestamp from the entry (sort key)
    updated_at    timestamptz not null default now(),
    created_at    timestamptz not null default now()
);

create index if not exists user_history_user_time_idx
    on public.user_history (user_id, entry_time desc);
create index if not exists user_history_updated_idx
    on public.user_history (user_id, updated_at desc);

alter table public.user_history enable row level security;

create policy "owner select" on public.user_history
    for select to authenticated
    using ((select auth.uid()) = user_id);

create policy "owner insert" on public.user_history
    for insert to authenticated
    with check ((select auth.uid()) = user_id);

create policy "owner update" on public.user_history
    for update to authenticated
    using ((select auth.uid()) = user_id)
    with check ((select auth.uid()) = user_id);

create policy "owner delete" on public.user_history
    for delete to authenticated
    using ((select auth.uid()) = user_id);

-- =========================================================================
-- 4. user_reader_library — Reader documents + progress
-- =========================================================================
create table if not exists public.user_reader_library (
    id            uuid primary key default gen_random_uuid(),
    user_id       uuid not null references auth.users on delete cascade,
    -- Full reader_library.json entry as JSONB
    payload       jsonb not null default '{}'::jsonb,
    doc_hash      text,            -- content-hash identity for dedup
    updated_at    timestamptz not null default now(),
    created_at    timestamptz not null default now()
);

create index if not exists user_reader_library_user_idx
    on public.user_reader_library (user_id, doc_hash);
create index if not exists user_reader_library_updated_idx
    on public.user_reader_library (user_id, updated_at desc);

alter table public.user_reader_library enable row level security;

create policy "owner select" on public.user_reader_library
    for select to authenticated
    using ((select auth.uid()) = user_id);

create policy "owner insert" on public.user_reader_library
    for insert to authenticated
    with check ((select auth.uid()) = user_id);

create policy "owner update" on public.user_reader_library
    for update to authenticated
    using ((select auth.uid()) = user_id)
    with check ((select auth.uid()) = user_id);

create policy "owner delete" on public.user_reader_library
    for delete to authenticated
    using ((select auth.uid()) = user_id);

-- =========================================================================
-- 5. user_favorites — starred items
-- =========================================================================
create table if not exists public.user_favorites (
    id            uuid primary key default gen_random_uuid(),
    user_id       uuid not null references auth.users on delete cascade,
    payload       jsonb not null default '{}'::jsonb,
    fav_text      text,            -- for dedup lookups
    updated_at    timestamptz not null default now(),
    created_at    timestamptz not null default now()
);

create index if not exists user_favorites_user_idx
    on public.user_favorites (user_id, fav_text);
create index if not exists user_favorites_updated_idx
    on public.user_favorites (user_id, updated_at desc);

alter table public.user_favorites enable row level security;

create policy "owner select" on public.user_favorites
    for select to authenticated
    using ((select auth.uid()) = user_id);

create policy "owner insert" on public.user_favorites
    for insert to authenticated
    with check ((select auth.uid()) = user_id);

create policy "owner update" on public.user_favorites
    for update to authenticated
    using ((select auth.uid()) = user_id)
    with check ((select auth.uid()) = user_id);

create policy "owner delete" on public.user_favorites
    for delete to authenticated
    using ((select auth.uid()) = user_id);

-- =========================================================================
-- 6. user_presets — custom presets (5 slots)
-- =========================================================================
create table if not exists public.user_presets (
    id            uuid primary key default gen_random_uuid(),
    user_id       uuid not null references auth.users on delete cascade,
    payload       jsonb not null default '{}'::jsonb,
    updated_at    timestamptz not null default now(),
    created_at    timestamptz not null default now()
);

create unique index if not exists user_presets_user_idx
    on public.user_presets (user_id);

alter table public.user_presets enable row level security;

create policy "owner select" on public.user_presets
    for select to authenticated
    using ((select auth.uid()) = user_id);

create policy "owner insert" on public.user_presets
    for insert to authenticated
    with check ((select auth.uid()) = user_id);

create policy "owner update" on public.user_presets
    for update to authenticated
    using ((select auth.uid()) = user_id)
    with check ((select auth.uid()) = user_id);

create policy "owner delete" on public.user_presets
    for delete to authenticated
    using ((select auth.uid()) = user_id);

-- =========================================================================
-- Grants — enable Data API access for authenticated users
-- =========================================================================
grant select, insert, update, delete on public.user_settings to authenticated;
grant select, insert, update, delete on public.user_stats to authenticated;
grant select, insert, update, delete on public.user_history to authenticated;
grant select, insert, update, delete on public.user_reader_library to authenticated;
grant select, insert, update, delete on public.user_favorites to authenticated;
grant select, insert, update, delete on public.user_presets to authenticated;

-- Service role (server-side only — NEVER ship the service_role key)
grant all on public.user_settings to service_role;
grant all on public.user_stats to service_role;
grant all on public.user_history to service_role;
grant all on public.user_reader_library to service_role;
grant all on public.user_favorites to service_role;
grant all on public.user_presets to service_role;
