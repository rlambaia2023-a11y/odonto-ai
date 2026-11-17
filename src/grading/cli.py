"""Uso: python -m src.grading.cli gabarito.json respostas.csv [-o notas.csv] [--literatura]"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .grader import corrigir_turma, ler_respostas_csv, resultados_csv, resumo_turma
from .models import Gabarito


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Corrige as provas de uma turma.")
    p.add_argument("gabarito", type=Path, help="JSON com questões, gabarito e rubricas")
    p.add_argument("respostas", type=Path, help="CSV: coluna 'aluno' + uma coluna por questão")
    p.add_argument("-o", "--saida", type=Path, default=Path("notas.csv"))
    p.add_argument("--literatura", action="store_true", help="Consultar a base OdontoAI nas discursivas")
    a = p.parse_args(argv)

    try:
        gab = Gabarito.model_validate(json.loads(a.gabarito.read_text(encoding="utf-8")))
        entregas = ler_respostas_csv(a.respostas.read_bytes())
    except (OSError, ValueError) as e:
        print(f"Erro: {e}", file=sys.stderr)
        return 2

    t0 = time.perf_counter()
    res = corrigir_turma(gab, entregas, usar_literatura=a.literatura)
    a.saida.write_text(resultados_csv(gab, res), encoding="utf-8", newline="")
    r = resumo_turma(res)
    print(f"{r['alunos']} alunos corrigidos em {time.perf_counter() - t0:.1f}s -> {a.saida}")
    print(f"Média {r['media']} | mediana {r['mediana']} | maior {r['maior']} | menor {r['menor']} (de {gab.valor_total:g})")
    if r["a_revisar"]:
        print(f"{r['a_revisar']} aluno(s) com questão marcada para sua revisão (coluna 'revisar').")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
