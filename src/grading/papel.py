"""Provas em papel: leitura de fotos/PDFs digitalizados e transcrição em lote.

Fluxo para turmas grandes:
  1. Digitalize as provas (scanner ou celular) numa pasta.
  2. transcrever_lote -> planilha de respostas com a coluna 'conferir'.
  3. O professor confere só as linhas marcadas e corrige com corrigir_turma (mesma planilha das provas online).
"""
from __future__ import annotations

import csv
import io
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..utils import logger
from .grader import MAX_PARALELO, corrigir_turma
from .llm import Imagem, LLMFn, chamar_llm, extrair_json
from .models import Entrega, Gabarito, ResultadoAluno

EXT_IMAGEM = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
MAX_PAGINAS = 20
ESCALA_PDF = 2.0  # ~144 dpi: suficiente para letra manuscrita sem estourar o tamanho

SYSTEM_TRANSCRICAO = (
    "Você transcreve provas manuscritas de alunos de Odontologia. Transcreva fielmente o que o aluno escreveu, "
    "sem corrigir, completar ou melhorar. Use [ilegível] onde não for possível ler. Em questões objetivas, "
    "informe só a letra marcada; se houver mais de uma marcada ou rasura, escreva 'AMBÍGUA'. "
    "As páginas vêm em ordem. Responda SOMENTE com JSON válido."
)


@dataclass
class ProvaPapel:
    origem: str  # nome do arquivo/pasta, para o professor achar a prova física
    paginas: list[Imagem] = field(default_factory=list)


@dataclass
class Transcricao:
    origem: str
    entrega: Entrega
    conferir: list[str] = field(default_factory=list)


# ---------- Leitura de arquivos ----------

def pdf_para_imagens(dados: bytes) -> list[Imagem]:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(dados)
    try:
        if len(pdf) > MAX_PAGINAS * 500:
            raise ValueError(f"PDF com {len(pdf)} páginas: grande demais")
        saida = []
        for i in range(len(pdf)):
            img = pdf[i].render(scale=ESCALA_PDF).to_pil().convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            saida.append((buf.getvalue(), "image/jpeg"))
        return saida
    finally:
        pdf.close()


def carregar_paginas(caminho: Path) -> list[Imagem]:
    """Uma foto, um PDF ou uma pasta de fotos (ordenadas pelo nome) -> páginas."""
    if caminho.is_dir():
        fotos = sorted(p for p in caminho.iterdir() if p.suffix.lower() in EXT_IMAGEM)
        return [(p.read_bytes(), EXT_IMAGEM[p.suffix.lower()]) for p in fotos]
    ext = caminho.suffix.lower()
    if ext == ".pdf":
        return pdf_para_imagens(caminho.read_bytes())
    if ext in EXT_IMAGEM:
        return [(caminho.read_bytes(), EXT_IMAGEM[ext])]
    raise ValueError(f"Formato não suportado: {caminho.name}")


def provas_da_pasta(pasta: Path) -> list[ProvaPapel]:
    """Cada PDF, foto solta ou subpasta de fotos = a prova de um aluno."""
    if not pasta.is_dir():
        raise ValueError(f"Pasta não encontrada: {pasta}")
    provas = []
    for item in sorted(pasta.iterdir()):
        if item.name.startswith("."):
            continue
        if item.is_dir() or item.suffix.lower() in EXT_IMAGEM or item.suffix.lower() == ".pdf":
            paginas = carregar_paginas(item)
            if paginas:
                provas.append(ProvaPapel(origem=item.name, paginas=paginas))
    if not provas:
        raise ValueError("Nenhuma prova (PDF, JPG, PNG, WEBP) encontrada na pasta")
    return provas


def dividir_pdf_turma(dados: bytes, paginas_por_aluno: int, nome: str = "turma.pdf") -> list[ProvaPapel]:
    """PDF único com a turma inteira digitalizada em sequência -> uma prova a cada N páginas."""
    if paginas_por_aluno < 1:
        raise ValueError("paginas_por_aluno deve ser >= 1")
    paginas = pdf_para_imagens(dados)
    if len(paginas) % paginas_por_aluno:
        raise ValueError(
            f"O PDF tem {len(paginas)} páginas, que não dividem em blocos de {paginas_por_aluno}. "
            "Confira se falta ou sobra alguma folha."
        )
    return [
        ProvaPapel(origem=f"{nome} p.{i + 1}-{i + paginas_por_aluno}", paginas=paginas[i:i + paginas_por_aluno])
        for i in range(0, len(paginas), paginas_por_aluno)
    ]


# ---------- Transcrição ----------

def transcrever_prova(paginas: list[Imagem], gab: Gabarito, llm: LLMFn | None = None, origem: str = "") -> Transcricao:
    llm = llm or chamar_llm
    if not paginas:
        raise ValueError("Prova sem páginas")
    if len(paginas) > MAX_PAGINAS:
        raise ValueError(f"Prova com {len(paginas)} páginas (máximo {MAX_PAGINAS})")
    ids = ", ".join(f"{q.id} ({q.tipo})" for q in gab.questoes)
    pedido = (
        f"Questões desta prova: {ids}.\n"
        'Formato: {"aluno": "<nome escrito na prova ou vazio>", "matricula": "<se houver, ou vazio>", '
        '"respostas": {"<id>": "<texto ou letra; vazio se em branco>"}}'
    )
    dados = extrair_json(llm(SYSTEM_TRANSCRICAO, pedido, imagens=paginas))
    respostas = {str(k).strip(): str(v or "").strip() for k, v in (dados.get("respostas") or {}).items()}

    nome = str(dados.get("aluno") or "").strip()
    matricula = str(dados.get("matricula") or "").strip()
    conferir = []
    if not nome:
        conferir.append("nome não encontrado")
    for q in gab.questoes:
        r = respostas.get(q.id, "")
        if "AMBÍGUA" in r.upper() or "AMBIGUA" in r.upper():
            conferir.append(f"{q.id} marcação ambígua")
        elif "[ilegível]" in r.lower() or "[ilegivel]" in r.lower():
            conferir.append(f"{q.id} trecho ilegível")
    rotulo = nome or f"Sem nome ({origem or 'prova'})"
    if matricula:
        rotulo = f"{rotulo} - {matricula}"
    return Transcricao(origem=origem, entrega=Entrega(aluno=rotulo, respostas=respostas), conferir=conferir)


def transcrever_lote(
    provas: list[ProvaPapel],
    gab: Gabarito,
    llm: LLMFn | None = None,
    progresso: Callable[[int, int], None] | None = None,
) -> list[Transcricao]:
    """Transcreve todas as provas em paralelo. Uma prova com erro não para o lote."""
    llm = llm or chamar_llm

    def _uma(p: ProvaPapel) -> Transcricao:
        try:
            return transcrever_prova(p.paginas, gab, llm=llm, origem=p.origem)
        except Exception as e:
            logger.error("Falha ao transcrever %s: %s", p.origem, e)
            return Transcricao(
                origem=p.origem,
                entrega=Entrega(aluno=f"Sem nome ({p.origem})"),
                conferir=[f"falha na leitura: {type(e).__name__}"],
            )

    if not provas:
        return []
    saida: list[Transcricao] = []
    with ThreadPoolExecutor(max_workers=max(1, min(MAX_PARALELO, len(provas)))) as pool:
        for n, t in enumerate(pool.map(_uma, provas), start=1):
            saida.append(t)
            if progresso:
                progresso(n, len(provas))
    return saida


def transcricoes_csv(gab: Gabarito, itens: list[Transcricao]) -> str:
    """Planilha de respostas no mesmo formato das provas online, + colunas para conferência."""
    buf = io.StringIO()
    buf.write("﻿")
    w = csv.writer(buf, delimiter=";")
    ids = [q.id for q in gab.questoes]
    w.writerow(["aluno", *ids, "arquivo", "conferir"])
    for t in itens:
        w.writerow([t.entrega.aluno, *(t.entrega.respostas.get(i, "") for i in ids), t.origem, " / ".join(t.conferir)])
    return buf.getvalue()


def corrigir_provas_papel(
    provas: list[ProvaPapel],
    gab: Gabarito,
    llm: LLMFn | None = None,
    progresso_leitura: Callable[[int, int], None] | None = None,
    progresso_correcao: Callable[[int, int], None] | None = None,
) -> tuple[list[Transcricao], list[ResultadoAluno]]:
    """Fluxo completo da prova em papel: lê cada prova e já corrige.

    Problemas de leitura não bloqueiam a nota: entram em `pendencias_leitura` e marcam o aluno para revisão.
    """
    itens = transcrever_lote(provas, gab, llm=llm, progresso=progresso_leitura)
    resultados = corrigir_turma(gab, [t.entrega for t in itens], llm=llm, progresso=progresso_correcao)
    for t, r in zip(itens, resultados):
        r.pendencias_leitura = list(t.conferir)
        r.origem = t.origem
    return itens, resultados
