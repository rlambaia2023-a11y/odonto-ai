#!/bin/sh
# Sobe o bot (Telegram + webhook do WhatsApp) em segundo plano e o site Streamlit em primeiro plano.
# O site fica na porta do Railway ($PORT); o bot na BOT_PORT, exposta por um segundo domínio.
uvicorn src.bot.app:app --host 0.0.0.0 --port "${BOT_PORT:-8081}" --no-access-log &
exec streamlit run frontend/pages/1_Corrigir_provas.py \
    --server.port "${PORT:-8501}" --server.address 0.0.0.0 \
    --server.headless true --server.maxUploadSize 300 \
    --browser.gatherUsageStats false
