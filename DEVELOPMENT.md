# SaveMyHistory — Гид разработчика

Этот файл — точка входа в код для любого разработчика (или для работы через Cursor / GitHub Copilot).
Здесь: что где лежит, на каком этапе проект, что делать дальше, как запускать локально.

---

## 1. СТРУКТУРA РЕПОЗИТОРИЯ

```
smh/
├── public/              # статичный фронтенд (деплоится как static site)
│   ├── index.html       # лендинг (3 языка RU/UA/EN, i18n в <script>)
│   ├── cabinet.html     # кабинет: логин, загрузка, очередь, галерея до/после,
│   │                    #   инвайт-код + согласие, форма фидбека
│   └── app.html         # (вспомогательная страница)
├── api/                 # backend (FastAPI), деплой на DO App Platform
│   ├── main.py          # все эндпоинты
│   └── requirements.txt
├── .do/app.yaml         # спецификация деплоя DigitalOcean (api + static web)
├── README.md
└── DEVELOPMENT.md       # этот файл
```

**Деплой автоматический:** push в `main` → DigitalOcean пересобирает (`deploy_on_push: true`).
Фронт — статика из `/public`. API — `/api`, запуск `uvicorn main:app`.

---

## 2. АРХИТЕКТУРА (как всё связано)

```
Браузер (cabinet.html)
   │  Google OAuth → Supabase Auth → access_token
   │
   ├─ POST /api/upload-url     → presigned URL → грузим фото прямо в DO Spaces
   ├─ POST /api/restorations   → создаём заказ (status=queued) в Supabase
   ├─ POST /api/redeem-code    → активируем инвайт-код (+ согласие)
   ├─ POST /api/feedback       → отзыв
   └─ GET  /api/restorations   → список заказов + presigned ссылки до/после
                                   │
                                   ▼
                          [ОЧЕРЕДЬ в Supabase: restorations]
                                   │
                                   ▼   ◄── ВОРКЕР (см. раздел 4 — ГЛАВНЫЙ ПРОБЕЛ)
                          обработка фото → результат в DO Spaces → status=done
```

**Сервисы:**
- **Supabase** — Postgres + Auth + RLS. Таблицы: `profiles`, `restorations`, `invite_codes`, `feedback`. RPC: `redeem_invite`.
- **DO Spaces** — приватный бакет `smh-photos` (S3-совместимый). Доступ только через presigned URL с TTL.
- **DO App Platform** — хостинг api + static.

---

## 3. ЭНДПОИНТЫ API (`api/main.py`)

| Метод | Путь | Назначение | Auth |
|---|---|---|---|
| GET | `/api/health` | проверка живости | нет |
| GET | `/api/me` | юзер + free_quota/used_quota/consent | Bearer |
| POST | `/api/upload-url` | presigned URL для загрузки фото | Bearer |
| POST | `/api/restorations` | создать заказ (проверка квоты) | Bearer |
| GET | `/api/restorations` | список заказов + ссылки | Bearer |
| POST | `/api/restorations/{id}/retry` | повтор упавшего | Bearer |
| POST | `/api/restorations/{id}/report` | пожаловаться на результат | Bearer |
| POST | `/api/redeem-code` | активировать инвайт-код (нужно consent=true) | Bearer |
| POST | `/api/feedback` | оставить отзыв | Bearer |

**Env (задаются в DO как секреты):** `SUPABASE_URL`, `SUPABASE_SECRET`, `SPACES_KEY`, `SPACES_SECRET`, `SPACES_REGION`, `SPACES_BUCKET`, `FREE_LIMIT`.

---

## 4. ЭТАП ПРОЕКТА — ЧЕСТНО

### ✅ Готово и в проде
- Фронт: лендинг + кабинет, 3 языка, Google-логин, загрузка, очередь, галерея до/после.
- API: все эндпоинты выше, включая инвайты/согласие/фидбек.
- БД: таблицы + RLS + RPC активации кодов. 100 инвайт-кодов в базе.
- Реставрация как процесс отлажена на 283 фото (архив «Онуфрий»).

### ⚠️ ГЛАВНЫЙ ПРОБЕЛ — нет воркера очереди В ПРОДЕ
Сейчас заказ встаёт в очередь (`status=queued`), но **в репозитории НЕТ сервиса, который автоматически берёт очередь и обрабатывает фото**. Обработка пока запускается вручную скриптом вне репо.

→ **Задача №1:** перенести воркер в репозиторий как постоянный сервис.
Прототип воркера уже написан (логика fal + Supabase + Spaces) — его нужно оформить в `worker/` и задеплоить как DO Worker (или RunPod для GPU-режима).

### 🔜 Дальше по плану (из Мастер-плана)
1. **Воркер в прод** (API-режим на fal) — обрабатывает очередь автоматически.
2. **Двухслойный движок лиц:** CodeFormer (лица из оригинала) + генеративная (фон/цвет). См. ниже.
3. **GPU-режим:** RunPod serverless, веса на Network Volume, оркестратор очереди.
4. **Google Photos Picker** — выбор фото без ручной загрузки.
5. Кнопка «удалить фото» (GDPR), rate-limit.
6. Email-уведомления «готово».

---

## 5. ДВИЖОК РЕСТАВРАЦИИ (ключевое решение)

Два слоя, каждый своей моделью:
- **Лица → CodeFormer** (`fal-ai/codeformer`, `fidelity` 0.8-0.9), применяется к **ОРИГИНАЛУ**.
- **Фон/цвет/повреждения → генеративная** (сейчас nano-banana через fal; кандидаты для GPU: SUPIR, Qwen-Image-Edit).

**Критично:** CodeFormer по оригиналу, НЕ поверх генеративного результата (иначе чиним уже искажённое лицо).

**Два режима:**
- **API** (fal): ~$0.085/фото, ноль инфраструктуры. Для старта.
- **GPU** (RunPod): ~$0.003/фото + аренда карты. Для объёма.

**Грабли (учтено):** генеративные модели идеализируют лица; гигантские исходники (>~50МП) ломают API — нужен ресайз на входе; большие батчи нестабильны — дробить.

---

## 6. КАК РАБОТАТЬ С КОДОМ БЕЗ ПОМОЩИ АССИСТЕНТА (Cursor / Copilot)

### Шаг 1. Склонировать репозиторий
```bash
git clone https://github.com/VycheslavUkraincev/smh.git
cd smh
```
(Доступ к репо — у тебя как у владельца аккаунта GitHub.)

### Шаг 2. Открыть в Cursor
- File → Open Folder → выбрать папку `smh`.
- Cursor сам прочитает `DEVELOPMENT.md` и `README.md` — это контекст для ИИ.
- Открой чат Cursor (Cmd/Ctrl+L) и можешь спрашивать: *«объясни api/main.py»*, *«добавь эндпоинт X»*, *«напиши воркер очереди по разделу 4»*.

### Шаг 3. Запустить фронт локально (просто статика)
```bash
cd public
python3 -m http.server 5500
# открыть http://localhost:5500/index.html
```
(API-вызовы пойдут на прод, если не менять адрес.)

### Шаг 4. Запустить API локально (опционально)
```bash
cd api
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# нужны env-переменные (SUPABASE_URL и т.д.) — взять из DO
export SUPABASE_URL=... SUPABASE_SECRET=... SPACES_KEY=... SPACES_SECRET=...
uvicorn main:app --reload --port 8080
```

### Шаг 5. Внести изменения и задеплоить
```bash
git add -A
git commit -m "что изменил"
git push          # DigitalOcean пересоберёт автоматически
```

### Подсказки для Cursor/Copilot (промпты, которые работают)
- *«Прочитай DEVELOPMENT.md раздел 4 и напиши воркер: бери restorations со status=queued, обрабатывай через fal-ai/codeformer + nano-banana, клади результат в DO Spaces, ставь status=done. Атомарно: queued→processing.»*
- *«Добавь в api/main.py эндпоинт DELETE /api/restorations/{id} — удаляет заказ и файл из Spaces, только своё.»*
- *«Добавь rate-limit: не больше N заказов в час на юзера.»*

### Важные правила (чтобы не сломать)
- **Service-ключ Supabase — только на сервере** (`api/`), никогда в `public/` (это утечка).
- **Бакет приватный** — отдавать фото только presigned URL с TTL.
- **i18n:** любой видимый текст на фронте — через `data-i18n` (и `data-i18n-ph` для placeholder), иначе не переведётся (был баг «2 мин» вместо «2 хв» в UA).
- **Большие фото** ресайзить до ~30-50МП перед обработкой.

---

## 6b. ВОРКЕР ОЧЕРЕДИ (worker/) — автономная обработка

3-стадийный пайплайн «ИИ-глаза»:
```
queued ──analyze.py──► analyzed ──generate.py──► generated ──verify.py──► done
  (ИИ-глаза: анализ+промпт)   (CodeFormer+генератив)    (проверка лиц)
```
- `worker/run.py` — раннер всех стадий. Постоянный режим: `python run.py` (цикл, `LOOP_INTERVAL` сек). Разовый: `python run.py --once`.
- `worker/analyze.py` `generate.py` `verify.py` — стадии 1/2/3.
- `worker/common.py` — хелперы (db/spaces/vision).
- `worker/healthcheck.py` — проверка готовности: `python healthcheck.py` (база, Spaces, RPC, ключи).
- `worker/migration_eyes.sql` — миграция 2 (применить в Supabase до запуска воркера).

**ENV воркера:** `SUPABASE_URL`, `SUPABASE_SECRET`, `SPACES_*`, `OPENAI_API_KEY`, `FAL_KEY`, `LOOP_INTERVAL`.

**Деплой:** в `.do/app.yaml` воркер описан как постоянный `workers:` сервис (деплоится с push).

**Переход на GPU:** заменить тело `generate.py` на вызов RunPod (тот же контракт статусов analyzed→generated). Стадии 1 и 3 остаются на API.

---

## 7. ГДЕ ЛЕЖАТ ПЛАНЫ (Obsidian / Dropbox)
`/Obsidian/SaveMyHistory-Vault/`:
- `03 Архитектура/Движок реставрации (API+GPU+лица).md`
- `03 Архитектура/migration_invites.sql`
- `06 Решения и планы/Мастер-план сервиса.md`
- `06 Решения и планы/План запуска сервиса (GTM).md`
- `06 Решения и планы/Бриф для коллеги-стратега.md`
