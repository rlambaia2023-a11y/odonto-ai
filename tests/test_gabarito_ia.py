"""Montagem do gabarito a partir da prova do professor."""
import io
import json
import zipfile

import pytest

from src.grading.gabarito_ia import (
    gabarito_para_linhas, ler_documento, linhas_para_gabarito, montar_gabarito, texto_docx, texto_para_rubrica,
)


def llm_com(resposta: dict):
    chamadas = []

    def fn(system, user, imagens=None):
        chamadas.append((user, imagens))
        return "```json\n" + json.dumps(resposta) + "\n```"

    fn.chamadas = chamadas
    return fn


RESPOSTA = {
    "titulo": "P1 Periodontia",
    "disciplina": "Periodontia",
    "questoes": [
        {"id": "Q1", "tipo": "objetiva", "valor": 1, "enunciado": "Microrganismo", "gabarito": "Letra B", "rascunho_ia": False},
        {"id": "Q2", "tipo": "objetiva", "valor": "1,5", "enunciado": "Anulada", "gabarito": "A e D"},
        {"id": "Q3", "tipo": "discursiva", "valor": 3, "enunciado": "Gengivite x periodontite",
         "resposta_esperada": "Biofilme...", "rascunho_ia": True,
         # soma 4, mas a questão vale 3: deve ser reescalado
         "rubrica": [{"descricao": "Biofilme", "pontos": 1}, {"descricao": "Sinais", "pontos": 1}, {"descricao": "Reversível", "pontos": 2}]},
        {"id": "Q4", "tipo": "objetiva", "valor": 1, "enunciado": "Sem letra", "gabarito": ""},
    ],
}


def test_monta_e_corrige_inconsistencias():
    llm = llm_com(RESPOSTA)
    gab, rasc = montar_gabarito(texto="1) ... 2) ...", nota_total=10, llm=llm)
    q = {x.id: x for x in gab.questoes}
    assert q["Q1"].gabarito == "B"
    assert q["Q2"].gabarito == "A,D" and q["Q2"].valor == 1.5
    assert sum(c.pontos for c in q["Q3"].rubrica) == pytest.approx(3)
    assert q["Q4"].tipo == "discursiva" and "completar" in q["Q4"].resposta_esperada  # sem alternativa: professor completa
    assert set(rasc) == {"Q3", "Q4"}
    assert gab.titulo == "P1 Periodontia"
    assert "<<<\n1) ... 2) ...\n>>>" in llm.chamadas[0][0]


def test_prova_em_imagens_vai_para_a_ia():
    llm = llm_com(RESPOSTA)
    montar_gabarito(paginas=[(b"x", "image/png")] * 2, llm=llm)
    assert len(llm.chamadas[0][1]) == 2


def test_sem_documento_e_sem_questoes():
    with pytest.raises(ValueError):
        montar_gabarito(llm=llm_com(RESPOSTA))
    with pytest.raises(ValueError, match="Nenhuma questão"):
        montar_gabarito(texto="x", llm=llm_com({"questoes": []}))


def test_docx_e_formatos():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", "<w:document><w:p><w:r><w:t>1) C&amp;D</w:t></w:r></w:p><w:p><w:t>Resposta: B</w:t></w:p></w:document>")
    assert texto_docx(buf.getvalue()) == "1) C&D\nResposta: B"
    assert ler_documento("prova.docx", buf.getvalue())[1].startswith("1) C&D")
    assert ler_documento("prova.txt", "Questão 1".encode("cp1252"))[1] == "Questão 1"
    with pytest.raises(ValueError):
        ler_documento("prova.xls", b"x")
    with pytest.raises(ValueError):
        texto_docx(b"nao e zip")


def test_ida_e_volta_preserva_ponto_e_virgula():
    from src.grading.gabarito_ia import rubrica_para_texto
    from src.grading.models import Criterio
    r = [Criterio(descricao="a; b", pontos=1), Criterio(descricao="c", pontos=2)]
    assert [(c.descricao, c.pontos) for c in texto_para_rubrica(rubrica_para_texto(r))] == [("a; b", 1), ("c", 2)]


def test_tabela_ida_e_volta_com_edicao():
    gab, rasc = montar_gabarito(texto="x", llm=llm_com(RESPOSTA))
    linhas = gabarito_para_linhas(gab, rasc)
    assert linhas[2]["conferir"] == "resposta escrita pela IA"
    linhas[3].update(tipo="objetiva", gabarito="c")  # professor completa a Q4
    novo = linhas_para_gabarito(linhas, "P1", "Perio", "não descontar português")
    assert {q.id: q for q in novo.questoes}["Q4"].gabarito == "C"
    assert novo.instrucoes_correcao == "não descontar português"


def test_rubrica_texto():
    assert [c.pontos for c in texto_para_rubrica("A (1 pt) ; B (1,5 pts)")] == [1, 1.5]
    # descrição com ';' (a IA escreve assim às vezes) não pode quebrar o critério
    crits = texto_para_rubrica("Sinais: gengivite sem perda; periodontite com perda (1 pt) ; Reversível (2 pt)")
    assert [c.descricao for c in crits] == ["Sinais: gengivite sem perda; periodontite com perda", "Reversível"]
    with pytest.raises(ValueError, match="sem pontuação"):
        texto_para_rubrica("critério sem pontos")


def test_edicao_invalida_aponta_a_linha():
    gab, rasc = montar_gabarito(texto="x", llm=llm_com(RESPOSTA))
    linhas = gabarito_para_linhas(gab, rasc)
    linhas[2]["rubrica"] = "Biofilme (1 pt)"  # soma 1, questão vale 3
    with pytest.raises(ValueError, match="Linha 3 \\(Q3\\)"):
        linhas_para_gabarito(linhas, "P1", "Perio")


def test_resolucao_pegadinha_incorreta_em_negrito():
    from src.grading.gabarito_ia import montar_gabarito_completo, resolucao_para_markdown

    resp = {"titulo": "P2", "questoes": [{
        "id": "Q1", "tipo": "objetiva", "valor": 1, "enunciado": "Sobre a gengivite, assinale a INCORRETA",
        "comando": "Incorreta", "pegadinha": "pede a INCORRETA",
        "analise": [{"letra": "a", "julgamento": "v", "motivo": "biofilme"}, {"letra": "B", "julgamento": "F", "motivo": "é reversível"}],
        "gabarito": "B", "alternativa_texto": "É irreversível", "justificativa": "Gengivite é reversível",
        "fontes": ["https://pubmed.ncbi.nlm.nih.gov/1", "https://pubmed.ncbi.nlm.nih.gov/1"], "confianca": 0.9,
    }, {"id": "Q2", "tipo": "objetiva", "valor": 1, "enunciado": "Dúvida", "gabarito": "C", "confianca": 0.3}]}
    llm = llm_com(resp)
    gab, rasc, resol = montar_gabarito_completo(texto="x", llm=llm, pesquisar_web=True)
    assert "INCORRETA/FALSA" in llm.chamadas[0][0] and "PESQUISA NA INTERNET" in llm.chamadas[0][0]
    r = resol[0]
    assert r["comando"] == "incorreta" and r["analise"][0] == {"letra": "A", "julgamento": "V", "motivo": "biofilme"}
    assert len(r["fontes"]) == 1
    assert rasc == ["Q2"]  # confiança baixa vai para conferência
    md = resolucao_para_markdown(resol, gab.titulo)
    assert "**Resposta: B) É irreversível**" in md and "**INCORRETA**" in md and "(conferir)" in md


def test_sem_pesquisa_web_nao_pede_busca():
    llm = llm_com(RESPOSTA)
    montar_gabarito(texto="x", llm=llm)
    assert "PESQUISA NA INTERNET" not in llm.chamadas[0][0]


def test_ocr_paginas_e_json_com_texto_antes():
    from src.grading.gabarito_ia import ocr_paginas
    from src.grading.llm import extrair_json

    vistos = []
    assert ocr_paginas([(b"x", "image/png")], llm=lambda s, u, imagens=None: vistos.append(imagens) or " 1) Texto ") == "1) Texto"
    assert len(vistos[0]) == 1 and ocr_paginas([]) == ""
    assert extrair_json('Pesquisei {fonte} e conclui:\n{"questoes": [{"id": "Q1"}]}\nfim') == {"questoes": [{"id": "Q1"}]}
