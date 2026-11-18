"""Tela de correção de provas.

Roda sozinha (deploy do corretor):  streamlit run frontend/pages/1_Corrigir_provas.py
ou como página do app OdontoAI (menu lateral). Chama o módulo src.grading direto, sem API.
Acesso protegido pela variável CORRETOR_PASSWORD: sem ela definida, a tela não abre.
"""
import hmac
import io
import json
import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from src.grading import (  # noqa: E402
    Entrega, Gabarito, corrigir_provas_papel, corrigir_turma, dividir_pdf_turma, ler_respostas_csv, resultados_csv,
    resumo_turma, transcricoes_csv,
)
from src.grading.papel import EXT_IMAGEM, ProvaPapel, pdf_para_imagens, transcrever_prova  # noqa: E402

st.set_page_config(page_title="Corrigir provas · OdontoAI", page_icon="🦷", layout="wide")


# ---------- Acesso ----------

def exigir_senha() -> None:
    senha = os.getenv("CORRETOR_PASSWORD", "")
    if not senha:
        st.error("CORRETOR_PASSWORD não configurada no servidor. Defina a variável para liberar o acesso.")
        st.stop()
    if st.session_state.get("autorizado"):
        return
    st.title("Corretor de provas")
    with st.form("login"):
        tentativa = st.text_input("Senha", type="password")
        ok = st.form_submit_button("Entrar")
    if ok:
        if hmac.compare_digest(tentativa.encode(), senha.encode()):
            st.session_state["autorizado"] = True
            st.rerun()
        st.error("Senha incorreta.")
    st.stop()


exigir_senha()


# ---------- Utilidades ----------

def ler_gabarito(arquivo) -> Gabarito | None:
    try:
        return Gabarito.model_validate(json.loads(arquivo.getvalue().decode("utf-8-sig")))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        st.error(f"Gabarito não é um JSON válido: {e}")
    except ValidationError as e:
        st.error("Gabarito inválido:\n\n" + "\n".join(f"- {err['msg']}" for err in e.errors()))
    return None


def paginas_do_upload(f) -> list[tuple[bytes, str]]:
    dados = f.getvalue()
    if f.name.lower().endswith(".pdf"):
        return pdf_para_imagens(dados)
    return [(dados, EXT_IMAGEM[Path(f.name).suffix.lower()])]


def barra(rotulo: str):
    b = st.progress(0.0, text=rotulo)
    return lambda feitas, total: b.progress(feitas / total, text=f"{rotulo} {feitas}/{total}")


def tabela(texto_csv: str) -> pd.DataFrame:
    return pd.read_csv(io.StringIO(texto_csv.lstrip("﻿")), sep=";", dtype=str).fillna("")


# ---------- Tela ----------

st.title("Corrigir provas")
st.caption(
    "Objetivas: corrigidas na hora pelo gabarito. Discursivas: corrigidas pela IA seguindo a sua rubrica. "
    "O que for ambíguo ou de baixa confiança fica marcado para sua revisão. A nota final é sempre do professor."
)
with st.expander("Como funciona"):
    st.markdown(
        "1. **Gabarito (.json):** questões com `id`, `tipo` (objetiva/discursiva), `valor`, `gabarito` (letra) "
        "ou `resposta_esperada` e `rubrica`.\n"
        "2. **Provas em papel:** aba *Provas dos alunos*. Envie os PDFs ou fotos e clique em *Corrigir provas*. "
        "Leituras duvidosas (nome não encontrado, marcação ambígua, letra ilegível) ficam marcadas para sua revisão.\n"
        "3. **Provas no computador:** CSV com coluna `aluno` + `Q1`, `Q2`... (Google Forms, Moodle)."
    )
    modelo = RAIZ / "examples" / "gabarito_exemplo.json"
    if modelo.exists():
        st.download_button("Baixar modelo de gabarito", modelo.read_bytes(), "gabarito_modelo.json", "application/json")

gab_file = st.file_uploader("Gabarito e rubrica (.json)", type=["json"])
gab = ler_gabarito(gab_file) if gab_file else None
if gab:
    st.success(f"{gab.titulo}: {len(gab.questoes)} questões, valendo {gab.valor_total:g} pontos.")

aba_provas, aba_planilhas, aba_uma = st.tabs(["Provas dos alunos", "Provas feitas no computador", "Uma prova avulsa"])


def mostrar_resultado(chave: str) -> None:
    """Métricas, tabela e downloads de uma correção guardada em st.session_state[chave]."""
    dados = st.session_state[chave]
    r = dados["resumo"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Alunos", r["alunos"])
    c2.metric("Média", r["media"])
    c3.metric("Mediana", r["mediana"])
    c4.metric("Para sua revisão", r["a_revisar"])
    b1, b2 = st.columns(2)
    b1.download_button("Baixar notas (.csv)", dados["notas"].encode("utf-8"), "notas.csv", "text/csv",
                       type="primary", key=f"dl_notas_{chave}")
    if dados.get("transcricoes"):
        b2.download_button("Baixar o que a IA leu de cada prova (.csv)", dados["transcricoes"].encode("utf-8"),
                           "leitura_das_provas.csv", "text/csv", key=f"dl_leitura_{chave}")
    df = tabela(dados["notas"])
    if st.toggle("Mostrar só os que precisam de revisão", value=r["a_revisar"] > 0, key=f"tg_{chave}"):
        df = df[df["revisar"] == "SIM"]
    st.dataframe(df, use_container_width=True, hide_index=True)


with aba_provas:
    st.write("Envie as provas digitalizadas ou fotografadas. O sistema lê cada uma e já devolve as notas.")
    modo = st.radio(
        "Como estão as provas?",
        ["Um arquivo por aluno (PDF ou foto)", "Um PDF só com a turma inteira"],
        horizontal=True,
    )
    if modo.startswith("Um arquivo"):
        provas = st.file_uploader("Provas dos alunos", type=["pdf", "png", "jpg", "jpeg", "webp"], accept_multiple_files=True)
        pronto = bool(gab and provas)
    else:
        pdf_turma = st.file_uploader("PDF da turma (provas em sequência)", type=["pdf"])
        ppa = st.number_input("Quantas páginas tem cada prova?", min_value=1, max_value=20, value=2)
        pronto = bool(gab and pdf_turma)
    if not gab:
        st.info("Envie primeiro o gabarito e a rubrica, no campo acima.")
    if st.button("Corrigir provas", type="primary", disabled=not pronto):
        try:
            with st.spinner("Preparando as páginas..."):
                if modo.startswith("Um arquivo"):
                    lote = [ProvaPapel(origem=f.name, paginas=paginas_do_upload(f)) for f in provas]
                else:
                    lote = dividir_pdf_turma(pdf_turma.getvalue(), int(ppa), pdf_turma.name)
        except ValueError as e:
            st.error(str(e))
            st.stop()
        itens, res = corrigir_provas_papel(
            lote, gab, progresso_leitura=barra("Lendo provas"), progresso_correcao=barra("Corrigindo")
        )
        st.session_state["res_provas"] = {
            "notas": resultados_csv(gab, res),
            "resumo": resumo_turma(res),
            "transcricoes": transcricoes_csv(gab, itens),
        }
    if "res_provas" in st.session_state:
        mostrar_resultado("res_provas")

with aba_planilhas:
    planilhas = st.file_uploader("Planilhas de respostas (.csv), ex.: Google Forms ou Moodle", type=["csv"], accept_multiple_files=True)
    literatura = False  # a base de literatura do OdontoAI não está neste servidor
    if st.button("Corrigir turma", type="primary", disabled=not (gab and planilhas)):
        try:
            entregas: list[Entrega] = [e for f in planilhas for e in ler_respostas_csv(f.getvalue())]
        except (ValueError, UnicodeDecodeError) as e:
            st.error(f"Planilha inválida: {e}")
            st.stop()
        res = corrigir_turma(gab, entregas, usar_literatura=literatura, progresso=barra("Corrigindo discursivas"))
        st.session_state["res_planilhas"] = {"notas": resultados_csv(gab, res), "resumo": resumo_turma(res)}
    if "res_planilhas" in st.session_state:
        mostrar_resultado("res_planilhas")

with aba_uma:
    st.write("Envie as páginas de uma prova (na ordem). Confira a transcrição antes de corrigir.")
    paginas = st.file_uploader("Páginas", type=["pdf", "png", "jpg", "jpeg", "webp"], accept_multiple_files=True)
    if st.button("Ler prova", disabled=not (gab and paginas)):
        with st.spinner("Lendo a prova..."):
            try:
                todas = [p for f in paginas for p in paginas_do_upload(f)]
                st.session_state["avulsa"] = transcrever_prova(todas, gab, origem=paginas[0].name).entrega.model_dump()
            except Exception as e:
                st.error(f"Não foi possível ler a prova: {e}")
    if "avulsa" in st.session_state and gab:
        t = st.session_state["avulsa"]
        aluno = st.text_input("Aluno", t.get("aluno", ""))
        respostas = {q.id: st.text_area(q.id, t["respostas"].get(q.id, ""), height=80) for q in gab.questoes}
        if st.button("Corrigir esta prova", type="primary"):
            with st.spinner("Corrigindo..."):
                res = corrigir_turma(gab, [Entrega(aluno=aluno or "Sem nome", respostas=respostas)])[0]
            st.metric("Nota", f"{res.nota:g} / {res.valor_total:g}")
            for q in res.questoes:
                marca = " ⚠️ revisar" if q.revisar else ""
                st.markdown(f"**{q.id}**: {q.nota:g}/{q.valor:g}{marca}. {q.feedback}")
