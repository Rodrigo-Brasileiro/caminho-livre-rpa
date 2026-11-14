"""Regras de negócio do robô (funções puras, sem rede nem disco, fáceis de testar).

1. Validação e normalização das linhas recebidas por outros canais.
2. Detecção de duplicatas por proximidade.
3. Cálculo de prioridade explicável (score + motivos) para a fila do gestor.
"""
import hashlib
import math
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import lgpd

CATEGORIAS = ("calcada_obstruida", "rampa_bloqueada", "travessia_barreira")
STATUS_ABERTOS = ("pendente", "em_analise")

# Sinônimos que aparecem nas planilhas da central 156, vistorias e parceiros
SINONIMOS = {
    "calcada_obstruida": "calcada_obstruida", "calcada": "calcada_obstruida", "calcada_obstruida_": "calcada_obstruida",
    "obstrucao": "calcada_obstruida", "obstrucao_na_calcada": "calcada_obstruida", "passeio_obstruido": "calcada_obstruida",
    "rampa_bloqueada": "rampa_bloqueada", "rampa": "rampa_bloqueada", "rampa_obstruida": "rampa_bloqueada",
    "rampa_de_acessibilidade_bloqueada": "rampa_bloqueada", "guia_rebaixada_bloqueada": "rampa_bloqueada",
    "travessia_barreira": "travessia_barreira", "travessia": "travessia_barreira",
    "travessia_com_barreira": "travessia_barreira", "faixa_sem_rebaixamento": "travessia_barreira",
}

RAIO_DUPLICATA_M = 15        # coordenadas do MVP são arredondadas a ~11 m
RAIO_RECORRENCIA_M = 30
LIMITE_DESCRICAO = 280

PESO_CATEGORIA = {"rampa_bloqueada": 40, "travessia_barreira": 35, "calcada_obstruida": 25}
PONTOS_POR_DIA = 5
TETO_IDADE = 30
BONUS_POI_100M = 20
BONUS_POI_200M = 10
BONUS_CHUVA = 10
CHUVA_RELEVANTE_MM = 5.0
BONUS_RECORRENCIA = 10
TETO_RECORRENCIA = 20
CORTE_ALTA = 70
CORTE_MEDIA = 45
PRAZO_DIAS = {"ALTA": 2, "MEDIA": 7, "BAIXA": 15}


# ---------- 1. validação ----------

def _slug(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", sem_acento.lower()).strip("_")


def normalizar_categoria(valor: str | None) -> str | None:
    return SINONIMOS.get(_slug(valor or ""))


def ler_coordenada(valor: str | None) -> float | None:
    """Aceita '-23.5646' e o formato de planilha brasileira '-23,5646'."""
    if valor is None:
        return None
    texto = str(valor).strip().replace(",", ".")
    try:
        numero = float(texto)
    except ValueError:
        return None
    return numero if math.isfinite(numero) else None


@dataclass
class LinhaValidada:
    valida: bool
    motivo: str = ""
    latitude: float | None = None
    longitude: float | None = None
    categoria: str | None = None
    descricao: str | None = None
    canal: str = "nao_informado"
    pii_removida: bool = False
    hash: str = ""


def impressao_digital(latitude: float, longitude: float, categoria: str, descricao: str | None) -> str:
    """Hash da linha já normalizada e mascarada: identifica a linha sem guardar dado pessoal."""
    base = f"{latitude:.4f}|{longitude:.4f}|{categoria}|{(descricao or '').lower()}"
    return hashlib.sha256(base.encode()).hexdigest()[:24]


def validar_linha(linha: dict, bbox: tuple) -> LinhaValidada:
    lat = ler_coordenada(linha.get("latitude"))
    lon = ler_coordenada(linha.get("longitude"))
    if lat is None or lon is None:
        return LinhaValidada(False, "coordenada ausente ou não numérica")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return LinhaValidada(False, "coordenada impossível")
    min_lat, min_lon, max_lat, max_lon = bbox
    if not (min_lat <= lat <= max_lat and min_lon <= lon <= max_lon):
        return LinhaValidada(False, "fora do território do piloto")
    categoria = normalizar_categoria(linha.get("categoria"))
    if not categoria:
        return LinhaValidada(False, "categoria desconhecida")

    original = (linha.get("descricao") or "").strip()
    descricao = lgpd.mascarar(original) or None
    canal = _slug(linha.get("canal") or "") or "nao_informado"
    if descricao:
        descricao = f"{descricao} (via {canal})"[:LIMITE_DESCRICAO]
    lat, lon = round(lat, 4), round(lon, 4)  # mesma precisão que o MVP grava (~11 m)
    return LinhaValidada(
        True, latitude=lat, longitude=lon, categoria=categoria, descricao=descricao, canal=canal,
        pii_removida="[removido]" in (descricao or "") or bool((linha.get("contato") or "").strip()),
        hash=impressao_digital(lat, lon, categoria, descricao),
    )


# ---------- 2. duplicatas ----------

def distancia_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def procurar_duplicata(lat: float, lon: float, categoria: str, existentes: list[dict]) -> dict | None:
    """Relato aberto da mesma categoria a até RAIO_DUPLICATA_M metros."""
    for relato in existentes:
        if relato.get("categoria") != categoria or relato.get("status") not in STATUS_ABERTOS:
            continue
        if distancia_m(lat, lon, relato["latitude"], relato["longitude"]) <= RAIO_DUPLICATA_M:
            return relato
    return None


# ---------- 3. prioridade ----------

@dataclass
class Prioridade:
    score: int
    nivel: str
    prazo_dias: int
    motivos: list[str] = field(default_factory=list)


def _idade_dias(criado_em: str, agora: datetime) -> float:
    criado = datetime.fromisoformat(criado_em.replace("Z", "+00:00"))
    return max(0.0, (agora - criado).total_seconds() / 86400)


def calcular_prioridade(relato: dict, *, agora: datetime | None = None, poi_proximo: dict | None = None,
                        chuva_mm: float | None = None, recorrencias: int = 0) -> Prioridade:
    """Score explicável. Cada ponto somado vira um motivo legível para o gestor."""
    agora = agora or datetime.now(timezone.utc)
    motivos = []
    score = PESO_CATEGORIA.get(relato["categoria"], 20)
    motivos.append(f"categoria {relato['categoria']} (+{score})")

    dias = _idade_dias(relato["criado_em"], agora)
    pontos_idade = min(TETO_IDADE, int(dias) * PONTOS_POR_DIA)
    if pontos_idade:
        score += pontos_idade
        motivos.append(f"aberto há {int(dias)} dia(s) (+{pontos_idade})")

    if poi_proximo:
        d = poi_proximo["distancia_m"]
        bonus = BONUS_POI_100M if d <= 100 else BONUS_POI_200M if d <= 200 else 0
        if bonus:
            score += bonus
            motivos.append(f"a {int(d)} m de {poi_proximo['tipo']} '{poi_proximo['nome']}' (+{bonus})")

    if chuva_mm is not None and chuva_mm >= CHUVA_RELEVANTE_MM:
        score += BONUS_CHUVA
        motivos.append(f"chuva prevista de {chuva_mm:.1f} mm em 24 h (+{BONUS_CHUVA})")

    if recorrencias:
        bonus = min(TETO_RECORRENCIA, recorrencias * BONUS_RECORRENCIA)
        score += bonus
        motivos.append(f"{recorrencias} outro(s) relato(s) a até {RAIO_RECORRENCIA_M} m (+{bonus})")

    nivel = "ALTA" if score >= CORTE_ALTA else "MEDIA" if score >= CORTE_MEDIA else "BAIXA"
    return Prioridade(score, nivel, PRAZO_DIAS[nivel], motivos)


def poi_mais_proximo(lat: float, lon: float, pois: list[dict]) -> dict | None:
    melhor = None
    for poi in pois:
        d = distancia_m(lat, lon, poi["lat"], poi["lon"])
        if melhor is None or d < melhor["distancia_m"]:
            melhor = {**poi, "distancia_m": round(d, 1)}
    return melhor


def contar_recorrencias(relato: dict, todos: list[dict]) -> int:
    return sum(
        1 for outro in todos
        if outro["id"] != relato["id"] and outro.get("status") in STATUS_ABERTOS
        and distancia_m(relato["latitude"], relato["longitude"], outro["latitude"], outro["longitude"]) <= RAIO_RECORRENCIA_M
    )
