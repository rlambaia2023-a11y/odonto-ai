"""Histórico das correções e gabaritos, guardado em disco (um JSON por registro).

Pasta: variável HISTORICO_DIR (no Railway, um volume montado ali); padrão `data/historico`.
Data e hora gravadas no horário de Brasília (UTC-3, sem horário de verão desde 2019).
"""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

BRASILIA = timezone(timedelta(hours=-3), "BRT")
_ID_VALIDO = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$")


def pasta() -> Path:
    p = Path(os.environ.get("HISTORICO_DIR") or Path(__file__).resolve().parents[2] / "data" / "historico")
    p.mkdir(parents=True, exist_ok=True)
    return p


def _arquivo(reg_id: str) -> Path:
    if not _ID_VALIDO.match(reg_id or ""):  # evita caminho arbitrário vindo de fora
        raise ValueError(f"id de histórico inválido: {reg_id!r}")
    return pasta() / f"{reg_id}.json"


def salvar(tipo: str, titulo: str, dados: dict, agora: datetime | None = None) -> str:
    """Grava um registro e devolve o id. `dados` precisa ser serializável em JSON."""
    agora = (agora or datetime.now(BRASILIA)).astimezone(BRASILIA)
    reg_id = f"{agora:%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    reg = {"id": reg_id, "criado_em": agora.isoformat(timespec="seconds"), "tipo": tipo,
           "titulo": titulo or "Sem título", "dados": dados}
    tmp = _arquivo(reg_id).with_suffix(".tmp")
    tmp.write_text(json.dumps(reg, ensure_ascii=False), encoding="utf-8")
    tmp.replace(_arquivo(reg_id))  # escrita atômica
    return reg_id


def listar() -> list[dict]:
    """Registros do mais novo para o mais antigo; arquivos corrompidos são ignorados."""
    regs = []
    for f in pasta().glob("*.json"):
        try:
            regs.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return sorted(regs, key=lambda r: r.get("criado_em", ""), reverse=True)


def excluir(reg_id: str) -> bool:
    try:
        _arquivo(reg_id).unlink()
        return True
    except FileNotFoundError:
        return False


def excluir_tudo() -> int:
    n = 0
    for f in pasta().glob("*.json"):
        f.unlink(missing_ok=True)
        n += 1
    return n


def data_hora(reg: dict) -> str:
    """'18/11/2026 às 14:05' a partir do ISO gravado."""
    try:
        return datetime.fromisoformat(reg["criado_em"]).astimezone(BRASILIA).strftime("%d/%m/%Y às %H:%M")
    except (KeyError, ValueError):
        return "data desconhecida"
