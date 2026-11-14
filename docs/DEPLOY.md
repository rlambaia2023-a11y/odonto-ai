# Deploy OdontoAI

## Docker Compose
```bash
docker compose up -d
# API: http://localhost:8000
# UI:  http://localhost:8501
```

## Atualização diária
Agendar `python scheduler/daily.py` via cron (Linux) ou Task Scheduler (Windows).

## Variáveis obrigatórias (.env)
OPENAI_API_KEY ou ANTHROPIC_API_KEY, API_KEY (opcional).
```