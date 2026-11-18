"""Modelos de dados do corretor de provas."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class Criterio(BaseModel):
    """Um item da rubrica de uma questão discursiva."""

    descricao: str = Field(min_length=1)
    pontos: float = Field(gt=0)


class Questao(BaseModel):
    id: str = Field(min_length=1, description="Identificador, ex.: Q1")
    tipo: Literal["objetiva", "discursiva"]
    valor: float = Field(gt=0, description="Pontuação máxima da questão")
    enunciado: str = ""
    # Objetiva
    gabarito: str | None = Field(default=None, description="Letra correta (ou letras separadas por vírgula, se anulada/dupla)")
    # Discursiva
    resposta_esperada: str = ""
    rubrica: list[Criterio] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validar_tipo(self) -> "Questao":
        if self.tipo == "objetiva" and not (self.gabarito or "").strip():
            raise ValueError(f"Questão {self.id}: objetiva precisa de 'gabarito'")
        if self.tipo == "discursiva":
            if not self.resposta_esperada.strip() and not self.rubrica:
                raise ValueError(f"Questão {self.id}: discursiva precisa de 'resposta_esperada' ou 'rubrica'")
            if self.rubrica:
                soma = sum(c.pontos for c in self.rubrica)
                if abs(soma - self.valor) > 1e-6:
                    raise ValueError(
                        f"Questão {self.id}: soma da rubrica ({soma:g}) difere do valor da questão ({self.valor:g})"
                    )
        return self


class Gabarito(BaseModel):
    titulo: str = "Prova"
    disciplina: str = "Odontologia"
    instrucoes_correcao: str = Field(
        default="",
        description="Orientações gerais do professor (rigor, o que aceitar como sinônimo etc.)",
    )
    questoes: list[Questao] = Field(min_length=1)

    @field_validator("questoes")
    @classmethod
    def _ids_unicos(cls, v: list[Questao]) -> list[Questao]:
        ids = [q.id for q in v]
        dup = {i for i in ids if ids.count(i) > 1}
        if dup:
            raise ValueError(f"IDs de questão repetidos: {sorted(dup)}")
        return v

    @property
    def valor_total(self) -> float:
        return sum(q.valor for q in self.questoes)


class Entrega(BaseModel):
    """Respostas de um aluno."""

    aluno: str = Field(min_length=1)
    respostas: dict[str, str] = Field(default_factory=dict)


class ResultadoCriterio(BaseModel):
    descricao: str
    pontos_obtidos: float
    pontos_max: float
    comentario: str = ""


class ResultadoQuestao(BaseModel):
    id: str
    tipo: str
    nota: float
    valor: float
    feedback: str = ""
    criterios: list[ResultadoCriterio] = Field(default_factory=list)
    revisar: bool = False
    motivo_revisao: str = ""
    confianca: float = 1.0


class ResultadoAluno(BaseModel):
    aluno: str
    nota: float
    valor_total: float
    questoes: list[ResultadoQuestao]
    comentario_geral: str = ""
    # Problemas na leitura da prova em papel (nome não lido, marcação ambígua, trecho ilegível...)
    pendencias_leitura: list[str] = Field(default_factory=list)
    origem: str = ""  # arquivo/páginas da prova física

    @property
    def revisar(self) -> bool:
        return bool(self.pendencias_leitura) or any(q.revisar for q in self.questoes)
