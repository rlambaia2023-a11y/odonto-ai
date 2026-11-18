"""Conversa do bot, igual no Telegram e no WhatsApp.

Fluxo: o professor manda a prova (vira gabarito) e depois as provas dos alunos (viram notas).
Os arquivos ficam guardados em disco até a execução; ela começa sozinha alguns segundos depois do
último arquivo (ou na hora, com "pronto"). O trabalho pesado roda em segundo plano para o
webhook responder rápido.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol

from ..grading import historico
from ..grading.gabarito_ia import ler_documento, montar_gabarito_completo, ocr_paginas, resolucao_para_markdown
from ..grading.grader import resultados_csv, resumo_turma
from ..grading.llm import LLMFn
from ..grading.models import Gabarito, ResultadoAluno
from ..grading.papel import EXT_IMAGEM, ProvaPapel, corrigir_provas_papel, dividir_pdf_turma, pdf_para_imagens, transcricoes_csv
from .formatar import br

logger = logging.getLogger(__name__)

MAX_PENDENTES = 200
MAX_BYTES_ARQUIVO = 100 * 1024 * 1024
EXT_POR_MIME = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "application/pdf": ".pdf",
                "text/plain": ".txt", "application/json": ".json",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx"}
MODOS = {
    "gabarito": "montar o gabarito",
    "avulsa": "uma prova (todos os arquivos são páginas do mesmo aluno)",
    "provas": "várias provas (cada arquivo é de um aluno)",
    "turma": "PDF da turma inteira",
}

AJUDA = """**Corretor de provas**

**1. Gabarito:** mande a sua prova (PDF, Word, fotos ou o texto colado). Eu resolvo, confiro na internet e devolvo as respostas em negrito.
  - /gabarito 10 aceitar sinônimos → valor total e orientações (opcional)
  - Também aceito um arquivo de gabarito .json

**2. Provas dos alunos** (depois do gabarito):
  - /avulsa → uma prova; mande as páginas (é o padrão)
  - /provas → várias provas, um arquivo por aluno
  - /turma 2 → um PDF com a turma inteira, 2 páginas por aluno

Começo sozinho alguns segundos depois do último arquivo, ou na hora com **pronto**.

**Outros:** /status · /historico · /usar 3 (reusa o gabarito nº 3 do histórico) · /pesquisa on|off · /cancelar · /id
Dica: no Telegram, mande as fotos como *arquivo* para não perderem qualidade."""


@dataclass
class Arquivo:
    nome: str
    mime: str
    dados: bytes


class Canal(Protocol):
    nome: str

    def enviar_texto(self, chat: str, md: str) -> None: ...

    def enviar_arquivo(self, chat: str, nome: str, dados: bytes, mime: str) -> None: ...


@dataclass
class Sessao:
    modo: str = ""
    paginas_por_aluno: int = 1
    nota_total: float = 10.0
    orientacoes: str = ""
    pesquisar: bool = True
    gabarito_json: str = ""
    historico_ids: list[str] = field(default_factory=list)  # última listagem, para /usar N


def _sem_acento(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn")


def _ids_permitidos(canal: str) -> set[str]:
    var = {"telegram": "BOT_TELEGRAM_IDS", "whatsapp": "BOT_WHATSAPP_NUMEROS"}.get(canal, "")
    return {re.sub(r"\D", "", x) for x in os.getenv(var, "").split(",") if re.sub(r"\D", "", x)}


class Bot:
    def __init__(
        self,
        pasta: Path | None = None,
        llm: LLMFn | None = None,
        auto_segundos: float | None = None,
        sincrono: bool = False,
    ):
        base = os.getenv("BOT_DIR") or (Path(os.environ["HISTORICO_DIR"]).parent / "bot" if os.getenv("HISTORICO_DIR") else None)
        self.pasta = Path(pasta or base or Path(__file__).resolve().parents[2] / "data" / "bot")
        self.pasta.mkdir(parents=True, exist_ok=True)
        self.llm = llm
        self.auto_segundos = float(os.getenv("BOT_AUTO_SEGUNDOS", "20")) if auto_segundos is None else auto_segundos
        self.sincrono = sincrono
        self._pool = ThreadPoolExecutor(max_workers=int(os.getenv("BOT_TRABALHOS", "2")), thread_name_prefix="bot")
        self._lock = threading.Lock()
        self._ocupados: set[str] = set()
        self._timers: dict[str, threading.Timer] = {}

    # ---------- Autorização ----------

    def autorizado(self, canal: str, chat: str) -> bool:
        return re.sub(r"\D", "", chat) in _ids_permitidos(canal)

    # ---------- Estado em disco ----------

    def _dir(self, canal: Canal, chat: str) -> Path:
        d = self.pasta / f"{canal.nome}-{re.sub(r'[^0-9A-Za-z_-]', '', chat)}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _sessao(self, canal: Canal, chat: str) -> Sessao:
        f = self._dir(canal, chat) / "sessao.json"
        try:
            return Sessao(**json.loads(f.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            return Sessao()

    def _salvar(self, canal: Canal, chat: str, s: Sessao) -> None:
        f = self._dir(canal, chat) / "sessao.json"
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(s), ensure_ascii=False), encoding="utf-8")
        tmp.replace(f)

    def _pendentes_dir(self, canal: Canal, chat: str) -> Path:
        d = self._dir(canal, chat) / "pendentes"
        d.mkdir(exist_ok=True)
        return d

    def _pendentes(self, canal: Canal, chat: str) -> list[Path]:
        return sorted(self._pendentes_dir(canal, chat).iterdir())

    def _guardar(self, canal: Canal, chat: str, arq: Arquivo) -> int:
        d = self._pendentes_dir(canal, chat)
        n = len(list(d.iterdir()))
        if n >= MAX_PENDENTES:
            raise ValueError(f"Limite de {MAX_PENDENTES} arquivos por vez")
        if len(arq.dados) > MAX_BYTES_ARQUIVO:
            raise ValueError(f"{arq.nome}: arquivo grande demais")
        nome = re.sub(r"[^\w.\- ]", "_", Path(arq.nome or "arquivo").name)[:80] or "arquivo"
        if not Path(nome).suffix:
            nome += EXT_POR_MIME.get(arq.mime, "")
        (d / f"{n + 1:03d}_{nome}").write_bytes(arq.dados)
        return n + 1

    def _limpar_pendentes(self, canal: Canal, chat: str) -> None:
        shutil.rmtree(self._pendentes_dir(canal, chat), ignore_errors=True)

    @staticmethod
    def _gab(s: Sessao) -> Gabarito | None:
        return Gabarito.model_validate_json(s.gabarito_json) if s.gabarito_json else None

    # ---------- Entrada ----------

    def receber(self, canal: Canal, chat: str, texto: str = "", arquivos: list[Arquivo] | None = None) -> None:
        """Ponto de entrada dos adaptadores. Nunca levanta exceção: erros viram mensagem."""
        try:
            self._receber(canal, chat, (texto or "").strip(), arquivos or [])
        except Exception as e:  # noqa: BLE001 - a conversa não pode morrer por um erro
            logger.exception("Erro no bot (%s %s)", canal.nome, chat)
            self._enviar(canal, chat, f"Deu um erro aqui: {e}")

    def _enviar(self, canal: Canal, chat: str, md: str) -> None:
        try:
            canal.enviar_texto(chat, md)
        except Exception:  # noqa: BLE001
            logger.exception("Falha ao enviar mensagem (%s %s)", canal.nome, chat)

    def _receber(self, canal: Canal, chat: str, texto: str, arquivos: list[Arquivo]) -> None:
        if not self.autorizado(canal.nome, chat):
            if not _ids_permitidos(canal.nome):  # ainda não configurado: ajuda a descobrir o próprio id
                var = "BOT_TELEGRAM_IDS" if canal.nome == "telegram" else "BOT_WHATSAPP_NUMEROS"
                self._enviar(canal, chat, f"Bot privado ainda não configurado. Seu identificador é **{chat}**: "
                                          f"cadastre-o na variável {var} do Railway.")
            else:
                logger.warning("Mensagem ignorada de %s %s (não autorizado)", canal.nome, chat)
            return

        s = self._sessao(canal, chat)
        if arquivos:
            self._receber_arquivos(canal, chat, s, arquivos, texto)
            return
        if not texto:
            return
        if self._comando(canal, chat, s, texto):
            return
        if s.modo == "gabarito" or (not s.gabarito_json and len(texto) > 200):
            # Texto longo = a prova colada no chat
            self._receber_arquivos(canal, chat, s, [Arquivo("prova.txt", "text/plain", texto.encode("utf-8"))], "")
            return
        self._enviar(canal, chat, "Não entendi. Mande /ajuda para ver o que eu faço.")

    def _receber_arquivos(self, canal: Canal, chat: str, s: Sessao, arquivos: list[Arquivo], legenda: str) -> None:
        # Um .json sozinho é um gabarito pronto
        if len(arquivos) == 1 and (arquivos[0].nome.lower().endswith(".json") or arquivos[0].mime == "application/json"):
            try:
                gab = Gabarito.model_validate_json(arquivos[0].dados)
            except ValueError as e:
                self._enviar(canal, chat, f"Esse .json não é um gabarito válido: {e}")
                return
            s.gabarito_json = gab.model_dump_json()
            s.modo = s.modo if s.modo in ("avulsa", "provas", "turma") else ""
            self._salvar(canal, chat, s)
            self._enviar(canal, chat, f"Gabarito **{gab.titulo}** carregado ({len(gab.questoes)} questões, "
                                      f"valor {br(gab.valor_total)}). Agora mande as provas dos alunos.")
            return

        primeiro = not self._pendentes(canal, chat)
        if not s.modo:
            s.modo = "avulsa" if s.gabarito_json else "gabarito"
            self._salvar(canal, chat, s)
        if s.modo == "gabarito" and legenda:  # legenda da foto/arquivo = orientações
            s.orientacoes = legenda
            self._salvar(canal, chat, s)
        for a in arquivos:
            total = self._guardar(canal, chat, a)
        if primeiro:
            dica = {
                "gabarito": "Mande as outras páginas, se houver.",
                "avulsa": "Mande as outras páginas desta prova. Se cada arquivo for de um aluno, mande /provas.",
                "provas": "Mande as provas dos outros alunos.",
                "turma": "",
            }[s.modo]
            quando = f"Começo em {self.auto_segundos:g}s sem novos arquivos" if self.auto_segundos > 0 else "Mande **pronto** para começar"
            self._enviar(canal, chat, f"Recebi para {MODOS[s.modo]}. {dica} {quando} (ou **pronto** para já).".replace("  ", " "))
        logger.info("%s %s: %d arquivo(s) pendente(s)", canal.nome, chat, total)
        self._agendar(canal, chat)

    # ---------- Comandos ----------

    def _comando(self, canal: Canal, chat: str, s: Sessao, texto: str) -> bool:
        partes = texto.split(maxsplit=1)
        cmd = _sem_acento(partes[0].lstrip("/").split("@")[0].lower())
        resto = partes[1].strip() if len(partes) > 1 else ""

        if cmd in ("start", "ajuda", "help", "menu", "oi", "ola"):
            self._enviar(canal, chat, AJUDA)
        elif cmd == "id":
            self._enviar(canal, chat, f"Seu identificador: {chat}")
        elif cmd == "gabarito":
            m = re.match(r"(\d+(?:[.,]\d+)?)\s*(.*)", resto, re.S)
            if m:
                s.nota_total = float(m.group(1).replace(",", "."))
                resto = m.group(2).strip()
            if resto:
                s.orientacoes = resto
            s.modo = "gabarito"
            self._salvar(canal, chat, s)
            extra = f" Orientações: {s.orientacoes}" if s.orientacoes else ""
            self._enviar(canal, chat, f"Ok, vou montar um gabarito novo (valor total {br(s.nota_total)}).{extra} "
                                      "Mande a prova: PDF, Word, fotos ou o texto.")
        elif cmd in ("avulsa", "provas", "turma"):
            if cmd == "turma":
                m = re.search(r"\d+", resto)
                if not m or not 1 <= int(m.group()) <= 20:
                    self._enviar(canal, chat, "Diga quantas páginas tem cada prova, ex.: /turma 2")
                    return True
                s.paginas_por_aluno = int(m.group())
            s.modo = cmd
            self._salvar(canal, chat, s)
            aviso = "" if s.gabarito_json else " Atenção: ainda não há gabarito; monte um antes (/gabarito)."
            extra = f", {s.paginas_por_aluno} página(s) por aluno" if cmd == "turma" else ""
            self._enviar(canal, chat, f"Modo: {MODOS[cmd]}{extra}. Pode mandar.{aviso}")
        elif cmd in ("pronto", "corrigir", "ok", "montar", "vai", "começar", "comecar"):
            self._cancelar_timer(canal, chat)
            self._disparar(canal, chat, avisar_vazio=True)
        elif cmd == "cancelar":
            self._cancelar_timer(canal, chat)
            self._limpar_pendentes(canal, chat)
            s.modo = ""
            self._salvar(canal, chat, s)
            self._enviar(canal, chat, "Arquivos pendentes descartados. O gabarito atual continua valendo.")
        elif cmd == "status":
            self._status(canal, chat, s)
        elif cmd == "historico":
            self._historico(canal, chat, s)
        elif cmd == "usar":
            self._usar(canal, chat, s, resto)
        elif cmd == "pesquisa":
            s.pesquisar = _sem_acento(resto.lower()) not in ("off", "nao", "desligar", "0")
            self._salvar(canal, chat, s)
            self._enviar(canal, chat, "Pesquisa na internet " + ("ligada" if s.pesquisar else "desligada (mais rápido)") + " para o próximo gabarito.")
        else:
            return False
        return True

    def _status(self, canal: Canal, chat: str, s: Sessao) -> None:
        gab = self._gab(s)
        linhas = [f"**Gabarito:** {gab.titulo} ({len(gab.questoes)} questões, valor {br(gab.valor_total)})" if gab
                  else "**Gabarito:** nenhum ainda"]
        linhas.append(f"**Modo:** {MODOS.get(s.modo, 'nenhum')}")
        linhas.append(f"**Arquivos esperando:** {len(self._pendentes(canal, chat))}")
        linhas.append(f"**Pesquisa na internet:** {'ligada' if s.pesquisar else 'desligada'}")
        if self._chave(canal, chat) in self._ocupados:
            linhas.append("⏳ Trabalhando em um pedido agora.")
        self._enviar(canal, chat, "\n".join(linhas))

    def _historico(self, canal: Canal, chat: str, s: Sessao) -> None:
        regs = historico.listar()[:10]
        if not regs:
            self._enviar(canal, chat, "Nenhum registro no histórico ainda.")
            return
        rot = {"gabarito": "Gabarito", "provas": "Provas", "planilhas": "Planilhas", "avulsa": "Prova avulsa"}
        s.historico_ids = [r["id"] for r in regs]
        self._salvar(canal, chat, s)
        linhas = ["**Últimos registros** (site e bot):"]
        for i, r in enumerate(regs, start=1):
            linhas.append(f"{i}. {historico.data_hora(r)} — {rot.get(r.get('tipo'), r.get('tipo'))}: {r.get('titulo', '')}")
        linhas.append("\nPara reusar um gabarito: /usar <número>")
        self._enviar(canal, chat, "\n".join(linhas))

    def _usar(self, canal: Canal, chat: str, s: Sessao, resto: str) -> None:
        m = re.search(r"\d+", resto)
        if not m or not s.historico_ids or not 1 <= int(m.group()) <= len(s.historico_ids):
            self._enviar(canal, chat, "Mande /historico primeiro e depois /usar com o número do gabarito.")
            return
        reg_id = s.historico_ids[int(m.group()) - 1]
        reg = next((r for r in historico.listar() if r.get("id") == reg_id), None)
        if not reg or reg.get("tipo") != "gabarito" or not reg.get("dados", {}).get("gabarito_json"):
            self._enviar(canal, chat, "Esse item não é um gabarito (ou foi excluído).")
            return
        gab = Gabarito.model_validate_json(reg["dados"]["gabarito_json"])
        s.gabarito_json = gab.model_dump_json()
        s.modo = ""
        self._salvar(canal, chat, s)
        self._enviar(canal, chat, f"Gabarito **{gab.titulo}** ativo ({len(gab.questoes)} questões). Pode mandar as provas.")

    # ---------- Execução ----------

    @staticmethod
    def _chave(canal: Canal, chat: str) -> str:
        return f"{canal.nome}-{chat}"

    def _cancelar_timer(self, canal: Canal, chat: str) -> None:
        with self._lock:
            t = self._timers.pop(self._chave(canal, chat), None)
        if t:
            t.cancel()

    def _agendar(self, canal: Canal, chat: str) -> None:
        if self.auto_segundos <= 0:
            return
        self._cancelar_timer(canal, chat)
        t = threading.Timer(self.auto_segundos, self._disparar, args=(canal, chat))
        t.daemon = True
        with self._lock:
            self._timers[self._chave(canal, chat)] = t
        t.start()

    def _disparar(self, canal: Canal, chat: str, avisar_vazio: bool = False) -> None:
        chave = self._chave(canal, chat)
        with self._lock:
            self._timers.pop(chave, None)
            if chave in self._ocupados:
                ocupado = True
            else:
                ocupado = False
                self._ocupados.add(chave)
        if ocupado:
            self._enviar(canal, chat, "Ainda estou terminando o pedido anterior. Assim que acabar, mande **pronto**.")
            return
        if self.sincrono:
            self._executar(canal, chat, avisar_vazio)
        else:
            self._pool.submit(self._executar, canal, chat, avisar_vazio)

    def _executar(self, canal: Canal, chat: str, avisar_vazio: bool) -> None:
        try:
            s = self._sessao(canal, chat)
            arquivos = self._pendentes(canal, chat)
            if not arquivos:
                if avisar_vazio:
                    self._enviar(canal, chat, "Não há arquivos esperando. Mande a prova primeiro.")
                return
            if s.modo == "gabarito" or not s.gabarito_json:
                self._montar_gabarito(canal, chat, s, arquivos)
            else:
                self._corrigir(canal, chat, s, arquivos)
        except Exception as e:  # noqa: BLE001
            logger.exception("Falha na execução (%s %s)", canal.nome, chat)
            self._enviar(canal, chat, f"Não consegui terminar: {e}\nOs arquivos continuam guardados; mande **pronto** para tentar de novo ou /cancelar.")
        finally:
            with self._lock:
                self._ocupados.discard(self._chave(canal, chat))

    def _montar_gabarito(self, canal: Canal, chat: str, s: Sessao, arquivos: list[Path]) -> None:
        paginas, textos = [], []
        for f in arquivos:
            pg, tx = ler_documento(f.name, f.read_bytes())
            paginas += pg
            if tx.strip():
                textos.append(tx)
        aviso = "conferindo cada resposta na internet, pode levar alguns minutos" if s.pesquisar else "resolvendo"
        self._enviar(canal, chat, f"Montando o gabarito ({len(paginas)} página(s) para ler; {aviso})...")
        ocr = ocr_paginas(paginas, llm=self.llm) if paginas else ""
        texto = "\n\n".join(textos + ([ocr] if ocr else []))
        gab, rascunhos, resol = montar_gabarito_completo(
            paginas, texto, nota_total=s.nota_total, orientacoes=s.orientacoes, llm=self.llm, pesquisar_web=s.pesquisar,
        )
        if s.orientacoes:
            gab.instrucoes_correcao = s.orientacoes
        s.gabarito_json = gab.model_dump_json()
        s.modo = ""
        self._salvar(canal, chat, s)
        self._limpar_pendentes(canal, chat)
        md = resolucao_para_markdown(resol, gab.titulo) if resol else ""
        _registrar("gabarito", gab.titulo, {"gabarito_json": gab.model_dump_json(indent=2), "resolucao_md": md,
                                            "questoes": len(gab.questoes)})

        resumo = " · ".join(f"{q.id}: **{q.gabarito}**" for q in gab.questoes if q.tipo == "objetiva")
        linhas = [f"✅ **Gabarito pronto: {gab.titulo}** ({len(gab.questoes)} questões, valor {br(gab.valor_total)})"]
        if resumo:
            linhas.append(resumo)
        if rascunhos:
            linhas.append(f"⚠️ Conferir: {', '.join(rascunhos)}")
        canal.enviar_texto(chat, "\n".join(linhas))
        if md:
            canal.enviar_texto(chat, md)
        canal.enviar_arquivo(chat, _nome_arquivo(gab.titulo, "gabarito", ".json"),
                             gab.model_dump_json(indent=2).encode("utf-8"), "application/json")
        canal.enviar_texto(chat, "Agora mande as provas dos alunos (/avulsa, /provas ou /turma N).")

    def _corrigir(self, canal: Canal, chat: str, s: Sessao, arquivos: list[Path]) -> None:
        gab = self._gab(s)
        modo = s.modo if s.modo in ("avulsa", "provas", "turma") else "avulsa"
        if modo == "avulsa":
            lote = [ProvaPapel(origem=_original(arquivos[0]), paginas=[p for f in arquivos for p in _paginas(f)])]
        elif modo == "provas":
            lote = [ProvaPapel(origem=_original(f), paginas=_paginas(f)) for f in arquivos]
        else:
            lote = []
            for f in arquivos:
                if f.suffix.lower() != ".pdf":
                    raise ValueError(f"{_original(f)}: no modo turma mande um PDF")
                lote += dividir_pdf_turma(f.read_bytes(), s.paginas_por_aluno, _original(f))
        self._enviar(canal, chat, f"Corrigindo {len(lote)} prova(s) com o gabarito **{gab.titulo}**...")
        itens, res = corrigir_provas_papel(lote, gab, llm=self.llm)
        self._limpar_pendentes(canal, chat)
        notas = resultados_csv(gab, res)
        _registrar("provas", gab.titulo, {"notas": notas, "resumo": resumo_turma(res), "transcricoes": transcricoes_csv(gab, itens)})

        if len(res) == 1:
            canal.enviar_texto(chat, _detalhe_aluno(res[0]))
        else:
            r = resumo_turma(res)
            linhas = [f"✅ **{len(res)} provas corrigidas** — média {br(r['media'])}, mediana {br(r['mediana'])}, "
                      f"maior {br(r['maior'])}, menor {br(r['menor'])}"]
            for a in sorted(res, key=lambda x: x.aluno.lower()):
                linhas.append(f"- {a.aluno}: **{br(a.nota)}**/{br(a.valor_total)}" + (" ⚠️ revisar" if a.revisar else ""))
            if r["a_revisar"]:
                linhas.append(f"\n{r['a_revisar']} prova(s) marcadas para revisão; os motivos estão na planilha.")
            canal.enviar_texto(chat, "\n".join(linhas))
            canal.enviar_arquivo(chat, _nome_arquivo(gab.titulo, "notas", ".csv"), notas.encode("utf-8"), "text/csv")


# ---------- Auxiliares ----------

def _registrar(tipo: str, titulo: str, dados: dict) -> None:
    try:
        historico.salvar(tipo, titulo, dados)
    except Exception:  # noqa: BLE001 - histórico não pode atrapalhar a correção
        logger.exception("Falha ao gravar o histórico")


def _original(f: Path) -> str:
    return f.name.split("_", 1)[-1]


def _paginas(f: Path):
    ext = f.suffix.lower()
    if ext == ".pdf":
        return pdf_para_imagens(f.read_bytes())
    if ext in EXT_IMAGEM:
        return [(f.read_bytes(), EXT_IMAGEM[ext])]
    raise ValueError(f"{_original(f)}: para as provas dos alunos mande PDF ou foto")


def _nome_arquivo(titulo: str, prefixo: str, ext: str) -> str:
    base = re.sub(r"[^\w\-]+", "_", _sem_acento(titulo)).strip("_")[:40] or "prova"
    return f"{prefixo}_{base}{ext}"


def _detalhe_aluno(r: ResultadoAluno) -> str:
    linhas = [f"✅ **{r.aluno}: {br(r.nota)} de {br(r.valor_total)}**"]
    for q in r.questoes:
        marca = "✔️" if q.nota >= q.valor - 1e-9 else ("❌" if q.nota <= 1e-9 else "➗")
        linha = f"{marca} {q.id}: {br(q.nota)}/{br(q.valor)}"
        if q.tipo == "discursiva" and q.feedback:
            linha += f" — {q.feedback}"
        if q.revisar:
            linha += f" ⚠️ {q.motivo_revisao or 'revisar'}"
        linhas.append(linha)
    if r.pendencias_leitura:
        linhas.append("⚠️ Leitura: " + "; ".join(r.pendencias_leitura))
    return "\n".join(linhas)


_bot: Bot | None = None


def bot_padrao() -> Bot:
    global _bot
    if _bot is None:
        _bot = Bot()
    return _bot

