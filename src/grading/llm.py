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
# Endereço fixo: não herda ANTHROPIC_BASE_URL de outros programas da máquina (ex.: proxies de ferramentas).
GRADING_ANTHROPIC_BASE_URL = os.getenv("GRADING_ANTHROPIC_BASE_URL", "https://api.anthropic.com")


Imagem = tuple[bytes, str]  # (conteúdo, media_type), ex.: (b"...", "image/jpeg")


class LLM(Protocol):
    def __call__(self, system: str, user: str, imagens: list[Imagem] | None = None) -> str: ...


def extrair_json(texto: str) -> dict:
    """Extrai o objeto JSON da resposta do modelo (tolera ```json ... ``` e texto antes/depois).

    Com pesquisa na web o modelo pode escrever comentários antes do JSON; por isso, se o
    recorte simples falhar, procura o maior objeto JSON válido no texto.
    """
    blocos = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", texto, re.S)
    for bruto in reversed(blocos):  # o último bloco costuma ser a resposta final
        try:
            return json.loads(bruto)
        except json.JSONDecodeError:
            continue
    bruto = texto[texto.find("{"): texto.rfind("}") + 1]
    if not bruto:
        raise ValueError("Resposta do modelo sem JSON")
    try:
        return json.loads(bruto)
    except json.JSONDecodeError:
        pass
    dec, melhor = json.JSONDecoder(), None
    for m in re.finditer(r"\{", texto):
        try:
            obj, fim = dec.raw_decode(texto, m.start())
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and (melhor is None or fim - m.start() > melhor[1]):
            melhor = (obj, fim - m.start())
    if melhor is None:
        raise ValueError("Resposta do modelo sem JSON válido")
    return melhor[0]


# Pesquisa na web (servidor da Anthropic): quantas buscas/leituras de página por chamada.
GRADING_WEB_MAX_BUSCAS = int(os.getenv("GRADING_WEB_MAX_BUSCAS", "20"))
GRADING_WEB_MAX_PAGINAS = int(os.getenv("GRADING_WEB_MAX_PAGINAS", "10"))
_MAX_CONTINUACOES = 6  # pause_turn: o servidor pausa buscas longas; retomamos até este limite


def chamar_llm(system: str, user: str, imagens: list[Imagem] | None = None, pesquisa_web: bool = False) -> str:
    """LLM padrão, conforme LLM_PROVIDER do .env. `imagens`: páginas da prova, em ordem.

    `pesquisa_web=True` (só Claude) libera busca e leitura de páginas na internet para
    cruzar as respostas com literatura e bancos de questões.
    """
    require_env()  # fora do retry: chave ausente não deve ser re-tentada
    if pesquisa_web and LLM_PROVIDER == "anthropic":
        return _chamar_com_web(system, user, imagens or [])
    return _chamar(system, user, imagens or [])


def _conteudo_anthropic(user: str, imagens: list[Imagem]) -> list[dict]:
    content: list[dict] = [
        {"type": "image", "source": {"type": "base64", "media_type": mt, "data": base64.b64encode(b).decode()}}
        for b, mt in imagens
    ]
    content.append({"type": "text", "text": user})
    return content


def _checar_parada(r) -> None:
    if r.stop_reason == "refusal":
        raise ValueError(f"IA recusou a resposta ({getattr(r.stop_details, 'category', None)})")
    if r.stop_reason == "max_tokens":
        raise ValueError("Resposta da IA cortada (max_tokens)")


@retry(times=2, delay=3.0)
def _chamar_com_web(system: str, user: str, imagens: list[Imagem]) -> str:
    """Claude com web_search/web_fetch no servidor. Retoma `pause_turn` e devolve o texto final."""
    import anthropic

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY, base_url=GRADING_ANTHROPIC_BASE_URL)
    tools = [
        {"type": "web_search_20260209", "name": "web_search", "max_uses": GRADING_WEB_MAX_BUSCAS},
        {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": GRADING_WEB_MAX_PAGINAS},
    ]
    messages: list[dict] = [{"role": "user", "content": _conteudo_anthropic(user, imagens)}]
    for _ in range(_MAX_CONTINUACOES + 1):
        # streaming: buscas + raciocínio podem passar do tempo limite de uma chamada comum
        with client.beta.messages.stream(
            model=GRADING_MODEL_ANTHROPIC, max_tokens=64000,
            output_config={"effort": GRADING_EFFORT},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            system=system, messages=messages, tools=tools,
        ) as stream:
            r = stream.get_final_message()
        if r.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": r.content})
            continue
        _checar_parada(r)
        return "".join(b.text for b in r.content if b.type == "text")
    raise ValueError("Pesquisa na web não terminou (muitas pausas); tente de novo")


@retry(times=3, delay=2.0)
def _chamar(system: str, user: str, imagens: list[Imagem]) -> str:
    if LLM_PROVIDER == "anthropic":
        import anthropic

        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY, base_url=GRADING_ANTHROPIC_BASE_URL)
        content = _conteudo_anthropic(user, imagens)
        # fallbacks="default": se o modelo recusar por política, o servidor refaz no modelo reserva.
        r = client.beta.messages.create(
            model=GRADING_MODEL_ANTHROPIC, max_tokens=16000,
            output_config={"effort": GRADING_EFFORT},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            system=system, messages=[{"role": "user", "content": content}],
        )
        _checar_parada(r)
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
