"""Chamadas ao modelo de linguagem usadas pelo corretor.

Mantido separado para que os testes injetem um LLM falso e para que o restante
do módulo não dependa dos SDKs (importados só quando necessário).
"""
from __future__ import annotations

import base64
import json
import os
import re
from typing import Callable, Protocol

from ..config import ANTHROPIC_API_KEY, LLM_PROVIDER, OPENAI_API_KEY
from ..utils import require_env, retry

GRADING_MODEL_ANTHROPIC = os.getenv("GRADING_MODEL_ANTHROPIC", "claude-sonnet-5-5")
GRADING_MODEL_OPENAI = os.getenv("GRADING_MODEL_OPENAI", "gpt-4o")


class LLM(Protocol):
    def __call__(self, system: str, user: str, image: bytes | None = None, media_type: str = "image/png") -> str: ...


def extrair_json(texto: str) -> dict:
    """Extrai o primeiro objeto JSON da resposta do modelo (tolera ```json ... ```)."""
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", texto, re.S)
    bruto = m.group(1) if m else texto[texto.find("{"): texto.rfind("}") + 1]
    if not bruto:
        raise ValueError("Resposta do modelo sem JSON")
    return json.loads(bruto)


def chamar_llm(system: str, user: str, image: bytes | None = None, media_type: str = "image/png") -> str:
    """LLM padrão, conforme LLM_PROVIDER do .env."""
    require_env()  # fora do retry: chave ausente não deve ser re-tentada
    return _chamar(system, user, image, media_type)


@retry(times=3, delay=2.0)
def _chamar(system: str, user: str, image: bytes | None, media_type: str) -> str:
    if LLM_PROVIDER == "anthropic":
        import anthropic

        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        content: list[dict] = []
        if image is not None:
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": media_type, "data": base64.b64encode(image).decode()},
            })
        content.append({"type": "text", "text": user})
        r = client.messages.create(
            model=GRADING_MODEL_ANTHROPIC, max_tokens=2048, temperature=0,
            system=system, messages=[{"role": "user", "content": content}],
        )
        return "".join(b.text for b in r.content if getattr(b, "type", "") == "text")

    from openai import OpenAI

    client = OpenAI(api_key=OPENAI_API_KEY)
    if image is not None:
        b64 = base64.b64encode(image).decode()
        user_content: list[dict] | str = [
            {"type": "text", "text": user},
            {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{b64}"}},
        ]
    else:
        user_content = user
    r = client.chat.completions.create(
        model=GRADING_MODEL_OPENAI, temperature=0,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user_content}],
    )
    return r.choices[0].message.content or ""


LLMFn = Callable[..., str]
