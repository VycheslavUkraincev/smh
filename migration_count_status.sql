-- SaveMyHistory — RPC count_status(p_status text) → int
-- СТАТУС: STUB / НЕ ПРИМЕНЯТЬ автоматически.
-- Роман: выполнить вручную в Supabase SQL Editor (Dashboard → SQL).
-- Зачем: воркер вызывает rpc/count_status; без функции PostgREST даёт PGRST202.
-- До apply воркер тихо фолбэчится на REST Prefer:count=exact (waiting=0 без спама).

create or replace function public.count_status(p_status text)
returns integer
language sql
stable
security definer
set search_path = public
as $$
  select count(*)::integer
  from public.restorations
  where status = p_status;
$$;

grant execute on function public.count_status(text) to service_role;
grant execute on function public.count_status(text) to authenticated;

-- Проверка после apply:
-- select public.count_status('queued');
