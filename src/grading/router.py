"""Endpoints HTTP do corretor (montados em /grading pelo api.py)."""
from __future__ import annotations

import json
import os
import secrets

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field, ValidationError

from .grader import corrigir_turma, ler_respostas_csv, resultados_csv, resumo_turma, transcrever_prova
from .models import Entrega, Gabarito, ResultadoAluno

MAX_IMAGEM = 10 * 1024 * 1024
TIPOS_IMAGEM = {"image/png", "image/jpeg", "image/webp"}


def exigir_chave(x_api_key: str = Header(default="")) -> None:
    chave = os.getenv("API_KEY", "")
    if chave and not secrets.compare_digest(x_api_key, chave):
        raise HTTPException(status_code=401, detail="invalid api key")


router = APIRouter(prefix="/grading", tags=["correção de provas"], dependencies=[Depends(exigir_chave)])


class PedidoCorrecao(BaseModel):
    gabarito: Gabarito
    entregas: list[Entrega] = Field(min_length=1)
    usar_literatura: bool = False


class RespostaCorrecao(BaseModel):
    resumo: dict
    resultados: list[ResultadoAluno]


def _gabarito(bruto: bytes) -> Gabarito:
    try:
        return Gabarito.model_validate(json.loads(bruto.decode("utf-8-sig")))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise HTTPException(422, f"Gabarito não é um JSON válido: {e}")
    except ValidationError as e:
        raise HTTPException(422, f"Gabarito inválido: {e.errors(include_url=False)}")


@router.post("/corrigir", response_model=RespostaCorrecao)
def corrigir(p: PedidoCorrecao) -> RespostaCorrecao:
    res = corrigir_turma(p.gabarito, p.entregas, usar_literatura=p.usar_literatura)
    return RespostaCorrecao(resumo=resumo_turma(res), resultados=res)


@router.post("/corrigir-planilha")
async def corrigir_planilha(
    gabarito: UploadFile = File(..., description="gabarito.json"),
    respostas: UploadFile = File(..., description="respostas.csv"),
    usar_literatura: bool = Form(False),
) -> Response:
    gab = _gabarito(await gabarito.read())
    try:
        entregas = ler_respostas_csv(await respostas.read())
    except (ValueError, UnicodeDecodeError) as e:
        raise HTTPException(422, f"Planilha de respostas inválida: {e}")
    res = corrigir_turma(gab, entregas, usar_literatura=usar_literatura)
    return Response(
        content=resultados_csv(gab, res).encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="notas.csv"'},
    )


@router.post("/transcrever", response_model=Entrega)
async def transcrever(gabarito: UploadFile = File(...), imagem: UploadFile = File(...)) -> Entrega:
    gab = _gabarito(await gabarito.read())
    tipo = (imagem.content_type or "").lower()
    if tipo not in TIPOS_IMAGEM:
        raise HTTPException(415, "Envie a prova como PNG, JPG ou WEBP")
    dados = await imagem.read()
    if len(dados) > MAX_IMAGEM:
        raise HTTPException(413, "Imagem maior que 10 MB")
    try:
        return transcrever_prova(dados, gab, media_type=tipo)
    except ValueError as e:
        raise HTTPException(502, f"Não foi possível ler a prova: {e}")
