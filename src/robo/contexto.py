from dataclasses import dataclass

from .auditoria import Auditoria
from .config import Config
from .estado import Estado
from .fontes import MvpApi
from .http_cliente import ClienteHttp


@dataclass
class Execucao:
    cfg: Config
    http: ClienteHttp
    mvp: MvpApi
    estado: Estado
    aud: Auditoria
    simulacao: bool = False
    bbox: tuple = ()
