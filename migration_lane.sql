-- SaveMyHistory — миграция lane (очередь overnight vs realtime)
-- СТАТУС: STUB / НЕ ПРИМЕНЯТЬ автоматически.
-- Нужен доступ Романа к Supabase SQL Editor (секреты не в чате).
-- После apply: API уже умеет писать lane; воркер пока берёт все analyzed
-- одинаково — per-row lane claim = следующий шаг.

-- колонка очереди: overnight (free default) | realtime (paid later)
alter table public.restorations
  add column if not exists lane text not null default 'overnight';

-- ограничение значений (мягкое: check, не enum — проще расширять)
do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'restorations_lane_check'
  ) then
    alter table public.restorations
      add constraint restorations_lane_check
      check (lane in ('overnight', 'realtime'));
  end if;
end $$;

create index if not exists restorations_lane_status_idx
  on public.restorations(lane, status);

-- Опционально позже: claim_restorations_by_lane(p_from, p_to, p_lane, p_limit)
-- чтобы paid realtime не ждал ночной батч при INTAKE_ONLY=1.
