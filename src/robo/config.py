"""Configuração do robô: tudo vem de variáveis de ambiente (ou de um arquivo .env local).

Credenciais nunca ficam no código. O .env está no .gitignore.
"""
import os
from dataclasses import dataclass, field
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]

# Território do piloto (min_lat, min_lon, max_lat, max_lon). O valor oficial vem de GET /api/config do MVP;
# este é só o fallback quando a rota não responde.
BBOX_PADRAO = (-23.5700, -46.6600, -23.5590, -46.6450)


def carregar_dotenv(caminho: Path) -> None:
    """Lê KEY=VALUE de um .env sem sobrescrever o que já está no ambiente."""
    if not caminho.is_file():
        return
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, valor = linha.split("=", 1)
        os.environ.setdefault(chave.strip(), valor.strip().strip('"').strip("'"))


def _caminho(var: str, padrao: str) -> Path:
    valor = Path(os.getenv(var, padrao))
    return valor if valor.is_absolute() else RAIZ / valor


@dataclass
class Config:
    mvp_url: str = "https://caminholivre-gs.azurewebsites.net"
    mvp_admin_token: str = ""
    open_meteo_url: str = "https://api.open-meteo.com/v1/forecast"
    overpass_url: str = "https://overpass-api.de/api/interpreter,https://maps.mail.ru/osm/tools/overpass/api/interpreter"
    pois_snapshot: Path = field(default_factory=lambda: RAIZ / "dados" / "pois-snapshot.json")
    ntfy_url: str = ""
    dir_entrada: Path = field(default_factory=lambda: RAIZ / "entrada")
    dir_saida: Path = field(default_factory=lambda: RAIZ / "saida")
    dir_logs: Path = field(default_factory=lambda: RAIZ / "logs")
    estado_db: Path = field(default_factory=lambda: RAIZ / "saida" / "estado.db")
    http_timeout: float = 15.0
    http_tentativas: int = 3
    http_backoff: float = 2.0
    cache_pois_horas: int = 24 * 7
    marcar_demonstracao: bool = False  # acrescenta [demonstração] à descrição dos relatos criados

    @classmethod
    def do_ambiente(cls) -> "Config":
        carregar_dotenv(RAIZ / ".env")
        return cls(
            mvp_url=os.getenv("MVP_URL", cls.mvp_url).rstrip("/"),
            mvp_admin_token=os.getenv("MVP_ADMIN_TOKEN", ""),
            open_meteo_url=os.getenv("OPEN_METEO_URL", cls.open_meteo_url),
            overpass_url=os.getenv("OVERPASS_URL", cls.overpass_url),
            ntfy_url=os.getenv("NTFY_URL", ""),
            dir_entrada=_caminho("ROBO_DIR_ENTRADA", "entrada"),
            dir_saida=_caminho("ROBO_DIR_SAIDA", "saida"),
            dir_logs=_caminho("ROBO_DIR_LOGS", "logs"),
            estado_db=_caminho("ROBO_ESTADO_DB", "saida/estado.db"),
            http_timeout=float(os.getenv("HTTP_TIMEOUT", "15")),
            http_tentativas=int(os.getenv("HTTP_TENTATIVAS", "3")),
            http_backoff=float(os.getenv("HTTP_BACKOFF", "2")),
            marcar_demonstracao=os.getenv("ROBO_DEMONSTRACAO", "").lower() in ("1", "true", "sim"),
        )
