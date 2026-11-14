"""Estado local do robô (SQLite): garante idempotência e guarda cache para quando uma fonte cair."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS linhas_importadas (
  hash TEXT PRIMARY KEY,           -- impressão digital da linha (sem dado pessoal)
  resultado TEXT NOT NULL,         -- enviado | duplicata | rejeitado
  relato_id TEXT,
  arquivo TEXT NOT NULL,
  em TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cache (
  chave TEXT PRIMARY KEY,
  valor TEXT NOT NULL,
  em TEXT NOT NULL
);
"""


def _agora() -> datetime:
    return datetime.now(timezone.utc)


class Estado:
    def __init__(self, caminho: Path):
        caminho.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(caminho, isolation_level=None)
        self.conn.executescript(SCHEMA)

    def ja_processada(self, hash_linha: str) -> str | None:
        linha = self.conn.execute("SELECT resultado FROM linhas_importadas WHERE hash = ?", (hash_linha,)).fetchone()
        return linha[0] if linha else None

    def marcar_linha(self, hash_linha: str, resultado: str, arquivo: str, relato_id: str | None = None) -> None:
        self.conn.execute("INSERT OR REPLACE INTO linhas_importadas VALUES (?, ?, ?, ?, ?)",
                          (hash_linha, resultado, relato_id, arquivo, _agora().isoformat()))

    def gravar_cache(self, chave: str, valor) -> None:
        self.conn.execute("INSERT OR REPLACE INTO cache VALUES (?, ?, ?)",
                          (chave, json.dumps(valor), _agora().isoformat()))

    def ler_cache(self, chave: str, validade_horas: float | None = None):
        """Devolve (valor, idade_horas) ou (None, None)."""
        linha = self.conn.execute("SELECT valor, em FROM cache WHERE chave = ?", (chave,)).fetchone()
        if not linha:
            return None, None
        idade = (_agora() - datetime.fromisoformat(linha[1])) / timedelta(hours=1)
        if validade_horas is not None and idade > validade_horas:
            return None, idade
        return json.loads(linha[0]), idade

    def fechar(self) -> None:
        self.conn.close()
