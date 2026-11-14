"""Conectores com os sistemas externos: API do MVP, Open-Meteo, OpenStreetMap (Overpass) e ntfy."""
from .config import BBOX_PADRAO, Config
from .http_cliente import ClienteHttp, ErroHttp, ServicoIndisponivel


class MvpApi:
    """API do Caminho Livre (FastAPI no Azure App Service)."""

    def __init__(self, cfg: Config, http: ClienteHttp):
        self.base, self.token, self.http = cfg.mvp_url, cfg.mvp_admin_token, http

    def saude(self) -> dict:
        return self.http.requisitar("GET", f"{self.base}/health")

    def territorio(self) -> tuple:
        try:
            bbox = self.http.requisitar("GET", f"{self.base}/api/config")["bbox"]
            return tuple(float(v) for v in bbox)
        except (ErroHttp, ServicoIndisponivel, KeyError, TypeError, ValueError):
            return BBOX_PADRAO

    def listar(self, status: str | None = None) -> list[dict]:
        params = {"status": status} if status else None
        return self.http.requisitar("GET", f"{self.base}/api/reports", params=params)["itens"]

    def criar(self, latitude: float, longitude: float, categoria: str, descricao: str | None) -> dict:
        corpo = {"latitude": latitude, "longitude": longitude, "categoria": categoria}
        if descricao:
            corpo["descricao"] = descricao
        return self.http.requisitar("POST", f"{self.base}/api/reports", corpo_json=corpo)

    def mudar_status(self, relato_id: str, status: str) -> dict:
        return self.http.requisitar("PATCH", f"{self.base}/api/admin/reports/{relato_id}",
                                    corpo_json={"status": status}, token=self.token)


def chuva_prevista_mm(cfg: Config, http: ClienteHttp, lat: float, lon: float) -> float:
    """Soma da precipitação prevista nas próximas 24 h (Open-Meteo, sem chave de API)."""
    dados = http.requisitar("GET", cfg.open_meteo_url, params={
        "latitude": lat, "longitude": lon, "hourly": "precipitation",
        "forecast_days": 2, "timezone": "America/Sao_Paulo"})
    valores = [v for v in dados["hourly"]["precipitation"][:24] if v is not None]
    return round(sum(valores), 1)


TIPOS_POI = {"hospital": "hospital", "clinic": "clínica", "school": "escola",
             "social_facility": "equipamento de assistência social", "subway_entrance": "acesso ao metrô"}


def pois_sensiveis(cfg: Config, http: ClienteHttp, bbox: tuple) -> list[dict]:
    """Locais onde uma barreira pesa mais: hospitais, clínicas, escolas, assistência social e metrô (OSM).

    O Overpass é um serviço público e instável (502/504 são comuns). Tenta cada espelho de
    cfg.overpass_url (separados por vírgula), com timeout maior, antes de desistir.
    """
    s, w, n, e = bbox
    margem = 0.002  # ~200 m para pegar POIs na borda do território
    caixa = f"{s - margem},{w - margem},{n + margem},{e + margem}"
    consulta = (f'[out:json][timeout:60];('
                f'nwr["amenity"~"^(hospital|clinic|school|social_facility)$"]({caixa});'
                f'node["railway"="subway_entrance"]({caixa}););out center tags;')
    lento = ClienteHttp(max(cfg.http_timeout, 75), cfg.http_tentativas, cfg.http_backoff, http.ao_tentar_de_novo)
    ultimo_erro: Exception | None = None
    for url in [u.strip() for u in cfg.overpass_url.split(",") if u.strip()]:
        try:
            dados = lento.requisitar("POST", url, corpo_form={"data": consulta})
            break
        except (ServicoIndisponivel, ErroHttp) as erro:
            ultimo_erro = erro
    else:
        raise ultimo_erro or ServicoIndisponivel(cfg.overpass_url, 0, "nenhum endereço configurado")
    return converter_overpass(dados)


def converter_overpass(dados: dict) -> list[dict]:
    pois = []
    for el in dados.get("elements", []):
        tags = el.get("tags", {})
        lat = el.get("lat", el.get("center", {}).get("lat"))
        lon = el.get("lon", el.get("center", {}).get("lon"))
        chave = tags.get("amenity") or tags.get("railway")
        if lat is None or lon is None or chave not in TIPOS_POI:
            continue
        pois.append({"lat": lat, "lon": lon, "tipo": TIPOS_POI[chave],
                     "nome": tags.get("name", "sem nome"), "osm_id": f"{el['type']}/{el['id']}"})
    return pois


def notificar(cfg: Config, http: ClienteHttp, titulo: str, mensagem: str, prioridade: str = "default") -> bool:
    """Push para o gestor via ntfy.sh (o mesmo canal dos alertas de DevOps). Opcional."""
    if not cfg.ntfy_url:
        return False
    import urllib.request
    pedido = urllib.request.Request(cfg.ntfy_url, data=mensagem.encode(), method="POST",
                                    headers={"Title": titulo.encode("ascii", "ignore").decode(),
                                             "Priority": prioridade, "Tags": "wheelchair"})
    try:
        with urllib.request.urlopen(pedido, timeout=cfg.http_timeout):
            return True
    except OSError:
        return False
