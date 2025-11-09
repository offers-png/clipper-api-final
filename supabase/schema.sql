-- Enable UUIDs
create extension if not exists "pgcrypto";

-- ========== PROFILES ==========
create table if not exists public.profiles (
  id uuid primary key default auth.uid(),
  email text unique not null,
  plan text not null default 'free',             -- free | pro | enterprise
  credits_seconds int not null default 3600,     -- 60 minutes
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create or replace function public.handle_new_user()
returns trigger as $$
begin
  insert into public.profiles (id, email)
  values (new.id, new.email)
  on conflict (id) do nothing;
  return new;
end;
$$ language plpgsql security definer;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
after insert on auth.users
for each row execute function public.handle_new_user();

-- ========== VIDEOS ==========
create table if not exists public.videos (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  src_url text,                     -- absolute URL you return to the FE, optional
  storage_path text,                -- if you store the raw file in storage
  duration_sec int,                 -- full source duration (optional)
  transcript text,                  -- full transcript for the *source*
  created_at timestamptz not null default now()
);

create index if not exists idx_videos_user on public.videos(user_id);

-- ========== CLIPS ==========
create table if not exists public.clips (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  video_id uuid not null references public.videos(id) on delete cascade,
  start_sec int not null,
  end_sec   int not null,
  duration_sec int generated always as ((end_sec - start_sec)) stored,
  preview_url text,                  -- absolute URL ( /media/previews/... resolved )
  final_url   text,                  -- absolute URL if exported
  transcript  text,                  -- transcript for the *clip* (optional)
  created_at  timestamptz not null default now()
);

create index if not exists idx_clips_user on public.clips(user_id);
create index if not exists idx_clips_video on public.clips(video_id);

-- ========== CREDITS ==========
create or replace function public.charge_seconds(u uuid, used int)
returns void as $$
begin
  update public.profiles
     set credits_seconds = greatest(0, credits_seconds - used),
         updated_at = now()
   where id = u;
end;
$$ language plpgsql security definer;

-- ========== RLS ==========
alter table public.profiles enable row level security;
alter table public.videos  enable row level security;
alter table public.clips   enable row level security;

-- Profiles: user can see & update self
drop policy if exists "profiles_select_self" on public.profiles;
create policy "profiles_select_self"
  on public.profiles for select
  using (id = auth.uid());

drop policy if exists "profiles_update_self" on public.profiles;
create policy "profiles_update_self"
  on public.profiles for update
  using (id = auth.uid());

-- Videos: owner-only CRUD
drop policy if exists "videos_crud_owner" on public.videos;
create policy "videos_crud_owner"
  on public.videos for all
  using (user_id = auth.uid())
  with check (user_id = auth.uid());

-- Clips: owner-only CRUD
drop policy if exists "clips_crud_owner" on public.clips;
create policy "clips_crud_owner"
  on public.clips for all
  using (user_id = auth.uid())
  with check (user_id = auth.uid());
