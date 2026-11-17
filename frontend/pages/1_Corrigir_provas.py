"""Tela de correção de provas (Streamlit multipage: aparece no menu lateral)."""
import json
import os

import requests
import streamlit as st

API = os.getenv("ODONTOAI_API", "http://localhost:8000")
HEADERS = {"x-api-key": os.getenv("API_KEY", "")}

st.set_page_config(page_title="Corrigir provas · OdontoAI", layout="wide")
st.title("Corrigir provas")
st.caption(
    "Objetivas são corrigidas na hora pelo gabarito. Discursivas são corrigidas pela IA seguindo a sua rubrica; "
    "as de baixa confiança ficam marcadas para sua revisão. A nota final é sempre do professor."
)

with st.expander("Formato dos arquivos"):
    st.markdown(
        "- **Gabarito (.json):** questões com `id`, `tipo` (objetiva/discursiva), `valor`, `gabarito` (letra) "
        "ou `resposta_esperada` e `rubrica`. Veja `examples/gabarito_exemplo.json`.\n"
        "- **Respostas (.csv):** coluna `aluno` + uma coluna por questão (`Q1`, `Q2`...). Pode exportar do Excel ou do Google Forms."
    )

gab_file = st.file_uploader("1. Gabarito e rubrica (.json)", type=["json"])
aba_planilha, aba_foto = st.tabs(["Respostas em planilha", "Prova em papel (foto)"])

with aba_planilha:
    resp_file = st.file_uploader("2. Respostas da turma (.csv)", type=["csv"])
    literatura = st.checkbox("Conferir discursivas com a literatura da base OdontoAI (mais lento)")
    if st.button("Corrigir turma", type="primary", disabled=not (gab_file and resp_file)):
        with st.spinner("Corrigindo..."):
            r = requests.post(
                f"{API}/grading/corrigir-planilha",
                files={"gabarito": gab_file.getvalue(), "respostas": resp_file.getvalue()},
                data={"usar_literatura": str(literatura).lower()},
                headers=HEADERS, timeout=600,
            )
        if r.ok:
            st.success("Pronto. Linhas com 'SIM' em revisar precisam do seu olhar.")
            st.download_button("Baixar notas (.csv)", r.content, "notas.csv", "text/csv")
            linhas = r.content.decode("utf-8-sig").splitlines()
            st.dataframe([l.split(";") for l in linhas[1:]], use_container_width=True)
        else:
            st.error(r.text)

with aba_foto:
    st.write("Envie a foto de uma prova. Confira a transcrição antes de corrigir.")
    foto = st.file_uploader("Foto da prova", type=["png", "jpg", "jpeg", "webp"])
    if st.button("Ler prova", disabled=not (gab_file and foto)):
        with st.spinner("Transcrevendo..."):
            r = requests.post(
                f"{API}/grading/transcrever",
                files={"gabarito": gab_file.getvalue(), "imagem": (foto.name, foto.getvalue(), foto.type)},
                headers=HEADERS, timeout=180,
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
