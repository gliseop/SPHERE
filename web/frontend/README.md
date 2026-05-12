# SPHERE frontend

React/Vite-клиент веб-интерфейса SPHERE. Приложение подключается к FastAPI backend из `web/backend`, показывает граф агентов, ленту событий, сценарии, прогоны и панели управления симуляцией.

## Команды

```bash
npm install
npm run dev
npm run build
npm run test:e2e
```

Для полного локального запуска удобнее использовать общий launcher из корня веб-слоя:

```bash
cd ..
bash start.sh
```

Основная документация по web UI и API находится в [../../docs/web_interface.md](../../docs/web_interface.md).
