from datetime import datetime

import pytest

from src.grading import historico


@pytest.fixture(autouse=True)
def pasta_temp(tmp_path, monkeypatch):
    monkeypatch.setenv("HISTORICO_DIR", str(tmp_path))


def test_salvar_listar_excluir():
    a = historico.salvar("provas", "P1", {"notas": "aluno;nota\n", "resumo": {"alunos": 1}},
                         agora=datetime(2025, 3, 9, 17, 5, tzinfo=historico.BRASILIA))
    b = historico.salvar("gabarito", "P2", {"questoes": 3})
    regs = historico.listar()
    assert [r["id"] for r in regs] == [b, a]  # mais novo primeiro
    assert historico.data_hora(regs[1]) == "09/03/2025 às 17:05"
    assert historico.excluir(a) is True
    assert historico.excluir(a) is False
    assert [r["id"] for r in historico.listar()] == [b]
    assert historico.excluir_tudo() == 1
    assert historico.listar() == []


def test_id_invalido_nao_apaga_fora_da_pasta():
    with pytest.raises(ValueError):
        historico.excluir("../../etc/passwd")


def test_arquivo_corrompido_e_ignorado(tmp_path):
    (tmp_path / "20260101-000000-abcdef.json").write_text("{quebrado", encoding="utf-8")
    assert historico.listar() == []
