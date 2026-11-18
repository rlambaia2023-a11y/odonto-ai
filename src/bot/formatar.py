"""Conversão do markdown do corretor para o texto de cada aplicativo e divisão em mensagens."""
from __future__ import annotations

import html
import re

LIMITE = 3800  # Telegram e WhatsApp aceitam ~4096 caracteres por mensagem; sobra margem para a formatação

_NEGRITO = re.compile(r"\*\*(.+?)\*\*", re.S)
_TITULO = re.compile(r"^#{1,6}\s*(.+)$", re.M)
_LINK_ANGULAR = re.compile(r"<(https?://[^>\s]+)>")


def para_whatsapp(md: str) -> str:
    """WhatsApp: *negrito*; títulos viram linha em negrito; links sem os sinais < >."""
    t = _LINK_ANGULAR.sub(r"\1", md)
    t = _TITULO.sub(r"*\1*", t)
    return _NEGRITO.sub(r"*\1*", t)


def para_telegram(md: str) -> str:
    """Telegram em modo HTML: escapa o texto e marca o negrito com <b>."""
    t = _LINK_ANGULAR.sub(r"\1", md)
    t = html.escape(t, quote=False)
    t = _TITULO.sub(r"<b>\1</b>", t)
    return _NEGRITO.sub(r"<b>\1</b>", t)


def dividir(texto: str, limite: int = LIMITE) -> list[str]:
    """Quebra em pedaços de até `limite`, preferindo cortar entre parágrafos e linhas.

    Funciona no texto já formatado: como os cortes caem em quebras de linha, uma marca de
    negrito (que nunca atravessa linhas no markdown do corretor) não fica aberta.
    """
    texto = texto.strip()
    if not texto:
        return []
    partes: list[str] = []
    while len(texto) > limite:
        corte = texto.rfind("\n\n", 0, limite)
        if corte < limite // 2:
            corte = texto.rfind("\n", 0, limite)
        if corte < limite // 2:
            corte = limite
        partes.append(texto[:corte].rstrip())
        texto = texto[corte:].lstrip()
    if texto:
        partes.append(texto)
    return partes


def br(x: float) -> str:
    """7.5 -> '7,5'; 8.0 -> '8'."""
    return f"{x:.2f}".rstrip("0").rstrip(".").replace(".", ",")
