"""Adaptador do WhatsApp pela Cloud API oficial da Meta (webhook)."""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
from collections import OrderedDict

import httpx

from .formatar import dividir, para_whatsapp
from .nucleo import Arquivo, Bot

logger = logging.getLogger(__name__)

GRAPH = "https://graph.facebook.com/" + os.getenv("WHATSAPP_API_VERSION", "v21.0")
# Tipos de documento que o WhatsApp aceita enviar; o resto (CSV, JSON) vai como texto simples
MIMES_DOC = {"text/plain", "application/pdf", "application/msword", "application/vnd.ms-excel",
             "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}


def assinatura_valida(segredo: str, corpo: bytes, cabecalho: str) -> bool:
    """Confere o X-Hub-Signature-256 que a Meta manda em cada webhook."""
    if not segredo or not cabecalho.startswith("sha256="):
        return False
    esperado = hmac.new(segredo.encode(), corpo, hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperado, cabecalho.removeprefix("sha256="))


class WhatsApp:
    nome = "whatsapp"

    def __init__(self, token: str, phone_number_id: str, http: httpx.Client | None = None):
        self.phone_number_id = phone_number_id
        self.http = http or httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0),
                                         headers={"Authorization": f"Bearer {token}"})
        self._vistos: OrderedDict[str, None] = OrderedDict()  # a Meta pode reenviar o mesmo evento

    def _post(self, caminho: str, **kw) -> dict:
        r = self.http.post(f"{GRAPH}/{caminho}", **kw)
        if r.status_code >= 400:
            raise RuntimeError(f"WhatsApp {caminho}: {r.status_code} {r.text[:300]}")
        return r.json()

    # ---------- Envio ----------

    def enviar_texto(self, chat: str, md: str) -> None:
        for parte in dividir(para_whatsapp(md)):
            self._post(f"{self.phone_number_id}/messages", json={
                "messaging_product": "whatsapp", "to": chat, "type": "text",
                "text": {"body": parte, "preview_url": False},
            })

    def enviar_arquivo(self, chat: str, nome: str, dados: bytes, mime: str) -> None:
        mime = mime if mime in MIMES_DOC else "text/plain"
        media = self._post(f"{self.phone_number_id}/media", data={"messaging_product": "whatsapp", "type": mime},
                           files={"file": (nome, dados, mime)})
        self._post(f"{self.phone_number_id}/messages", json={
            "messaging_product": "whatsapp", "to": chat, "type": "document",
            "document": {"id": media["id"], "filename": nome},
        })

    # ---------- Recebimento ----------

    def baixar(self, media_id: str) -> bytes:
        r = self.http.get(f"{GRAPH}/{media_id}")
        r.raise_for_status()
        arq = self.http.get(r.json()["url"])
        arq.raise_for_status()
        return arq.content

    def _novo(self, msg_id: str) -> bool:
        if not msg_id or msg_id in self._vistos:
            return not msg_id
        self._vistos[msg_id] = None
        while len(self._vistos) > 1000:
            self._vistos.popitem(last=False)
        return True

    def entrada(self, msg: dict) -> tuple[str, list[Arquivo]] | None:
        """Mensagem do WhatsApp -> (texto, arquivos); None se o tipo não for suportado."""
        tipo = msg.get("type")
        if tipo == "text":
            return msg.get("text", {}).get("body", ""), []
        if tipo == "image":
            img = msg["image"]
            mime = img.get("mime_type", "image/jpeg").split(";")[0]
            ext = {"image/png": ".png", "image/webp": ".webp"}.get(mime, ".jpg")
            return img.get("caption", ""), [Arquivo(f"foto_{msg.get('id', '')[-8:]}{ext}", mime, self.baixar(img["id"]))]
        if tipo == "document":
            doc = msg["document"]
            mime = doc.get("mime_type", "application/octet-stream").split(";")[0]
            return doc.get("caption", ""), [Arquivo(doc.get("filename") or "arquivo", mime, self.baixar(doc["id"]))]
        return None

    def tratar(self, bot: Bot, payload: dict) -> None:
        """Processa o corpo do webhook (pode trazer várias mensagens)."""
        for entry in payload.get("entry") or []:
            for change in entry.get("changes") or []:
                valor = change.get("value") or {}
                if str((valor.get("metadata") or {}).get("phone_number_id", self.phone_number_id)) != str(self.phone_number_id):
                    continue
                for msg in valor.get("messages") or []:
                    if not self._novo(msg.get("id", "")):
                        continue
                    chat = msg.get("from", "")
                    if not bot.autorizado(self.nome, chat):
                        bot.receber(self, chat, (msg.get("text") or {}).get("body", ""))
                        continue
                    try:
                        conteudo = self.entrada(msg)
                    except Exception as e:  # noqa: BLE001
                        logger.exception("Falha ao baixar mídia do WhatsApp")
                        self.enviar_texto(chat, f"Não consegui baixar o arquivo: {e}")
                        continue
                    if conteudo is None:
                        self.enviar_texto(chat, "Por aqui eu entendo texto, fotos e documentos (PDF, Word).")
                        continue
                    texto, arquivos = conteudo
                    bot.receber(self, chat, texto, arquivos)
