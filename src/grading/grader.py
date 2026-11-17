"""Orquestra a correção de uma turma: objetivas na hora, discursivas em paralelo."""
from __future__ import annotations

import csv
import io
import os
from concurrent.futures import ThreadPoolExecutor

from .discursive import corrigir_discursiva
from .llm import LLMFn, chamar_llm, extrair_json
from .models import Entrega, Gabarito, ResultadoAluno, ResultadoQuestao
from .objective import corrigir_objetiva

# Chamadas simultâneas à IA. Mais = mais rápido, até o limite de taxa da sua conta.
MAX_PARALELO = int(os.getenv("GRADING_MAX_WORKERS", "8"))


def corrigir_aluno(gab: Gabarito, entrega: Entrega, llm: LLMFn | None = None, usar_literatura: bool = False) -> ResultadoAluno:
    return corrigir_turma(gab, [entrega], llm=llm, usar_literatura=usar_literatura)[0]


def corrigir_turma(
    gab: Gabarito, entregas: list[Entrega], llm: LLMFn | None = None, usar_literatura: bool = False
) -> list[ResultadoAluno]:
    """Corrige todos os alunos. Cada questão discursiva de cada aluno é uma tarefa paralela."""
    llm = llm or chamar_llm
    tarefas: list[tuple[int, int]] = []  # (aluno, questão) discursivas
    resultados: list[list[ResultadoQuestao | None]] = []

    for ia, e in enumerate(entregas):
        linha: list[ResultadoQuestao | None] = []
        for iq, q in enumerate(gab.questoes):
            if q.tipo == "objetiva":
                linha.append(corrigir_objetiva(q, e.respostas.get(q.id)))
            else:
                linha.append(None)
                tarefas.append((ia, iq))
        resultados.append(linha)

    def _rodar(t: tuple[int, int]) -> tuple[int, int, ResultadoQuestao]:
        ia, iq = t
        q = gab.questoes[iq]
        return ia, iq, corrigir_discursiva(gab, q, entregas[ia].respostas.get(q.id), llm, usar_literatura)

    if tarefas:
        with ThreadPoolExecutor(max_workers=max(1, min(MAX_PARALELO, len(tarefas)))) as pool:
            for ia, iq, r in pool.map(_rodar, tarefas):
                resultados[ia][iq] = r

    saida = []
    for e, linha in zip(entregas, resultados):
        qs = [r for r in linha if r is not None]
        saida.append(ResultadoAluno(
            aluno=e.aluno,
            nota=round(sum(r.nota for r in qs), 2),
            valor_total=gab.valor_total,
            questoes=qs,
        ))
    return saida


# ---------- Entrada / saída em planilha ----------

def ler_respostas_csv(conteudo: bytes | str) -> list[Entrega]:
    """CSV com coluna 'aluno' e uma coluna por questão (Q1, Q2...). Aceita ';' ou ','."""
    texto = conteudo.decode("utf-8-sig") if isinstance(conteudo, bytes) else conteudo
    if not texto.strip():
        raise ValueError("Planilha de respostas vazia")
    try:
        dialeto = csv.Sniffer().sniff(texto.splitlines()[0], delimiters=";,\t")
    except csv.Error:
        dialeto = csv.excel
    leitor = csv.DictReader(io.StringIO(texto), dialect=dialeto)
    campos = {(c or "").strip().lower(): c for c in (leitor.fieldnames or [])}
    col_aluno = campos.get("aluno") or campos.get("nome") or campos.get("estudante")
    if not col_aluno:
        raise ValueError("A planilha precisa de uma coluna 'aluno'")
    entregas = []
    for n, linha in enumerate(leitor, start=2):
        nome = (linha.get(col_aluno) or "").strip()
        if not nome:
            continue
        respostas = {(k or "").strip(): (v or "").strip() for k, v in linha.items() if k and k != col_aluno}
        entregas.append(Entrega(aluno=nome, respostas=respostas))
    if not entregas:
        raise ValueError("Nenhum aluno encontrado na planilha")
    return entregas


def resultados_csv(gab: Gabarito, resultados: list[ResultadoAluno]) -> str:
    """Planilha de notas pronta para o Excel (separador ';', vírgula decimal, UTF-8 com BOM)."""
    def br(x: float) -> str:
        return f"{x:.2f}".replace(".", ",")

    buf = io.StringIO()
    buf.write("﻿")
    w = csv.writer(buf, delimiter=";")
    ids = [q.id for q in gab.questoes]
    w.writerow(["aluno", *ids, "nota", "valor_total", "revisar", "pontos_a_revisar", "feedback"])
    for r in resultados:
        por_id = {q.id: q for q in r.questoes}
        pendentes = [f"{q.id}: {q.motivo_revisao}" for q in r.questoes if q.revisar]
        feedback = " | ".join(f"{q.id}: {q.feedback}" for q in r.questoes if q.tipo == "discursiva" and q.feedback)
        w.writerow([
            r.aluno, *(br(por_id[i].nota) for i in ids), br(r.nota), br(r.valor_total),
            "SIM" if r.revisar else "", " / ".join(pendentes), feedback,
        ])
    return buf.getvalue()


def resumo_turma(resultados: list[ResultadoAluno]) -> dict:
    if not resultados:
        return {"alunos": 0}
    notas = sorted(r.nota for r in resultados)
    n = len(notas)
    meio = n // 2
    return {
        "alunos": n,
        "media": round(sum(notas) / n, 2),
        "mediana": round(notas[meio] if n % 2 else (notas[meio - 1] + notas[meio]) / 2, 2),
        "maior": notas[-1],
        "menor": notas[0],
        "a_revisar": sum(1 for r in resultados if r.revisar),
    }


# ---------- Prova em papel (foto/digitalização) ----------

SYSTEM_TRANSCRICAO = (
    "Você transcreve provas manuscritas de alunos de Odontologia. Transcreva fielmente o que o aluno escreveu, "
    "sem corrigir, completar ou melhorar. Use [ilegível] onde não for possível ler. Em questões objetivas, "
    "informe só a letra marcada; se houver mais de uma marcada ou rasura, escreva 'AMBÍGUA'. "
    "Responda SOMENTE com JSON válido."
)


def transcrever_prova(imagem: bytes, gab: Gabarito, media_type: str = "image/png", llm: LLMFn | None = None) -> Entrega:
    """Lê a foto da prova e devolve as respostas do aluno para conferência antes de corrigir."""
    llm = llm or chamar_llm
    ids = ", ".join(f"{q.id} ({q.tipo})" for q in gab.questoes)
    pedido = (
        f"Questões desta prova: {ids}.\n"
        'Formato: {"aluno": "<nome escrito na prova ou vazio>", "respostas": {"<id>": "<texto ou letra>"}}'
    )
    dados = extrair_json(llm(SYSTEM_TRANSCRICAO, pedido, image=imagem, media_type=media_type))
    respostas = {str(k): str(v) for k, v in (dados.get("respostas") or {}).items()}
    return Entrega(aluno=str(dados.get("aluno") or "Sem nome").strip() or "Sem nome", respostas=respostas)
