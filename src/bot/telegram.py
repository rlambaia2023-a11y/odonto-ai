"""Adaptador do Telegram (Bot API) por long polling: não precisa de endereço público nem webhook."""
from __future__ import annotations

import logging
import threading

import httpx

from .formatar import dividir, para_telegram
from .nucleo import Arquivo, Bot

logger = logging.getLogger(__name__)

MAX_DOWNLOAD = 20 * 1024 * 1024  # limite da Bot API para o bot baixar arquivos


class Telegram:
    nome = "telegram"

    def __init__(self, token: str, http: httpx.Client | None = None):
        self._api = f"https://api.telegram.org/bot{token}"
        self._arquivos = f"https://api.telegram.org/file/bot{token}"
        self.http = http or httpx.Client(timeout=httpx.Timeout(70.0, connect=15.0))

    def _post(self, metodo: str, **kw) -> dict:
        r = self.http.post(f"{self._api}/{metodo}", **kw)
        dados = r.json()
        if not dados.get("ok"):
            raise RuntimeError(f"Telegram {metodo}: {dados.get('description', r.status_code)}")
        return dados["result"]

    # ---------- Envio ----------

    def enviar_texto(self, chat: str, md: str) -> None:
        for parte in dividir(para_telegram(md)):
            try:
                self._post("sendMessage", json={"chat_id": chat, "text": parte, "parse_mode": "HTML",
                                                "link_preview_options": {"is_disabled": True}})
            except RuntimeError as e:  # HTML recusado: manda sem formatação para não perder o conteúdo
                logger.warning("Reenviando sem formatação: %s", e)
                self._post("sendMessage", json={"chat_id": chat, "text": parte.replace("<b>", "").replace("</b>", "")})

    def enviar_arquivo(self, chat: str, nome: str, dados: bytes, mime: str) -> None:
        self._post("sendDocument", data={"chat_id": chat}, files={"document": (nome, dados, mime)})

    # ---------- Recebimento ----------

    def baixar(self, file_id: str) -> bytes:
        info = self._post("getFile", json={"file_id": file_id})
        r = self.http.get(f"{self._arquivos}/{info['file_path']}")
        r.raise_for_status()
        return r.content

    def entrada(self, msg: dict) -> tuple[str, str, list[Arquivo]]:
        """Mensagem do Telegram -> (chat, texto, arquivos)."""
        chat = str(msg["chat"]["id"])
        texto = msg.get("text") or msg.get("caption") or ""
        arquivos: list[Arquivo] = []
        if msg.get("photo"):
            foto = msg["photo"][-1]  # maior resolução disponível
            arquivos.append(Arquivo(f"foto_{msg.get('message_id', 0)}.jpg", "image/jpeg", self.baixar(foto["file_id"])))
        elif msg.get("document"):
            doc = msg["document"]
            if (doc.get("file_size") or 0) > MAX_DOWNLOAD:
                raise ValueError(f"{doc.get('file_name', 'arquivo')} passa de 20MB, o limite do Telegram para bots. "
                                 "Divida o PDF ou use o site.")
            arquivos.append(Arquivo(doc.get("file_name") or f"arquivo_{msg.get('message_id', 0)}",
                                    doc.get("mime_type") or "application/octet-stream", self.baixar(doc["file_id"])))
        return chat, texto, arquivos

    def tratar(self, bot: Bot, update: dict) -> None:
        msg = update.get("message")
        if not msg or "chat" not in msg:
            return
        chat = str(msg["chat"]["id"])
        if not bot.autorizado(self.nome, chat):  # não baixa arquivos de quem não pode usar
            bot.receber(self, chat, msg.get("text") or "")
            return
        try:
            chat, texto, arquivos = self.entrada(msg)
        except Exception as e:  # noqa: BLE001
            logger.exception("Falha ao ler mensagem do Telegram")
            self.enviar_texto(chat, f"Não consegui baixar o arquivo: {e}")
            return
        bot.receber(self, chat, texto, arquivos)

    def rodar(self, bot: Bot, parar: threading.Event) -> None:
        """Laço de long polling; roda numa thread até `parar` ser acionado."""
        try:
            self._post("deleteWebhook", json={"drop_pending_updates": False})
        except Exception:  # noqa: BLE001
            logger.exception("Telegram: falha ao remover webhook")
        offset = 0
        logger.info("Telegram: escutando mensagens")
        while not parar.is_set():
            try:
                updates = self._post("getUpdates", json={"offset": offset, "timeout": 50, "allowed_updates": ["message"]})
            except Exception:  # noqa: BLE001 - rede instável: espera e tenta de novo
                logger.exception("Telegram: falha no getUpdates")
                parar.wait(5)
                continue
            for u in updates:
                offset = max(offset, u["update_id"] + 1)
                try:
                    self.tratar(bot, u)
                except Exception:  # noqa: BLE001
                    logger.exception("Telegram: falha ao tratar update")
