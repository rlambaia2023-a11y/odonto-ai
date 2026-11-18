"""Servidor do bot: webhook do WhatsApp + long polling do Telegram em segundo plano.

Rodar: uvicorn src.bot.app:app --host 0.0.0.0 --port 8081 (no Railway: serviço "bot", Dockerfile.bot)
Variáveis: TELEGRAM_BOT_TOKEN, BOT_TELEGRAM_IDS; WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID,
WHATSAPP_VERIFY_TOKEN, WHATSAPP_APP_SECRET, BOT_WHATSAPP_NUMEROS. Canal sem token fica desligado.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from .nucleo import bot_padrao
from .telegram import Telegram
from .whatsapp import WhatsApp, assinatura_valida

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

_parar = threading.Event()
_whatsapp: WhatsApp | None = None


@asynccontextmanager
async def _ciclo(app: FastAPI):
    global _whatsapp
    token_tg = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if token_tg:
        threading.Thread(target=Telegram(token_tg).rodar, args=(bot_padrao(), _parar), daemon=True, name="telegram").start()
    else:
        logger.info("Telegram desligado (sem TELEGRAM_BOT_TOKEN)")
    token_wa = os.getenv("WHATSAPP_TOKEN", "").strip()
    pnid = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "").strip()
    if token_wa and pnid:
        _whatsapp = WhatsApp(token_wa, pnid)
    else:
        logger.info("WhatsApp desligado (sem WHATSAPP_TOKEN/WHATSAPP_PHONE_NUMBER_ID)")
    yield
    _parar.set()


app = FastAPI(title="Corretor - bot", lifespan=_ciclo, docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/health")
def health() -> dict:
    return {"ok": True, "telegram": bool(os.getenv("TELEGRAM_BOT_TOKEN")), "whatsapp": _whatsapp is not None}


@app.get("/whatsapp/webhook", response_class=PlainTextResponse)
def verificar_whatsapp(
    modo: str = Query("", alias="hub.mode"),
    token: str = Query("", alias="hub.verify_token"),
    desafio: str = Query("", alias="hub.challenge"),
) -> str:
    esperado = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
    if modo == "subscribe" and esperado and token == esperado:
        return desafio
    raise HTTPException(403, "token de verificação inválido")


@app.post("/whatsapp/webhook")
async def receber_whatsapp(request: Request, tarefas: BackgroundTasks) -> dict:
    if _whatsapp is None:
        raise HTTPException(503, "WhatsApp não configurado")
    corpo = await request.body()
    if not assinatura_valida(os.getenv("WHATSAPP_APP_SECRET", ""), corpo, request.headers.get("x-hub-signature-256", "")):
        raise HTTPException(401, "assinatura inválida")
    try:
        payload = json.loads(corpo)
    except ValueError:
        raise HTTPException(400, "JSON inválido") from None
    # Responde 200 na hora (senão a Meta reenvia); baixar mídia e responder fica para depois
    tarefas.add_task(_whatsapp.tratar, bot_padrao(), payload)
    return {"ok": True}
