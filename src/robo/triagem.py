"""Etapa 2: triagem e priorização da fila do gestor + indicadores de impacto.

Antes do robô, o gestor abria o mapa, olhava relato por relato e decidia de cabeça o que atacar
primeiro. Agora o robô cruza cada relato aberto com dados públicos (OpenStreetMap e Open-Meteo),
calcula uma prioridade explicável e põe os casos de prioridade ALTA "em análise" no MVP.

Pessoa no circuito: o robô nunca marca "resolvido" nem "descartado". Essas decisões são do gestor.
"""
import csv
import json
from datetime import datetime, timezone

from . import fontes, regras
from .auditoria import agora_iso
from .contexto import Execucao
from .http_cliente import ErroHttp, ServicoIndisponivel

ETAPA = "triagem"
CHAVE_CACHE_POIS = "pois_sensiveis"


class TokenInvalido(Exception):
    pass


def _centro(bbox: tuple) -> tuple[float, float]:
    return round((bbox[0] + bbox[2]) / 2, 4), round((bbox[1] + bbox[3]) / 2, 4)


def _chuva(ex: Execucao, indisponiveis: list) -> float | None:
    lat, lon = _centro(ex.bbox)
    try:
        mm = fontes.chuva_prevista_mm(ex.cfg, ex.http, lat, lon)
        ex.aud.info(ETAPA, "chuva_consultada", f"{mm} mm previstos em 24 h", fonte="open-meteo")
        return mm
    except (ServicoIndisponivel, ErroHttp, KeyError, TypeError) as erro:
        # Degrada com elegância: segue sem o fator chuva e avisa
        ex.aud.alerta(ETAPA, "fator_indisponivel", f"Open-Meteo: {erro}; prioridade calculada sem chuva")
        indisponiveis.append("chuva")
        return None


def _pois(ex: Execucao, indisponiveis: list) -> list[dict]:
    pois, idade = ex.estado.ler_cache(CHAVE_CACHE_POIS, ex.cfg.cache_pois_horas)
    if pois is not None:
        ex.aud.info(ETAPA, "pois_do_cache", f"{len(pois)} locais sensíveis (cache de {idade:.1f} h)")
        return pois
    try:
        pois = fontes.pois_sensiveis(ex.cfg, ex.http, ex.bbox)
        ex.estado.gravar_cache(CHAVE_CACHE_POIS, pois)
        ex.aud.info(ETAPA, "pois_consultados", f"{len(pois)} locais sensíveis", fonte="openstreetmap")
        return pois
    except (ServicoIndisponivel, ErroHttp, KeyError, TypeError) as erro:
        antigo, idade = ex.estado.ler_cache(CHAVE_CACHE_POIS)
        if antigo is not None:
            ex.aud.alerta(ETAPA, "pois_cache_vencido", f"Overpass fora ({erro}); usando cache de {idade:.0f} h")
            return antigo
        snapshot = _snapshot(ex)
        if snapshot:
            ex.aud.alerta(ETAPA, "pois_snapshot", f"Overpass fora ({erro}); usando snapshot versionado "
                                                  f"de {snapshot['gerado_em'][:10]} ({len(snapshot['pois'])} locais)")
            return snapshot["pois"]
        ex.aud.alerta(ETAPA, "fator_indisponivel", f"Overpass: {erro}; prioridade calculada sem locais sensíveis")
        indisponiveis.append("locais_sensiveis")
        return []


def _snapshot(ex: Execucao) -> dict | None:
    try:
        dados = json.loads(ex.cfg.pois_snapshot.read_text(encoding="utf-8"))
        return dados if dados.get("pois") else None
    except (OSError, json.JSONDecodeError):
        return None


def _mover_para_analise(ex: Execucao, relato: dict) -> str:
    if ex.simulacao:
        return "seria_movido_para_em_analise"
    if not ex.cfg.mvp_admin_token:
        return "sem_token_mantido_pendente"
    try:
        ex.mvp.mudar_status(relato["id"], "em_analise")
    except ErroHttp as erro:
        if erro.status in (401, 403):
            raise TokenInvalido("MVP recusou o token de gestor (HTTP %s)" % erro.status) from None
        ex.aud.alerta(ETAPA, "status_nao_alterado", f"HTTP {erro.status}: {erro.detalhe}", relato_id=relato["id"])
        ex.aud.contar("falhas_mudanca_status")
        return "falha_ao_mover"
    except ServicoIndisponivel as erro:
        ex.aud.erro(ETAPA, "status_nao_alterado", str(erro), relato_id=relato["id"])
        ex.aud.contar("falhas_mudanca_status")
        return "falha_ao_mover"
    ex.aud.info(ETAPA, "movido_para_em_analise", "prioridade ALTA entrou na fila do gestor", relato_id=relato["id"])
    ex.aud.contar("relatos_movidos_em_analise")
    return "movido_para_em_analise"


def triar(ex: Execucao) -> dict:
    aud = ex.aud
    todos = ex.mvp.listar()
    abertos = [r for r in todos if r.get("status") in regras.STATUS_ABERTOS]
    aud.info(ETAPA, "inicio", f"{len(abertos)} relato(s) aberto(s) de {len(todos)}", abertos=len(abertos))

    indisponiveis: list[str] = []
    chuva = _chuva(ex, indisponiveis) if abertos else None
    pois = _pois(ex, indisponiveis) if abertos else []
    if not ex.cfg.mvp_admin_token and not ex.simulacao and abertos:
        aud.alerta(ETAPA, "sem_token", "MVP_ADMIN_TOKEN não configurado: o robô só prioriza, não muda status")

    agora = datetime.now(timezone.utc)
    itens = []
    for relato in abertos:  # REPETIÇÃO: cada relato aberto
        aud.contar("relatos_avaliados")
        poi = regras.poi_mais_proximo(relato["latitude"], relato["longitude"], pois) if pois else None
        p = regras.calcular_prioridade(relato, agora=agora, poi_proximo=poi, chuva_mm=chuva,
                                       recorrencias=regras.contar_recorrencias(relato, abertos))
        aud.contar(f"prioridade_{p.nivel.lower()}")

        # DECISÃO: só prioridade ALTA ainda pendente sobe automaticamente para "em análise"
        if p.nivel == "ALTA" and relato["status"] == "pendente":
            acao = _mover_para_analise(ex, relato)
        else:
            acao = "mantido"
        aud.info(ETAPA, "relato_priorizado", f"{p.nivel} score={p.score} acao={acao}",
                 relato_id=relato["id"], categoria=relato["categoria"])
        itens.append({
            "id": relato["id"], "categoria": relato["categoria"], "fonte": relato.get("fonte"),
            "latitude": relato["latitude"], "longitude": relato["longitude"], "criado_em": relato["criado_em"],
            "status_antes": relato["status"],
            "status_depois": "em_analise" if acao == "movido_para_em_analise" else relato["status"],
            "prioridade": p.nivel, "score": p.score, "prazo_sugerido_dias": p.prazo_dias,
            "motivos": p.motivos, "acao_do_robo": acao,
            "local_sensivel_mais_proximo": poi and {k: poi[k] for k in ("tipo", "nome", "distancia_m", "osm_id")},
        })

    itens.sort(key=lambda i: (-i["score"], i["criado_em"]))
    fila = {
        "gerado_em": agora_iso(), "run_id": aud.run_id, "simulacao": ex.simulacao, "mvp": ex.cfg.mvp_url,
        "contexto": {"chuva_prevista_24h_mm": chuva, "locais_sensiveis_considerados": len(pois),
                     "fatores_indisponiveis": indisponiveis,
                     "regras": {"corte_alta": regras.CORTE_ALTA, "corte_media": regras.CORTE_MEDIA}},
        "itens": itens,
    }
    _gravar_fila(ex, fila)
    indicadores = calcular_indicadores(todos, agora)
    indicadores.update({"gerado_em": fila["gerado_em"], "run_id": aud.run_id})
    _gravar_json(ex, "indicadores.json", indicadores)

    altas = [i for i in itens if i["acao_do_robo"] == "movido_para_em_analise"]
    if altas:
        fontes.avisar_gestor(ex.cfg, ex.http, aud, ETAPA, "Caminho Livre: prioridade ALTA",
                             f"{len(altas)} relato(s) de prioridade ALTA entraram em análise. "
                             f"Prazo sugerido: {regras.PRAZO_DIAS['ALTA']} dias.", "high")
    return fila


def _gravar_json(ex: Execucao, nome: str, dados: dict) -> None:
    ex.cfg.dir_saida.mkdir(parents=True, exist_ok=True)
    caminho = ex.cfg.dir_saida / nome
    temporario = caminho.with_suffix(".tmp")
    temporario.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
    temporario.replace(caminho)  # escrita atômica: quem consome nunca lê arquivo pela metade


def _gravar_fila(ex: Execucao, fila: dict) -> None:
    _gravar_json(ex, "fila_priorizada.json", fila)
    with (ex.cfg.dir_saida / "fila_priorizada.csv").open("w", newline="", encoding="utf-8-sig") as f:
        escritor = csv.writer(f, delimiter=";")
        escritor.writerow(["prioridade", "score", "prazo_dias", "categoria", "status", "latitude", "longitude",
                           "criado_em", "motivos", "id"])
        for i in fila["itens"]:
            escritor.writerow([i["prioridade"], i["score"], i["prazo_sugerido_dias"], i["categoria"],
                               i["status_depois"], i["latitude"], i["longitude"], i["criado_em"],
                               " | ".join(i["motivos"]), i["id"]])
    ex.aud.info(ETAPA, "fila_gravada", f"{len(fila['itens'])} item(ns) em saida/fila_priorizada.json")


def _horas(inicio: str, fim: str) -> float:
    a = datetime.fromisoformat(inicio.replace("Z", "+00:00"))
    b = datetime.fromisoformat(fim.replace("Z", "+00:00"))
    return (b - a).total_seconds() / 3600


def calcular_indicadores(todos: list[dict], agora: datetime) -> dict:
    """Indicadores de impacto da ficha técnica. Exclui dados sintéticos de demonstração."""
    reais = [r for r in todos if r.get("fonte") != "sintetico"]
    por_status: dict = {}
    por_categoria: dict = {}
    for r in reais:
        por_status[r["status"]] = por_status.get(r["status"], 0) + 1
        por_categoria[r["categoria"]] = por_categoria.get(r["categoria"], 0) + 1
    revisados = [r for r in reais if r["status"] != "pendente"]
    validos = len(reais) - por_status.get("descartado", 0)
    tempos = [_horas(r["criado_em"], r["atualizado_em"]) for r in revisados]
    pendentes_atrasados = sum(
        1 for r in reais if r["status"] == "pendente"
        and _horas(r["criado_em"], agora.isoformat()) > 24 * regras.PRAZO_DIAS["MEDIA"])
    return {
        "relatos_recebidos": len(reais),
        "relatos_sinteticos_excluidos": len(todos) - len(reais),
        "relatos_revisados": len(revisados),
        "relatos_resolvidos": por_status.get("resolvido", 0),
        "taxa_resolucao": round(por_status.get("resolvido", 0) / validos, 3) if validos else None,
        "tempo_medio_ate_revisao_h": round(sum(tempos) / len(tempos), 1) if tempos else None,
        "pendentes_ha_mais_de_7_dias": pendentes_atrasados,
        "por_status": por_status, "por_categoria": por_categoria,
        "observacao": "Indicadores de processo. tempo_medio_ate_revisao_h usa a última atualização como aproximação.",
    }
