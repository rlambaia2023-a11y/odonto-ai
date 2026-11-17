"""Corretor de provas de Odontologia (objetivas + discursivas com rubrica)."""
from .grader import corrigir_aluno, corrigir_turma, ler_respostas_csv, resultados_csv, resumo_turma
from .papel import dividir_pdf_turma, provas_da_pasta, transcrever_lote, transcricoes_csv
from .models import Entrega, Gabarito, Questao, ResultadoAluno

__all__ = [
    "Entrega", "Gabarito", "Questao", "ResultadoAluno",
    "corrigir_aluno", "corrigir_turma", "ler_respostas_csv", "resultados_csv", "resumo_turma",
    "provas_da_pasta", "dividir_pdf_turma", "transcrever_lote", "transcricoes_csv",
]
