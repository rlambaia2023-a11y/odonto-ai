"""Tela de correção de provas (Streamlit multipage: aparece no menu lateral)."""
import io
import json
import os

import pandas as pd
import requests
import streamlit as st

API = os.getenv("ODONTOAI_API", "http://localhost:8000")
HEADERS = {"x-api-key": os.getenv("API_KEY", "")}
TIMEOUT_LOTE = 3600  # turmas de 250 alunos podem levar dezenas de minutos

st.set_page_config(page_title="Corrigir provas · OdontoAI", layout="wide")
st.title("Corrigir provas")
st.caption(
    "Objetivas são corrigidas na hora pelo gabarito. Discursivas são corrigidas pela IA seguindo a sua rubrica; "
    "as de baixa confiança ficam marcadas para sua revisão. A nota final é sempre do professor."
)

with st.expander("Como funciona com prova mista, em papel e no computador"):
    st.markdown(
        "1. **Gabarito (.json):** questões com `id`, `tipo` (objetiva/discursiva), `valor`, `gabarito` (letra) "
        "ou `resposta_esperada` e `rubrica`. Modelo: `examples/gabarito_exemplo.json`.\n"
        "2. **Provas em papel:** aba *Ler provas em papel*. Envie um PDF ou foto por aluno, ou o PDF da turma inteira "
        "digitalizada. Baixe a planilha, confira as linhas com a coluna `conferir` preenchida e apague esse texto.\n"
        "3. **Provas no computador:** exporte as respostas em CSV (coluna `aluno` + `Q1`, `Q2`...), "
        "por exemplo do Google Forms ou do Moodle.\n"
        "4. **Corrigir:** envie as duas planilhas juntas na aba *Corrigir*."
    )


def ler_csv(dados: bytes) -> pd.DataFrame:
    return pd.read_csv(io.BytesIO(dados), sep=";", encoding="utf-8-sig", dtype=str).fillna("")


gab_file = st.file_uploader("Gabarito e rubrica (.json)", type=["json"])
aba_corrigir, aba_papel, aba_uma = st.tabs(["Corrigir", "Ler provas em papel", "Uma prova avulsa"])

with aba_corrigir:
    resp_files = st.file_uploader("Planilhas de respostas (.csv), papel e computador", type=["csv"], accept_multiple_files=True)
    literatura = st.checkbox("Conferir discursivas com a literatura da base OdontoAI (mais lento)")
    if st.button("Corrigir turma", type="primary", disabled=not (gab_file and resp_files)):
        with st.spinner("Corrigindo... com 250 alunos isso pode levar alguns minutos."):
            r = requests.post(
                f"{API}/grading/corrigir-planilha",
                files=[("gabarito", ("gabarito.json", gab_file.getvalue()))]
                + [("respostas", (f.name, f.getvalue(), "text/csv")) for f in resp_files],
                data={"usar_literatura": str(literatura).lower()},
                headers=HEADERS, timeout=TIMEOUT_LOTE,
            )
        if r.ok:
            df = ler_csv(r.content)
            revisar = int((df["revisar"] == "SIM").sum())
            c1, c2, c3 = st.columns(3)
            c1.metric("Alunos", len(df))
            c2.metric("Média", f"{pd.to_numeric(df['nota'].str.replace(',', '.')).mean():.2f}")
            c3.metric("Para sua revisão", revisar)
            st.download_button("Baixar notas (.csv)", r.content, "notas.csv", "text/csv")
            so_revisar = st.toggle("Mostrar só os que precisam de revisão", value=revisar > 0)
            st.dataframe(df[df["revisar"] == "SIM"] if so_revisar else df, use_container_width=True)
        else:
            st.error(r.text)

with aba_papel:
    modo = st.radio("Como as provas foram digitalizadas?", ["Um arquivo por aluno", "PDF único da turma"], horizontal=True)
    if modo == "Um arquivo por aluno":
        provas = st.file_uploader("Provas (PDF ou foto, um por aluno)", type=["pdf", "png", "jpg", "jpeg", "webp"], accept_multiple_files=True)
        pronto = bool(gab_file and provas)
    else:
        pdf_turma = st.file_uploader("PDF da turma", type=["pdf"])
        ppa = st.number_input("Páginas de cada prova", min_value=1, max_value=20, value=2)
        pronto = bool(gab_file and pdf_turma)
    if st.button("Ler provas", type="primary", disabled=not pronto):
        with st.spinner("Lendo as provas..."):
            if modo == "Um arquivo por aluno":
                r = requests.post(
                    f"{API}/grading/transcrever-lote",
                    files=[("gabarito", ("gabarito.json", gab_file.getvalue()))]
                    + [("provas", (f.name, f.getvalue(), f.type)) for f in provas],
                    headers=HEADERS, timeout=TIMEOUT_LOTE,
                )
            else:
                r = requests.post(
                    f"{API}/grading/transcrever-pdf-turma",
                    files={"gabarito": ("gabarito.json", gab_file.getvalue()), "pdf": (pdf_turma.name, pdf_turma.getvalue(), "application/pdf")},
                    data={"paginas_por_aluno": int(ppa)},
                    headers=HEADERS, timeout=TIMEOUT_LOTE,
                )
        if r.ok:
            df = ler_csv(r.content)
            pend = int((df["conferir"] != "").sum())
            st.success(f"{len(df)} provas lidas. {pend} para conferir (coluna 'conferir').")
            st.download_button("Baixar respostas (.csv)", r.content, "respostas_papel.csv", "text/csv")
            st.dataframe(df, use_container_width=True)
        else:
            st.error(r.text)

with aba_uma:
    st.write("Envie as páginas de uma prova (na ordem). Confira a transcrição antes de corrigir.")
    paginas = st.file_uploader("Páginas", type=["pdf", "png", "jpg", "jpeg", "webp"], accept_multiple_files=True)
    if st.button("Ler prova", disabled=not (gab_file and paginas)):
        with st.spinner("Transcrevendo..."):
            r = requests.post(
                f"{API}/grading/transcrever",
                files=[("gabarito", ("gabarito.json", gab_file.getvalue()))]
                + [("paginas", (f.name, f.getvalue(), f.type)) for f in paginas],
                headers=HEADERS, timeout=300,
            )
        if r.ok:
            st.session_state["transcricao"] = r.json()
        else:
            st.error(r.text)
    if "transcricao" in st.session_state and gab_file:
        t = st.session_state["transcricao"]
        aluno = st.text_input("Aluno", t.get("aluno", ""))
        respostas = {k: st.text_area(k, v, height=80) for k, v in t.get("respostas", {}).items()}
        if st.button("Corrigir esta prova", type="primary"):
            corpo = {"gabarito": json.loads(gab_file.getvalue()), "entregas": [{"aluno": aluno, "respostas": respostas}]}
            r = requests.post(f"{API}/grading/corrigir", json=corpo, headers=HEADERS, timeout=300)
            if r.ok:
                res = r.json()["resultados"][0]
                st.metric("Nota", f"{res['nota']:g} / {res['valor_total']:g}")
                for q in res["questoes"]:
                    marca = " ⚠️ revisar" if q["revisar"] else ""
                    st.markdown(f"**{q['id']}**: {q['nota']:g}/{q['valor']:g}{marca}. {q['feedback']}")
            else:
                st.error(r.text)
