"""Bot do corretor (Telegram e WhatsApp): conversa, canais e webhook."""
import hashlib
import hmac
import io
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from src.bot import app as app_mod
from src.bot.formatar import br, dividir, para_telegram, para_whatsapp
from src.bot.nucleo import Arquivo, Bot
from src.bot.telegram import Telegram
from src.bot.whatsapp import WhatsApp, assinatura_valida
from src.grading import historico
from src.grading.gabarito_ia import SYSTEM as SYS_GABARITO, SYSTEM_OCR
from src.grading.papel import SYSTEM_TRANSCRICAO

GAB_IA = {
    "titulo": "P1 Perio", "disciplina": "Periodontia",
    "questoes": [
        {"id": "Q1", "tipo": "objetiva", "valor": 1, "enunciado": "Microrganismo", "gabarito": "B", "confianca": 0.9},
        {"id": "Q2", "tipo": "discursiva", "valor": 2, "enunciado": "Defina gengivite", "resposta_esperada": "Inflamação",
         "rubrica": [{"descricao": "Inflamação", "pontos": 2}], "confianca": 0.9},
    ],
}


def llm_falso():
    chamadas = []

    def fn(system, user, imagens=None):
        chamadas.append(system)
        if system == SYSTEM_OCR:
            return "1) Qual microrganismo? 2) Defina gengivite."
        if system == SYS_GABARITO:
            return json.dumps(GAB_IA)
        if system == SYSTEM_TRANSCRICAO:
            return json.dumps({"aluno": f"Aluno {len(chamadas)}", "respostas": {"Q1": "B", "Q2": "inflamação da gengiva"}})
        return json.dumps({"criterios": [{"pontos_obtidos": 2}], "nota": 2, "feedback": "completo", "confianca": 0.95})

    fn.chamadas = chamadas
    return fn


class CanalFalso:
    nome = "telegram"

    def __init__(self):
        self.textos: list[str] = []
        self.arquivos: list[tuple[str, bytes, str]] = []

    def enviar_texto(self, chat, md):
        self.textos.append(md)

    def enviar_arquivo(self, chat, nome, dados, mime):
        self.arquivos.append((nome, dados, mime))

    def tudo(self) -> str:
        return "\n".join(self.textos)


def png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 40), "white").save(buf, format="PNG")
    return buf.getvalue()


def pdf(n: int) -> bytes:
    imgs = [Image.new("RGB", (100, 140), "white") for _ in range(n)]
    buf = io.BytesIO()
    imgs[0].save(buf, format="PDF", save_all=True, append_images=imgs[1:])
    return buf.getvalue()


@pytest.fixture
def ambiente(tmp_path, monkeypatch):
    monkeypatch.setenv("HISTORICO_DIR", str(tmp_path / "historico"))
    monkeypatch.setenv("BOT_TELEGRAM_IDS", "123")
    monkeypatch.setenv("BOT_WHATSAPP_NUMEROS", "5511999990000")
    llm = llm_falso()
    return Bot(pasta=tmp_path / "bot", llm=llm, auto_segundos=0, sincrono=True), CanalFalso(), llm


def montar(bot, canal):
    bot.receber(canal, "123", arquivos=[Arquivo("prova.png", "image/png", png())])
    bot.receber(canal, "123", "pronto")


# ---------- Autorização ----------

def test_sem_configuracao_informa_o_id(tmp_path, monkeypatch):
    monkeypatch.delenv("BOT_TELEGRAM_IDS", raising=False)
    bot, canal = Bot(pasta=tmp_path, llm=llm_falso(), auto_segundos=0, sincrono=True), CanalFalso()
    bot.receber(canal, "777", "oi")
    assert "777" in canal.tudo() and "BOT_TELEGRAM_IDS" in canal.tudo()


def test_estranho_e_ignorado_em_silencio(ambiente):
    bot, canal, llm = ambiente
    bot.receber(canal, "999", arquivos=[Arquivo("x.png", "image/png", png())])
    bot.receber(canal, "999", "pronto")
    assert canal.textos == [] and llm.chamadas == []


# ---------- Fluxo completo ----------

def test_gabarito_e_prova_avulsa(ambiente):
    bot, canal, llm = ambiente
    montar(bot, canal)
    texto = canal.tudo()
    assert "Gabarito pronto: P1 Perio" in texto and "Q1: **B**" in texto
    assert "Resposta: B" in texto  # resolução comentada vai junto
    assert canal.arquivos[0][0].endswith(".json")
    assert json.loads(canal.arquivos[0][1])["questoes"][0]["gabarito"] == "B"
    assert SYSTEM_OCR in llm.chamadas  # foto passa por OCR antes de resolver

    canal.textos.clear()
    # Duas páginas da mesma prova (modo padrão depois do gabarito)
    bot.receber(canal, "123", arquivos=[Arquivo("p1.png", "image/png", png())])
    bot.receber(canal, "123", arquivos=[Arquivo("p2.png", "image/png", png())])
    assert "uma prova" in canal.textos[0]
    bot.receber(canal, "123", "pronto")
    assert "3 de 3" in canal.tudo()  # Q1 1/1 + Q2 2/2
    tipos = [r["tipo"] for r in historico.listar()]
    assert sorted(tipos) == ["gabarito", "provas"]


def test_varias_provas_geram_planilha(ambiente):
    bot, canal, _ = ambiente
    montar(bot, canal)
    canal.arquivos.clear()
    bot.receber(canal, "123", "/provas")
    for i in range(3):
        bot.receber(canal, "123", arquivos=[Arquivo(f"aluno{i}.png", "image/png", png())])
    bot.receber(canal, "123", "corrigir")
    assert "3 provas corrigidas" in canal.tudo()
    nome, dados, _ = canal.arquivos[0]
    assert nome.endswith(".csv") and dados.decode("utf-8").count("\n") == 4  # cabeçalho + 3 alunos


def test_pdf_da_turma(ambiente):
    bot, canal, _ = ambiente
    montar(bot, canal)
    bot.receber(canal, "123", "/turma 2")
    bot.receber(canal, "123", arquivos=[Arquivo("turma.pdf", "application/pdf", pdf(4))])
    bot.receber(canal, "123", "pronto")
    assert "2 provas corrigidas" in canal.tudo()


def test_turma_com_paginas_que_nao_fecham(ambiente):
    bot, canal, _ = ambiente
    montar(bot, canal)
    bot.receber(canal, "123", "/turma 2")
    bot.receber(canal, "123", arquivos=[Arquivo("turma.pdf", "application/pdf", pdf(3))])
    bot.receber(canal, "123", "pronto")
    assert "Não consegui terminar" in canal.tudo() and "não dividem" in canal.tudo()


def test_gabarito_json_e_reuso_pelo_historico(ambiente, tmp_path):
    bot, canal, _ = ambiente
    montar(bot, canal)
    dados_json = canal.arquivos[0][1]
    # Outro chat (WhatsApp) carrega o .json direto
    wa = CanalFalso()
    wa.nome = "whatsapp"
    bot.receber(wa, "5511999990000", arquivos=[Arquivo("gabarito.json", "application/json", dados_json)])
    assert "Gabarito **P1 Perio** carregado" in wa.tudo()
    # E o mesmo gabarito pode ser reativado pelo histórico
    bot.receber(canal, "123", "/historico")
    assert "1." in canal.textos[-1]
    bot.receber(canal, "123", "/usar 1")
    assert "ativo" in canal.textos[-1]


def test_texto_colado_vira_prova(ambiente):
    bot, canal, llm = ambiente
    bot.receber(canal, "123", "/gabarito 20 aceitar sinônimos")
    assert "valor total 20" in canal.textos[-1]
    bot.receber(canal, "123", "1) Qual o principal microrganismo da periodontite? a) ... b) ...")
    bot.receber(canal, "123", "pronto")
    assert "Gabarito pronto" in canal.tudo()
    assert SYSTEM_OCR not in llm.chamadas  # texto não passa por OCR


def test_cancelar_e_pronto_sem_arquivos(ambiente):
    bot, canal, _ = ambiente
    bot.receber(canal, "123", arquivos=[Arquivo("prova.png", "image/png", png())])
    bot.receber(canal, "123", "/cancelar")
    bot.receber(canal, "123", "pronto")
    assert "Não há arquivos esperando" in canal.textos[-1]


def test_comeca_sozinho_depois_do_ultimo_arquivo(tmp_path, monkeypatch):
    monkeypatch.setenv("HISTORICO_DIR", str(tmp_path / "h"))
    monkeypatch.setenv("BOT_TELEGRAM_IDS", "123")
    bot, canal = Bot(pasta=tmp_path / "b", llm=llm_falso(), auto_segundos=0.1, sincrono=True), CanalFalso()
    bot.receber(canal, "123", arquivos=[Arquivo("prova.png", "image/png", png())])
    for _ in range(50):
        if "Gabarito pronto" in canal.tudo():
            break
        time.sleep(0.05)
    assert "Gabarito pronto" in canal.tudo()


# ---------- Formatação ----------

def test_formatacao():
    md = "# Resolução: A<B\n- **Resposta: B)** veja <https://x.org/a>"
    assert para_whatsapp(md) == "*Resolução: A<B*\n- *Resposta: B)* veja https://x.org/a"
    tg = para_telegram(md)
    assert "<b>Resolução: A&lt;B</b>" in tg and "<b>Resposta: B)</b>" in tg and "https://x.org/a" in tg
    partes = dividir("\n\n".join(["linha " * 50] * 40), limite=1000)
    assert all(len(p) <= 1000 for p in partes) and len(partes) > 1
    assert br(7.5) == "7,5" and br(8.0) == "8"


# ---------- Telegram ----------

def test_telegram_baixa_foto_e_envia():
    enviados = []

    def rota(req: httpx.Request):
        if req.url.path.endswith("/getFile"):
            return httpx.Response(200, json={"ok": True, "result": {"file_path": "photos/f.jpg"}})
        if "/file/bot" in req.url.path:
            return httpx.Response(200, content=b"JPEGDATA")
        enviados.append((req.url.path.rsplit("/", 1)[-1], req.content))
        return httpx.Response(200, json={"ok": True, "result": {}})

    tg = Telegram("TOKEN", http=httpx.Client(transport=httpx.MockTransport(rota)))
    chat, texto, arqs = tg.entrada({"message_id": 5, "chat": {"id": 123}, "caption": "oi",
                                    "photo": [{"file_id": "pequena"}, {"file_id": "grande"}]})
    assert (chat, texto, arqs[0].dados, arqs[0].mime) == ("123", "oi", b"JPEGDATA", "image/jpeg")
    tg.enviar_texto("123", "**Nota: 8**")
    assert enviados[0][0] == "sendMessage" and b"<b>Nota: 8</b>" in enviados[0][1]


def test_telegram_recusa_arquivo_grande():
    tg = Telegram("TOKEN", http=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))))
    with pytest.raises(ValueError, match="20MB"):
        tg.entrada({"chat": {"id": 1}, "document": {"file_id": "x", "file_size": 30 * 1024 * 1024}})


# ---------- WhatsApp ----------

def payload_wa(numero="5511999990000", msg_id="wamid.1", texto="/status"):
    return {"entry": [{"changes": [{"value": {
        "metadata": {"phone_number_id": "PNID"},
        "messages": [{"id": msg_id, "from": numero, "type": "text", "text": {"body": texto}}],
    }}]}]}


def test_whatsapp_responde_e_ignora_repetidas(ambiente):
    bot, _, _ = ambiente
    enviados = []

    def rota(req: httpx.Request):
        enviados.append(json.loads(req.content))
        return httpx.Response(200, json={"messages": [{"id": "x"}]})

    wa = WhatsApp("TOKEN", "PNID", http=httpx.Client(transport=httpx.MockTransport(rota)))
    wa.tratar(bot, payload_wa())
    wa.tratar(bot, payload_wa())  # reenvio da Meta: não responde de novo
    assert len(enviados) == 1
    assert enviados[0]["to"] == "5511999990000" and "*Gabarito:* nenhum" in enviados[0]["text"]["body"]


def test_whatsapp_envia_csv_como_texto():
    chamadas = []

    def rota(req: httpx.Request):
        chamadas.append(req)
        if req.url.path.endswith("/media"):
            return httpx.Response(200, json={"id": "MEDIA1"})
        return httpx.Response(200, json={})

    wa = WhatsApp("TOKEN", "PNID", http=httpx.Client(transport=httpx.MockTransport(rota)))
    wa.enviar_arquivo("55", "notas.csv", b"a;b", "text/csv")
    assert b"text/plain" in chamadas[0].content
    assert json.loads(chamadas[1].content)["document"] == {"id": "MEDIA1", "filename": "notas.csv"}


def test_webhook_whatsapp(monkeypatch):
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "verif")
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "segredo")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("WHATSAPP_TOKEN", raising=False)
    with TestClient(app_mod.app) as c:
        ok = c.get("/whatsapp/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "verif", "hub.challenge": "42"})
        assert ok.status_code == 200 and ok.text == "42"
        assert c.get("/whatsapp/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "errado"}).status_code == 403

        tratados = []
        app_mod._whatsapp = type("WA", (), {"tratar": lambda self, bot, p: tratados.append(p)})()
        corpo = json.dumps(payload_wa()).encode()
        assert c.post("/whatsapp/webhook", content=corpo, headers={"x-hub-signature-256": "sha256=00"}).status_code == 401
        assin = "sha256=" + hmac.new(b"segredo", corpo, hashlib.sha256).hexdigest()
        assert c.post("/whatsapp/webhook", content=corpo, headers={"x-hub-signature-256": assin}).status_code == 200
        assert tratados == [payload_wa()]
        assert c.get("/health").json()["ok"] is True
    app_mod._whatsapp = None


def test_assinatura():
    assert not assinatura_valida("", b"x", "sha256=abc")
    assert not assinatura_valida("s", b"x", "abc")
