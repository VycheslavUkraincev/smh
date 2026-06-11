# SaveMyHistory

ИИ-реставрация старых семейных фотографий.

## Структура
- `public/` — статичный фронт (лендинг + кабинет)
- `api/` — FastAPI backend (presigned upload в DO Spaces, заказы в Supabase)
- `.do/app.yaml` — спецификация деплоя на DigitalOcean App Platform

## Режимы обработки
- `restore` — базовая реставрация (массовый, доступный)
- `revive` — генеративное оживление (премиум)
