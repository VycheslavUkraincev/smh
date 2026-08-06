-- SaveMyHistory — soft-waitlist table
-- СТАТУС: STUB / НЕ ПРИМЕНЯТЬ автоматически.
-- Роман: выполнить вручную в Supabase SQL Editor (Dashboard → SQL).
-- Зачем: POST /api/waitlist + GET /api/waitlist/status пишут/считают public.waitlist.
-- Поля совместимы с waitlist_add.py / WAITLIST.md (email, name, note, source).
-- Не трогает profiles / restorations / invite_codes / feedback.

create table if not exists public.waitlist (
  id uuid primary key default gen_random_uuid(),
  email text not null,
  name text,
  note text,
  source text default 'api',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- unique email (case-insensitive via lower() expression)
create unique index if not exists waitlist_email_lower_uidx
  on public.waitlist (lower(email));

create index if not exists waitlist_created_at_idx
  on public.waitlist (created_at desc);

-- updated_at touch on update
create or replace function public.waitlist_touch_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

drop trigger if exists waitlist_touch_updated_at on public.waitlist;
create trigger waitlist_touch_updated_at
  before update on public.waitlist
  for each row execute function public.waitlist_touch_updated_at();

-- RLS: anon/authenticated не читают PII; service_role (API) обходит RLS.
alter table public.waitlist enable row level security;

-- нет политик для anon/authenticated → только service_role через API

grant select, insert, update on public.waitlist to service_role;

-- Проверка после apply:
-- insert into public.waitlist (email, source) values ('test@example.com', 'sql');
-- select count(*) from public.waitlist;
