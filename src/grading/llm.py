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

GRADING_MODEL_ANTHROPIC = os.getenv("GRADING_MODEL_ANTHROPIC", "claude-opus-5-5")
# Profundidade de raciocínio do Claude: low | medium | high | xhigh | max (mais alto = mais lento e caro).
GRADING_EFFORT = os.getenv("GRADING_EFFORT", "high")
GRADING_MODEL_OPENAI = os.getenv("GRADING_MODEL_OPENAI", "gpt-4o")


Imagem = tuple[bytes, str]  # (conteúdo, media_type), ex.: (b"...", "image/jpeg")


class LLM(Protocol):
    def __call__(self, system: str, user: str, imagens: list[Imagem] | None = None) -> str: ...


def extrair_json(texto: str) -> dict:
    """Extrai o primeiro objeto JSON da resposta do modelo (tolera ```json ... ```)."""
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", texto, re.S)
    bruto = m.group(1) if m else texto[texto.find("{"): texto.rfind("}") + 1]
    if not bruto:
        raise ValueError("Resposta do modelo sem JSON")
    return json.loads(bruto)


def chamar_llm(system: str, user: str, imagens: list[Imagem] | None = None) -> str:
    """LLM padrão, conforme LLM_PROVIDER do .env. `imagens`: páginas da prova, em ordem."""
    require_env()  # fora do retry: chave ausente não deve ser re-tentada
    return _chamar(system, user, imagens or [])


@retry(times=3, delay=2.0)
def _chamar(system: str, user: str, imagens: list[Imagem]) -> str:
    if LLM_PROVIDER == "anthropic":
        import anthropic

        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        content: list[dict] = [
            {"type": "image", "source": {"type": "base64", "media_type": mt, "data": base64.b64encode(b).decode()}}
            for b, mt in imagens
        ]
        content.append({"type": "text", "text": user})
        # fallbacks="default": se o modelo recusar por política, o servidor refaz no modelo reserva.
        r = client.beta.messages.create(
            model=GRADING_MODEL_ANTHROPIC, max_tokens=16000,
            output_config={"effort": GRADING_EFFORT},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            system=system, messages=[{"role": "user", "content": content}],
        )
        if r.stop_reason == "refusal":
            raise ValueError(f"IA recusou a resposta ({getattr(r.stop_details, 'category', None)})")
        if r.stop_reason == "max_tokens":
            raise ValueError("Resposta da IA cortada (max_tokens)")
        return "".join(b.text for b in r.content if b.type == "text")

    from openai import OpenAI

    client = OpenAI(api_key=OPENAI_API_KEY)
    user_content: list[dict] | str = user
    if imagens:
        user_content = [{"type": "text", "text": user}] + [
            {"type": "image_url", "image_url": {"url": f"data:{mt};base64,{base64.b64encode(b).decode()}"}}
            for b, mt in imagens
        ]
    r = client.chat.completions.create(
        model=GRADING_MODEL_OPENAI, temperature=0, max_tokens=4096,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user_content}],
    )
    return r.choices[0].message.content or ""


LLMFn = Callable[..., str]
