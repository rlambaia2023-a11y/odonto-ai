"""Provas em papel, lote e fluxo misto (papel + computador)."""
import io
import json
import threading
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from src.grading import cli
from src.grading.grader import corrigir_turma, ler_respostas_csv
from src.grading.models import Gabarito
from src.grading.papel import (
    ProvaPapel, dividir_pdf_turma, provas_da_pasta, transcrever_lote, transcrever_prova, transcricoes_csv,
)
from src.grading.router import router

EX = Path(__file__).resolve().parent.parent / "examples"
GAB = Gabarito.model_validate(json.loads((EX / "gabarito_exemplo.json").read_text(encoding="utf-8")))


def pdf(n_paginas: int) -> bytes:
    imgs = [Image.new("RGB", (200, 280), (255, 255, 255 - i)) for i in range(n_paginas)]
    buf = io.BytesIO()
    imgs[0].save(buf, format="PDF", save_all=True, append_images=imgs[1:])
    return buf.getvalue()


def png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (50, 50), "white").save(buf, format="PNG")
    return buf.getvalue()


def llm_transcricao(por_chamada=None, atraso=0.0):
    """Devolve uma transcrição por chamada; registra quantas páginas recebeu."""
    lock = threading.Lock()
    estado = {"n": 0, "paginas": []}

    def fn(system, user, imagens=None):
        if atraso:
            time.sleep(atraso)
        with lock:
            estado["n"] += 1
            estado["paginas"].append(len(imagens or []))
            i = estado["n"]
        if por_chamada:
            return json.dumps(por_chamada(i))
        if "transcreve" in system:
            return json.dumps({"aluno": f"Aluno {i}", "respostas": {"Q1": "B", "Q2": "C", "Q3": "A", "Q4": "biofilme...", "Q5": "tópico"}})
        return json.dumps({"criterios": [{"pontos_obtidos": 1}] * 3, "nota": 2, "feedback": "ok", "confianca": 0.9})

    fn.estado = estado
    return fn


def test_dividir_pdf_turma():
    provas = dividir_pdf_turma(pdf(6), 2, "turma.pdf")
    assert [p.origem for p in provas] == ["turma.pdf p.1-2", "turma.pdf p.3-4", "turma.pdf p.5-6"]
    assert all(len(p.paginas) == 2 and p.paginas[0][1] == "image/jpeg" for p in provas)


def test_dividir_pdf_paginas_sobrando():
    with pytest.raises(ValueError, match="não dividem"):
        dividir_pdf_turma(pdf(5), 2)


def test_provas_da_pasta(tmp_path):
    (tmp_path / "ana.pdf").write_bytes(pdf(3))
    (tmp_path / "bruno.png").write_bytes(png())
    carla = tmp_path / "carla"
    carla.mkdir()
    (carla / "02.png").write_bytes(png())
    (carla / "01.png").write_bytes(png())
    (tmp_path / "leia-me.txt").write_text("ignorar")
    provas = provas_da_pasta(tmp_path)
    assert [(p.origem, len(p.paginas)) for p in provas] == [("ana.pdf", 3), ("bruno.png", 1), ("carla", 2)]


def test_transcricao_marca_o_que_conferir():
    llm = llm_transcricao(lambda i: {"aluno": "", "respostas": {"Q1": "AMBÍGUA", "Q4": "o biofilme [ilegível] gengiva"}})
    t = transcrever_prova([(png(), "image/png")], GAB, llm=llm, origem="p7.jpg")
    assert t.entrega.aluno == "Sem nome (p7.jpg)"
    assert t.conferir == ["nome não encontrado", "Q1 marcação ambígua", "Q4 trecho ilegível"]


def test_transcricao_envia_todas_as_paginas_e_matricula():
    llm = llm_transcricao(lambda i: {"aluno": "Ana", "matricula": "2026001", "respostas": {}})
    t = transcrever_prova([(png(), "image/png")] * 3, GAB, llm=llm)
    assert llm.estado["paginas"] == [3] and t.entrega.aluno == "Ana - 2026001"


def test_lote_continua_quando_uma_prova_falha():
    def llm(system, user, imagens=None):
        if len(imagens) == 2:
            raise TimeoutError("lento")
        return json.dumps({"aluno": "Ok", "respostas": {"Q1": "b"}})

    lote = [ProvaPapel("a.pdf", [(png(), "image/png")]), ProvaPapel("b.pdf", [(png(), "image/png")] * 2)]
    itens = transcrever_lote(lote, GAB, llm=llm)
    assert itens[0].entrega.aluno == "Ok" and not itens[0].conferir
    assert itens[1].entrega.aluno == "Sem nome (b.pdf)" and "falha na leitura" in itens[1].conferir[0]


def test_fluxo_misto_papel_mais_computador():
    llm = llm_transcricao()
    itens = transcrever_lote([ProvaPapel(f"p{i}.pdf", [(png(), "image/png")]) for i in range(3)], GAB, llm=llm)
    papel = ler_respostas_csv(transcricoes_csv(GAB, itens).encode("utf-8"))
    online = ler_respostas_csv((EX / "respostas_exemplo.csv").read_bytes())
    res = corrigir_turma(GAB, papel + online, llm=llm_transcricao())
    assert len(res) == 6 and res[0].aluno == "Aluno 1" and res[0].nota == 8


def test_csv_salvo_pelo_excel_em_ansi():
    dados = "aluno;Q1\nJoão Conceição;b\n".encode("cp1252")
    assert ler_respostas_csv(dados)[0].aluno == "João Conceição"


def test_progresso():
    vistos = []
    entregas = ler_respostas_csv((EX / "respostas_exemplo.csv").read_bytes())
    corrigir_turma(GAB, entregas, llm=llm_transcricao(), progresso=lambda f, t: vistos.append((f, t)))
    assert vistos[-1] == (6, 6)  # 3 alunos x 2 discursivas (em branco contam, mas não chamam a IA)


def test_turma_de_250_em_paralelo(monkeypatch):
    """250 alunos x 2 discursivas, IA simulada com 50 ms por resposta: em série levaria 25 s."""
    monkeypatch.setattr("src.grading.grader.MAX_PARALELO", 16)
    linhas = ["aluno;Q1;Q2;Q3;Q4;Q5"] + [f"Aluno {i};b;c;a;resposta {i};resposta {i}" for i in range(250)]
    entregas = ler_respostas_csv("\n".join(linhas))
    t0 = time.perf_counter()
    res = corrigir_turma(GAB, entregas, llm=llm_transcricao(atraso=0.05))
    dt = time.perf_counter() - t0
    assert len(res) == 250 and all(r.nota == 8 for r in res)
    assert dt < 5, f"lento demais: {dt:.1f}s"


def test_cli_transcrever_e_corrigir(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("src.grading.papel.chamar_llm", llm_transcricao())
    monkeypatch.setattr("src.grading.grader.chamar_llm", llm_transcricao())
    (tmp_path / "turma.pdf").write_bytes(pdf(4))
    papel = tmp_path / "papel.csv"
    notas = tmp_path / "notas.csv"
    g = str(EX / "gabarito_exemplo.json")
    assert cli.main(["transcrever", g, str(tmp_path / "turma.pdf"), "--paginas-por-aluno", "2", "-o", str(papel)]) == 0
    assert cli.main(["corrigir", g, str(papel), str(EX / "respostas_exemplo.csv"), "-o", str(notas)]) == 0
    texto = notas.read_text(encoding="utf-8-sig")
    assert texto.count("\n") == 6  # cabeçalho + 2 do papel + 3 online
    assert "5 alunos corrigidos" in capsys.readouterr().out


def test_cli_uso_antigo_e_erro_amigavel(tmp_path, capsys):
    assert cli.main([str(EX / "gabarito_exemplo.json"), str(tmp_path / "nao_existe.csv")]) == 2
    assert "Erro:" in capsys.readouterr().err


@pytest.fixture
def cliente(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_api_transcrever_lote(cliente, monkeypatch):
    monkeypatch.setattr("src.grading.papel.chamar_llm", llm_transcricao())
    r = cliente.post(
        "/grading/transcrever-lote",
        files=[("gabarito", ("g.json", (EX / "gabarito_exemplo.json").read_bytes())),
               ("provas", ("ana.pdf", pdf(2), "application/pdf")),
               ("provas", ("bruno.jpg", png(), "image/jpeg"))],
    )
    assert r.status_code == 200
    linhas = r.content.decode("utf-8-sig").splitlines()
    assert linhas[0] == "aluno;Q1;Q2;Q3;Q4;Q5;arquivo;conferir" and len(linhas) == 3


def test_api_pdf_turma_paginas_erradas(cliente):
    r = cliente.post(
        "/grading/transcrever-pdf-turma",
        files={"gabarito": ("g.json", (EX / "gabarito_exemplo.json").read_bytes()), "pdf": ("t.pdf", pdf(3), "application/pdf")},
        data={"paginas_por_aluno": "2"},
    )
    assert r.status_code == 422 and "não dividem" in r.text


def test_api_corrigir_varias_planilhas(cliente, monkeypatch):
    monkeypatch.setattr("src.grading.grader.chamar_llm", llm_transcricao())
    csv = (EX / "respostas_exemplo.csv").read_bytes()
    r = cliente.post(
        "/grading/corrigir-planilha",
        files=[("gabarito", ("g.json", (EX / "gabarito_exemplo.json").read_bytes())),
               ("respostas", ("a.csv", csv, "text/csv")), ("respostas", ("b.csv", csv, "text/csv"))],
    )
    assert r.status_code == 200 and r.content.decode("utf-8-sig").count("Ana Souza") == 2
