-- SaveMyHistory — миграция 2: ИИ-глаза (предобработка очереди)
-- 2026-06-12. Безопасно поверх существующей схемы (idempotent).

-- поля для 3-стадийного пайплайна
alter table public.restorations
  add column if not exists analysis jsonb,      -- что увидели ИИ-глаза (тип, повреждения, лица, режим)
  add column if not exists prompt text,         -- готовый промпт генерации (стадия 1 → стадия 2)
  add column if not exists qc jsonb,            -- результат ИИ-проверки (стадия 3)
  add column if not exists attempts int default 0,  -- сколько раз пытались сгенерировать
  add column if not exists analyzed_at timestamptz,
  add column if not exists generated_at timestamptz;

-- индекс для воркеров: быстро брать фото нужной стадии
create index if not exists restorations_status_idx2 on public.restorations(status);

-- статусы (документация, не enum чтобы не ломать существующее):
--   uploaded → queued → analyzed → processing → generated → done
--   ветки: needs_review (провал QC), failed (фатально)

-- хелпер: атомарно взять пачку фото нужного статуса и пометить переход
-- (вызывается серверным ключом из воркера)
create or replace function public.claim_restorations(p_from text, p_to text, p_limit int)
returns setof public.restorations language plpgsql security definer as $$
begin
  return query
  update public.restorations r
     set status = p_to, updated_at = now()
   where r.id in (
     select id from public.restorations
      where status = p_from
      order by created_at asc
      limit p_limit
      for update skip locked
   )
  returning r.*;
end; $$;

-- recovery: вернуть зависшие в processing дольше 30 мин обратно в analyzed (до 3 попыток)
create or replace function public.recover_stuck()
returns int language plpgsql security definer as $$
declare n int;
begin
  -- зависший анализ → обратно в queued
  update public.restorations
     set status = 'queued', updated_at = now()
   where status = 'processing_analyze'
     and updated_at < now() - interval '30 minutes';
  -- зависшая генерация → обратно в analyzed (или failed после 3 попыток)
  update public.restorations
     set status = case when attempts >= 3 then 'failed' else 'analyzed' end,
         updated_at = now()
   where status = 'processing'
     and updated_at < now() - interval '30 minutes';
  -- зависшая проверка → обратно в generated (повторить QC)
  update public.restorations
     set status = 'generated', updated_at = now()
   where status = 'processing_verify'
     and updated_at < now() - interval '30 minutes';
  get diagnostics n = row_count;
  return n;
end; $$;
