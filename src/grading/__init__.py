"""Corretor de provas de Odontologia (objetivas + discursivas com rubrica)."""
from .grader import corrigir_aluno, corrigir_turma, ler_respostas_csv, resultados_csv, resumo_turma, transcrever_prova
from .models import Entrega, Gabarito, Questao, ResultadoAluno

__all__ = [
    "Entrega", "Gabarito", "Questao", "ResultadoAluno",
    "corrigir_aluno", "corrigir_turma", "ler_respostas_csv", "resultados_csv", "resumo_turma", "transcrever_prova",
]
