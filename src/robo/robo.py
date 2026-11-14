"""Orquestrador: abre a execução, roda as etapas e fecha com status e resumo. Nunca falha em silêncio."""
import os
import time

from . import fontes
from .auditoria import Auditoria
from .config import Config
from .contexto import Execucao
from .estado import Estado
from .fontes import MvpApi
from .http_cliente import ClienteHttp, ErroHttp, ServicoIndisponivel
from .importacao import importar
from .triagem import TokenInvalido, triar

COMANDOS = ("executar", "importar", "triagem")
TRAVA_MAX_SEGUNDOS = 30 * 60


def _travar(cfg: Config) -> bool:
    """Impede duas execuções ao mesmo tempo (ex.: agendador disparou enquanto a anterior ainda roda)."""
    cfg.dir_saida.mkdir(parents=True, exist_ok=True)
    trava = cfg.dir_saida / ".robo.lock"
    if trava.exists() and time.time() - trava.stat().st_mtime > TRAVA_MAX_SEGUNDOS:
        trava.unlink(missing_ok=True)  # trava esquecida por uma execução que morreu
    try:
        fd = os.open(trava, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)
    return True


def _destravar(cfg: Config) -> None:
    (cfg.dir_saida / ".robo.lock").unlink(missing_ok=True)


def executar(comando: str = "executar", simulacao: bool = False, cfg: Config | None = None,
             eco: bool = True) -> tuple[int, dict]:
    cfg = cfg or Config.do_ambiente()
    aud = Auditoria(cfg.dir_logs, cfg.dir_saida, comando, simulacao, eco=eco)
    if not _travar(cfg):
        aud.alerta("execucao", "ignorada", "outra execução do robô está em andamento")
        return 0, aud.fechar()

    http = ClienteHttp(cfg.http_timeout, cfg.http_tentativas, cfg.http_backoff,
                       ao_tentar_de_novo=lambda url, n, causa: aud.alerta(
                           "http", "nova_tentativa", f"tentativa {n} falhou ({causa})", destino=url.split("?")[0]))
    estado = Estado(cfg.estado_db)
    mvp = MvpApi(cfg, http)
    ex = Execucao(cfg, http, mvp, estado, aud, simulacao)
    fatal = False
    aud.info("execucao", "inicio", f"comando={comando}", mvp=cfg.mvp_url, simulacao=simulacao)
    try:
        saude = mvp.saude()
        aud.info("execucao", "mvp_no_ar", f"versão {str(saude.get('version', '?'))[:7]}")
        ex.bbox = mvp.territorio()
        if comando in ("executar", "importar"):
            importar(ex)
        if comando in ("executar", "triagem"):
            triar(ex)
    except ServicoIndisponivel as erro:
        fatal = True
        aud.erro("execucao", "mvp_indisponivel", str(erro))
    except TokenInvalido as erro:
        fatal = True
        aud.erro("execucao", "token_invalido", str(erro))
    except ErroHttp as erro:
        fatal = True
        aud.erro("execucao", "resposta_inesperada", str(erro))
    except Exception as erro:  # noqa: BLE001 - qualquer erro vira registro e status FALHA, nunca silêncio
        fatal = True
        aud.erro("execucao", "erro_inesperado", f"{type(erro).__name__}: {erro}")
    finally:
        estado.fechar()
        _destravar(cfg)

    resumo = aud.fechar(fatal)
    if fatal:
        fontes.notificar(cfg, http, "Caminho Livre: robô falhou",
                         f"Execução {aud.run_id} terminou em FALHA: {aud.erros[-1] if aud.erros else ''}", "high")
    return (1 if fatal else 0), resumo
