"""Logs de auditoria: um evento por linha (JSON Lines) e um resumo por execução.

Cada evento traz data/hora, run_id, etapa, nível, item e resultado. Os campos passam por
lgpd.limpar_campos antes de serem gravados, então dado pessoal não chega ao log.
"""
import json
import sys
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from . import lgpd

SUCESSO = "SUCESSO"
SUCESSO_COM_ALERTAS = "SUCESSO_COM_ALERTAS"
FALHA = "FALHA"


def agora_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class Auditoria:
    def __init__(self, dir_logs: Path, dir_saida: Path, comando: str, simulacao: bool, eco=True):
        self.run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
        self.dir_logs, self.dir_saida = dir_logs, dir_saida
        self.comando, self.simulacao, self.eco = comando, simulacao, eco
        self.inicio = agora_iso()
        self.contadores: Counter = Counter()
        self.alertas: list[str] = []
        self.erros: list[str] = []
        dir_logs.mkdir(parents=True, exist_ok=True)
        self.arquivo = dir_logs / f"robo-{datetime.now(timezone.utc):%Y-%m-%d}.jsonl"

    def registrar(self, nivel: str, etapa: str, evento: str, mensagem: str = "", **campos) -> None:
        registro = {"ts": agora_iso(), "run_id": self.run_id, "nivel": nivel, "etapa": etapa,
                    "evento": evento, "mensagem": lgpd.mascarar(mensagem) or ""}
        registro.update(lgpd.limpar_campos(campos))
        with self.arquivo.open("a", encoding="utf-8") as f:
            f.write(json.dumps(registro, ensure_ascii=False) + "\n")
        if nivel == "WARN":
            self.alertas.append(f"{etapa}/{evento}: {registro['mensagem']}")
        elif nivel == "ERROR":
            self.erros.append(f"{etapa}/{evento}: {registro['mensagem']}")
        if self.eco:
            extra = " ".join(f"{k}={v}" for k, v in lgpd.limpar_campos(campos).items())
            print(f"{registro['ts']} {nivel:<5} [{etapa}] {evento} {registro['mensagem']} {extra}".rstrip(),
                  file=sys.stdout, flush=True)

    def info(self, etapa, evento, mensagem="", **campos):
        self.registrar("INFO", etapa, evento, mensagem, **campos)

    def alerta(self, etapa, evento, mensagem="", **campos):
        self.registrar("WARN", etapa, evento, mensagem, **campos)

    def erro(self, etapa, evento, mensagem="", **campos):
        self.registrar("ERROR", etapa, evento, mensagem, **campos)

    def contar(self, chave: str, n: int = 1) -> None:
        self.contadores[chave] += n

    def status_final(self, falha_fatal: bool) -> str:
        if falha_fatal:
            return FALHA
        return SUCESSO_COM_ALERTAS if (self.alertas or self.erros) else SUCESSO

    def fechar(self, falha_fatal: bool = False) -> dict:
        status = self.status_final(falha_fatal)
        resumo = {
            "run_id": self.run_id, "comando": self.comando, "simulacao": self.simulacao,
            "inicio": self.inicio, "fim": agora_iso(), "status": status,
            "contadores": dict(sorted(self.contadores.items())),
            "alertas": self.alertas, "erros": self.erros, "log": str(self.arquivo.name),
        }
        pasta = self.dir_saida / "execucoes"
        pasta.mkdir(parents=True, exist_ok=True)
        (pasta / f"{self.run_id}.json").write_text(json.dumps(resumo, ensure_ascii=False, indent=2), encoding="utf-8")
        self.registrar("INFO" if status != FALHA else "ERROR", "execucao", "fim", f"status={status}",
                       **{k: v for k, v in resumo["contadores"].items()})
        return resumo
