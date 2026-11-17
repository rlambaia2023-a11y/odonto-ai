"""Endpoints HTTP do corretor (montados em /grading pelo api.py)."""
from __future__ import annotations

import json
import os
import secrets

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, ValidationError

from .grader import corrigir_turma, ler_respostas_csv, resultados_csv, resumo_turma
from .papel import ProvaPapel, dividir_pdf_turma, pdf_para_imagens, transcrever_lote, transcrever_prova, transcricoes_csv
from .models import Entrega, Gabarito, ResultadoAluno

MAX_ARQUIVO = 20 * 1024 * 1024
MAX_PDF_TURMA = 300 * 1024 * 1024
MAX_ARQUIVOS_LOTE = 400
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
    respostas: list[UploadFile] = File(..., description="Uma ou mais planilhas .csv (papel e computador)"),
    usar_literatura: bool = Form(False),
) -> Response:
    gab = _gabarito(await gabarito.read())
    try:
        entregas = [e for arq in respostas for e in ler_respostas_csv(await arq.read())]
    except (ValueError, UnicodeDecodeError) as e:
        raise HTTPException(422, f"Planilha de respostas inválida: {e}")
    res = await run_in_threadpool(corrigir_turma, gab, entregas, usar_literatura=usar_literatura)
    return _csv(resultados_csv(gab, res), "notas.csv")


async def _paginas(arq: UploadFile) -> list[tuple[bytes, str]]:
    """Uma foto ou um PDF enviado -> páginas da prova."""
    tipo = (arq.content_type or "").lower()
    nome = (arq.filename or "").lower()
    dados = await arq.read()
    if len(dados) > MAX_ARQUIVO:
        raise HTTPException(413, f"{arq.filename}: arquivo maior que 20 MB")
    if tipo == "application/pdf" or nome.endswith(".pdf"):
        try:
            return pdf_para_imagens(dados)
        except Exception as e:
            raise HTTPException(422, f"{arq.filename}: PDF inválido ({type(e).__name__})")
    if tipo in TIPOS_IMAGEM:
        return [(dados, tipo)]
    raise HTTPException(415, f"{arq.filename}: envie PDF, PNG, JPG ou WEBP")


@router.post("/transcrever", response_model=Entrega)
async def transcrever(gabarito: UploadFile = File(...), paginas: list[UploadFile] = File(...)) -> Entrega:
    """Uma prova (uma ou mais fotos/PDF, na ordem das páginas) -> respostas do aluno para conferência."""
    gab = _gabarito(await gabarito.read())
    todas: list[tuple[bytes, str]] = []
    for arq in paginas:
        todas += await _paginas(arq)
    try:
        return (await run_in_threadpool(transcrever_prova, todas, gab)).entrega
    except ValueError as e:
        raise HTTPException(422, f"Não foi possível ler a prova: {e}")


def _csv(texto: str, nome: str) -> Response:
    return Response(
        content=texto.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nome}"'},
    )


@router.post("/transcrever-lote")
async def transcrever_lote_rota(
    gabarito: UploadFile = File(...),
    provas: list[UploadFile] = File(..., description="Um arquivo (PDF ou foto) por aluno"),
) -> Response:
    """Várias provas em papel -> planilha de respostas com a coluna 'conferir'."""
    gab = _gabarito(await gabarito.read())
    if len(provas) > MAX_ARQUIVOS_LOTE:
        raise HTTPException(413, f"Máximo de {MAX_ARQUIVOS_LOTE} provas por envio")
    lote = [ProvaPapel(origem=a.filename or f"prova_{i + 1}", paginas=await _paginas(a)) for i, a in enumerate(provas)]
    itens = await run_in_threadpool(transcrever_lote, lote, gab)
    return _csv(transcricoes_csv(gab, itens), "respostas_papel.csv")


@router.post("/transcrever-pdf-turma")
async def transcrever_pdf_turma(
    gabarito: UploadFile = File(...),
    pdf: UploadFile = File(..., description="PDF único com a turma digitalizada em sequência"),
    paginas_por_aluno: int = Form(..., ge=1, le=20),
) -> Response:
    gab = _gabarito(await gabarito.read())
    dados = await pdf.read()
    if len(dados) > MAX_PDF_TURMA:
        raise HTTPException(413, "PDF maior que 300 MB: divida em partes")
    try:
        lote = await run_in_threadpool(dividir_pdf_turma, dados, paginas_por_aluno, pdf.filename or "turma.pdf")
    except ValueError as e:
        raise HTTPException(422, str(e))
    itens = await run_in_threadpool(transcrever_lote, lote, gab)
    return _csv(transcricoes_csv(gab, itens), "respostas_papel.csv")
