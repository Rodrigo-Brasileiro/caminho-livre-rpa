"""Etapa 1: importação em lote dos relatos que chegam por outros canais.

Hoje um atendente redigita no mapa, um por um, os relatos que chegam por planilha (central 156,
vistorias de agentes, ONGs parceiras). O robô lê cada arquivo da pasta entrada/, valida e normaliza
cada linha, remove dado pessoal, descarta duplicatas e cria os relatos pela API do MVP.
"""
import csv
import io
import shutil
from datetime import datetime, timezone
from pathlib import Path

from . import regras
from .contexto import Execucao
from .http_cliente import ErroHttp, ServicoIndisponivel

ETAPA = "importacao"
COLUNAS_OBRIGATORIAS = {"latitude", "longitude", "categoria"}
EXTENSOES = {".csv"}
MAX_LINHAS = 5000


class ArquivoInvalido(Exception):
    pass


def ler_csv(caminho: Path) -> list[dict]:
    bruto = caminho.read_bytes()
    if not bruto.strip():
        raise ArquivoInvalido("arquivo vazio")
    try:
        texto = bruto.decode("utf-8-sig")
    except UnicodeDecodeError:
        texto = bruto.decode("cp1252")  # planilha salva pelo Excel em português
    if "\x00" in texto:
        raise ArquivoInvalido("conteúdo binário, não é CSV")
    try:
        dialeto = csv.Sniffer().sniff(texto.splitlines()[0], delimiters=";,\t")
    except csv.Error:
        raise ArquivoInvalido("não foi possível identificar o separador do CSV") from None
    leitor = csv.DictReader(io.StringIO(texto), dialect=dialeto)
    colunas = {regras._slug(c or "") for c in (leitor.fieldnames or [])}
    faltando = COLUNAS_OBRIGATORIAS - colunas
    if faltando:
        raise ArquivoInvalido(f"colunas obrigatórias ausentes: {', '.join(sorted(faltando))}")
    linhas = [{regras._slug(k or ""): (v or "").strip() for k, v in linha.items() if k} for linha in leitor]
    linhas = [linha for linha in linhas if any(linha.values())]
    if not linhas:
        raise ArquivoInvalido("sem linhas de dados")
    if len(linhas) > MAX_LINHAS:
        raise ArquivoInvalido(f"mais de {MAX_LINHAS} linhas; divida o lote")
    return linhas


def _carimbo() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _mover(ex: Execucao, arquivo: Path, destino: str, motivo: str | None = None) -> None:
    if ex.simulacao:
        return
    pasta = ex.cfg.dir_entrada / destino
    pasta.mkdir(parents=True, exist_ok=True)
    alvo = pasta / f"{_carimbo()}-{arquivo.name}"
    shutil.move(str(arquivo), alvo)
    if motivo:
        alvo.with_name(alvo.name + ".motivo.txt").write_text(motivo + "\n", encoding="utf-8")


def _arquivos(pasta: Path) -> list[Path]:
    if not pasta.is_dir():
        return []
    return sorted(p for p in pasta.iterdir() if p.is_file() and not p.name.startswith("."))


def importar(ex: Execucao) -> None:
    aud, mvp, estado = ex.aud, ex.mvp, ex.estado
    if not ex.cfg.dir_entrada.is_dir():
        aud.alerta(ETAPA, "pasta_ausente", f"pasta de entrada não existe: {ex.cfg.dir_entrada.name}/")
        return
    arquivos = _arquivos(ex.cfg.dir_entrada)
    aud.info(ETAPA, "inicio", f"{len(arquivos)} arquivo(s) na caixa de entrada", arquivos=len(arquivos))
    if not arquivos:
        return

    # Relatos já existentes no MVP: base para não criar duplicata
    existentes = mvp.listar()

    for arquivo in arquivos:  # REPETIÇÃO 1: cada arquivo do lote
        aud.contar("arquivos_lidos")
        if arquivo.suffix.lower() not in EXTENSOES:
            motivo = f"formato não suportado ({arquivo.suffix or 'sem extensão'}); envie CSV"
            aud.alerta(ETAPA, "arquivo_quarentena", motivo, arquivo=arquivo.name)
            aud.contar("arquivos_quarentena")
            _mover(ex, arquivo, "quarentena", motivo)
            continue
        try:
            linhas = ler_csv(arquivo)
        except (ArquivoInvalido, OSError) as erro:
            aud.alerta(ETAPA, "arquivo_quarentena", str(erro), arquivo=arquivo.name)
            aud.contar("arquivos_quarentena")
            _mover(ex, arquivo, "quarentena", str(erro))
            continue

        aud.info(ETAPA, "arquivo_aberto", f"{len(linhas)} linha(s)", arquivo=arquivo.name, linhas=len(linhas))
        interrompido = False
        for numero, linha in enumerate(linhas, start=2):  # REPETIÇÃO 2: cada linha do arquivo
            aud.contar("linhas_lidas")
            v = regras.validar_linha(linha, ex.bbox)
            ref = {"arquivo": arquivo.name, "linha": numero}

            if not v.valida:  # DECISÃO: linha inválida não vai para o MVP
                aud.alerta(ETAPA, "linha_rejeitada", v.motivo, **ref)
                aud.contar("linhas_rejeitadas")
                continue
            if v.pii_removida:
                aud.info(ETAPA, "dado_pessoal_removido", "contato/telefone/e-mail descartado (LGPD)", **ref)
                aud.contar("linhas_com_dado_pessoal_removido")
            anterior = estado.ja_processada(v.hash)
            if anterior:  # IDEMPOTÊNCIA: rodar de novo não duplica
                aud.info(ETAPA, "linha_ja_processada", f"resultado anterior: {anterior}", **ref)
                aud.contar("linhas_ja_processadas")
                continue
            duplicata = regras.procurar_duplicata(v.latitude, v.longitude, v.categoria, existentes)
            if duplicata:  # DECISÃO: já existe relato aberto no mesmo ponto
                aud.info(ETAPA, "linha_duplicada", f"já existe relato aberto a até {regras.RAIO_DUPLICATA_M} m",
                         relato_existente=duplicata["id"], **ref)
                aud.contar("linhas_duplicadas")
                if not ex.simulacao:
                    estado.marcar_linha(v.hash, "duplicata", arquivo.name, duplicata["id"])
                continue
            if ex.simulacao:
                aud.info(ETAPA, "linha_simulada", "seria enviada ao MVP", categoria=v.categoria, **ref)
                aud.contar("linhas_simuladas")
                existentes.append({"id": f"simulado-{numero}", "latitude": v.latitude, "longitude": v.longitude,
                                   "categoria": v.categoria, "status": "pendente"})
                continue
            try:
                criado = mvp.criar(v.latitude, v.longitude, v.categoria, v.descricao)
            except ErroHttp as erro:  # 4xx: o MVP recusou o conteúdo
                aud.alerta(ETAPA, "linha_recusada_pelo_mvp", f"HTTP {erro.status}: {erro.detalhe}", **ref)
                aud.contar("linhas_recusadas_mvp")
                estado.marcar_linha(v.hash, "rejeitado", arquivo.name)
                continue
            except ServicoIndisponivel as erro:  # MVP caiu no meio do lote: para e tenta na próxima execução
                aud.erro(ETAPA, "mvp_indisponivel", str(erro), **ref)
                aud.contar("linhas_pendentes_reenvio")
                interrompido = True
                break
            estado.marcar_linha(v.hash, "enviado", arquivo.name, criado["id"])
            existentes.append({"id": criado["id"], "latitude": v.latitude, "longitude": v.longitude,
                               "categoria": v.categoria, "status": "pendente"})
            aud.info(ETAPA, "relato_criado", "", relato_id=criado["id"], categoria=v.categoria, **ref)
            aud.contar("relatos_criados")

        if interrompido:
            # As linhas já enviadas ficaram marcadas no estado; na próxima execução só o resto é enviado.
            aud.alerta(ETAPA, "arquivo_mantido", "arquivo fica na entrada para nova tentativa", arquivo=arquivo.name)
            raise ServicoIndisponivel(ex.cfg.mvp_url, ex.cfg.http_tentativas, "MVP caiu durante a importação")
        _mover(ex, arquivo, "processados")
        aud.contar("arquivos_processados")
        aud.info(ETAPA, "arquivo_concluido",
                 "simulação: arquivo fica na entrada" if ex.simulacao else "movido para processados/",
                 arquivo=arquivo.name)
