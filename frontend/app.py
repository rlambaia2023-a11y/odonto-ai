import streamlit as st
import requests

API = "http://localhost:8000"

st.title("OdontoAI")
q = st.text_input("Pergunta")
if st.button("Enviar") and q:
    r = requests.post(f"{API}/chat", json={"question": q})
    st.write(r.json().get("answer", ""))

st.subheader("Atualizar base")
if st.button("Rodar update (1 dia)"):
    r = requests.post(f"{API}/update", params={"days": 1})
    st.write(r.json())

st.subheader("Radiografia")
f = st.file_uploader("Upload imagem", type=["png", "jpg", "jpeg"])
if f and st.button("Analisar"):
    r = requests.post(f"{API}/radiograph", files={"file": f.getvalue()}, data={"question": "Descreva achados."})
    st.write(r.json().get("analysis", ""))
