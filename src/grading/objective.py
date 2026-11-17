"""Correção determinística de questões objetivas (sem IA, instantânea)."""
from __future__ import annotations

import re
import unicodedata

from .models import Questao, ResultadoQuestao

# "a", "A)", "(b)", "letra c", "alternativa D", "Resposta: e"
_LETRA = re.compile(r"^(?:resposta|resp|letra|alternativa|alt)?\s*[:\-]?\s*\(?\s*([a-j])\s*[\)\.\-]?\s*$")


def _sem_acento(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def normalizar_letra(resposta: str | None) -> str | None:
    """Extrai a alternativa marcada. Retorna None se em branco ou ambígua."""
    if resposta is None:
        return None
    s = _sem_acento(resposta).strip().lower()
    if not s:
        return None
    m = _LETRA.match(s)
    return m.group(1).upper() if m else None


def corrigir_objetiva(q: Questao, resposta: str | None) -> ResultadoQuestao:
    corretas = {normalizar_letra(g) for g in (q.gabarito or "").split(",")} - {None}
    marcada = normalizar_letra(resposta)
    bruto = (resposta or "").strip()

    if not bruto:
        return ResultadoQuestao(id=q.id, tipo=q.tipo, nota=0, valor=q.valor, feedback="Em branco.")
    if marcada is None:
        # Ex.: "A e C", rasura transcrita, texto livre: o professor decide.
        return ResultadoQuestao(
            id=q.id, tipo=q.tipo, nota=0, valor=q.valor,
            feedback=f"Resposta não reconhecida: '{bruto}'.",
            revisar=True, motivo_revisao="Marcação ambígua ou ilegível", confianca=0.0,
        )
    if marcada in corretas:
        return ResultadoQuestao(id=q.id, tipo=q.tipo, nota=q.valor, valor=q.valor, feedback=f"Correta ({marcada}).")
    return ResultadoQuestao(
        id=q.id, tipo=q.tipo, nota=0, valor=q.valor,
        feedback=f"Marcou {marcada}; gabarito {', '.join(sorted(corretas))}.",
    )
