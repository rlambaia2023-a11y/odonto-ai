"""Corretor de provas pela linha de comando.

  1) Provas em papel -> planilha de respostas (confira a coluna 'conferir'):
     python -m src.grading.cli transcrever gabarito.json PASTA_DAS_PROVAS -o respostas_papel.csv
     python -m src.grading.cli transcrever gabarito.json turma.pdf --paginas-por-aluno 4 -o respostas_papel.csv

  2) Corrigir (papel e computador juntos, quantas planilhas quiser):
     python -m src.grading.cli corrigir gabarito.json respostas_papel.csv respostas_online.csv -o notas.csv
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

from .grader import corrigir_turma, ler_respostas_csv, resultados_csv, resumo_turma
from .models import Entrega, Gabarito
from .papel import dividir_pdf_turma, provas_da_pasta, transcrever_lote, transcricoes_csv


def _progresso(rotulo: str):
    passo = {"ultimo": -1}

    def fn(feitas: int, total: int) -> None:
        pct = feitas * 100 // total
        if pct // 10 != passo["ultimo"] or feitas == total:  # imprime a cada 10%
            passo["ultimo"] = pct // 10
            print(f"  {rotulo}: {feitas}/{total} ({pct}%)", file=sys.stderr, flush=True)

    return fn


def _gabarito(caminho: Path) -> Gabarito:
    return Gabarito.model_validate(json.loads(caminho.read_text(encoding="utf-8-sig")))


def cmd_transcrever(a: argparse.Namespace) -> int:
    gab = _gabarito(a.gabarito)
    if a.origem.is_file() and a.origem.suffix.lower() == ".pdf" and a.paginas_por_aluno:
        provas = dividir_pdf_turma(a.origem.read_bytes(), a.paginas_por_aluno, a.origem.name)
    elif a.origem.is_dir():
        provas = provas_da_pasta(a.origem)
    else:
        raise ValueError("Informe uma pasta de provas, ou um PDF da turma com --paginas-por-aluno")

    print(f"{len(provas)} provas encontradas. Lendo...", file=sys.stderr)
    t0 = time.perf_counter()
    itens = transcrever_lote(provas, gab, progresso=_progresso("lidas"))
    a.saida.write_text(transcricoes_csv(gab, itens), encoding="utf-8", newline="")
    pendentes = sum(1 for t in itens if t.conferir)
    print(f"{len(itens)} provas transcritas em {time.perf_counter() - t0:.0f}s -> {a.saida}")
    if pendentes:
        print(f"Confira {pendentes} linha(s) com a coluna 'conferir' preenchida antes de corrigir.")
    return 0


def cmd_corrigir(a: argparse.Namespace) -> int:
    gab = _gabarito(a.gabarito)
    entregas: list[Entrega] = []
    for arq in a.respostas:
        entregas += ler_respostas_csv(arq.read_bytes())

    repetidos = [n for n, c in Counter(e.aluno.strip().lower() for e in entregas).items() if c > 1]
    if repetidos:
        print(f"Atenção: {len(repetidos)} nome(s) aparecem mais de uma vez (ex.: {repetidos[0]}). "
              "Todos serão corrigidos; confira se não é a mesma prova em duas planilhas.", file=sys.stderr)
    nao_conferidas = sum(1 for e in entregas if e.respostas.get("conferir", "").strip())
    if nao_conferidas:
        print(f"Atenção: {nao_conferidas} prova(s) em papel ainda com 'conferir' preenchido "
              "(apague o texto da coluna depois de conferir).", file=sys.stderr)

    discursivas = sum(1 for q in gab.questoes if q.tipo == "discursiva") * len(entregas)
    print(f"{len(entregas)} alunos, {discursivas} respostas discursivas. Corrigindo...", file=sys.stderr)
    t0 = time.perf_counter()
    res = corrigir_turma(gab, entregas, usar_literatura=a.literatura, progresso=_progresso("discursivas"))
    a.saida.write_text(resultados_csv(gab, res), encoding="utf-8", newline="")
    r = resumo_turma(res)
    print(f"{r['alunos']} alunos corrigidos em {time.perf_counter() - t0:.0f}s -> {a.saida}")
    print(f"Média {r['media']} | mediana {r['mediana']} | maior {r['maior']} | menor {r['menor']} (de {gab.valor_total:g})")
    if r["a_revisar"]:
        print(f"{r['a_revisar']} aluno(s) com questão marcada para sua revisão (coluna 'revisar').")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in {"corrigir", "transcrever", "-h", "--help"}:
        argv.insert(0, "corrigir")  # compatível com o uso antigo: cli gabarito.json respostas.csv

    p = argparse.ArgumentParser(prog="python -m src.grading.cli", description="Corretor de provas OdontoAI")
    sub = p.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("transcrever", help="Ler provas em papel (fotos/PDF) e gerar a planilha de respostas")
    t.add_argument("gabarito", type=Path)
    t.add_argument("origem", type=Path, help="Pasta (1 PDF/foto/subpasta por aluno) ou PDF único da turma")
    t.add_argument("--paginas-por-aluno", type=int, default=0, help="Para PDF único: páginas de cada prova")
    t.add_argument("-o", "--saida", type=Path, default=Path("respostas_papel.csv"))
    t.set_defaults(fn=cmd_transcrever)

    c = sub.add_parser("corrigir", help="Corrigir uma ou mais planilhas de respostas")
    c.add_argument("gabarito", type=Path)
    c.add_argument("respostas", type=Path, nargs="+", help="Planilhas CSV (papel e/ou computador)")
    c.add_argument("-o", "--saida", type=Path, default=Path("notas.csv"))
    c.add_argument("--literatura", action="store_true", help="Consultar a base OdontoAI nas discursivas")
    c.set_defaults(fn=cmd_corrigir)

    a = p.parse_args(argv)
    try:
        return a.fn(a)
    except (OSError, ValueError) as e:
        print(f"Erro: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
