"""Servidor falso que imita a API do Caminho Livre, o Open-Meteo e o Overpass.

Mesmas rotas, regras de território e token do MVP real (app/main.py do repositório caminho-livre),
mas em memória e com falhas programáveis: "derrubar" uma rota, devolver 503 N vezes, etc.
"""
import json
import threading
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

BBOX = [-23.5700, -46.6600, -23.5590, -46.6450]
TOKEN = "token-de-teste"


def iso(dias_atras: float = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=dias_atras)).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class MvpFalso:
    def __init__(self):
        self.relatos: list[dict] = []
        self.falhas: dict[str, list[int]] = {}   # rota -> fila de status a devolver antes de responder normal
        self.fora: set[str] = set()              # rotas que sempre devolvem 503
        self.chuva_por_hora = 0.0
        self.pois = [{"type": "node", "id": 1, "lat": -23.5657, "lon": -46.6494,
                      "tags": {"amenity": "hospital", "name": "Hospital Teste"}}]
        self.criados_via_post: list[dict] = []
        self.patches: list[tuple] = []
        self.servidor = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.url = f"http://127.0.0.1:{self.servidor.server_port}"
        threading.Thread(target=self.servidor.serve_forever, daemon=True).start()

    def parar(self):
        self.servidor.shutdown()
        self.servidor.server_close()

    def adicionar(self, lat, lon, categoria, status="pendente", dias_atras=0.0, fonte="cidadao") -> dict:
        r = {"id": str(uuid.uuid4()), "criado_em": iso(dias_atras), "atualizado_em": iso(dias_atras),
             "latitude": lat, "longitude": lon, "categoria": categoria, "descricao_sanitizada": None,
             "status": status, "fonte": fonte}
        self.relatos.append(r)
        return r

    def _handler(self):
        mvp = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _responder(self, status, corpo=None):
                dados = json.dumps(corpo if corpo is not None else {}).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(dados)))
                self.end_headers()
                self.wfile.write(dados)

            def _falha_programada(self, rota) -> bool:
                if rota in mvp.fora:
                    self._responder(503, {"detail": "fora do ar"})
                    return True
                fila = mvp.falhas.get(rota)
                if fila:
                    self._responder(fila.pop(0), {"detail": "falha programada"})
                    return True
                return False

            def _corpo(self):
                n = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(n) if n else b""

            def do_GET(self):
                u = urlparse(self.path)
                if self._falha_programada(u.path):
                    return
                if u.path == "/health":
                    return self._responder(200, {"status": "ok", "version": "teste123"})
                if u.path == "/api/config":
                    return self._responder(200, {"bbox": BBOX, "version": "teste123"})
                if u.path == "/api/reports":
                    status = parse_qs(u.query).get("status", [None])[0]
                    itens = [dict(r) for r in mvp.relatos if not status or r["status"] == status]
                    return self._responder(200, {"atualizado_em": iso(), "itens": itens})
                if u.path == "/meteo":
                    return self._responder(200, {"hourly": {"precipitation": [mvp.chuva_por_hora] * 48}})
                self._responder(404, {"detail": "Not Found"})

            def do_POST(self):
                u = urlparse(self.path)
                corpo = self._corpo()
                if self._falha_programada(u.path):
                    return
                if u.path == "/overpass":
                    return self._responder(200, {"elements": mvp.pois})
                if u.path == "/api/reports":
                    dados = json.loads(corpo)
                    if not (BBOX[0] <= dados["latitude"] <= BBOX[2] and BBOX[1] <= dados["longitude"] <= BBOX[3]):
                        return self._responder(422, {"detail": "localizacao fora do territorio do piloto"})
                    r = mvp.adicionar(round(dados["latitude"], 4), round(dados["longitude"], 4), dados["categoria"])
                    r["descricao_sanitizada"] = dados.get("descricao")
                    mvp.criados_via_post.append(dados)
                    return self._responder(201, {"id": r["id"], "status": "pendente"})
                self._responder(404, {"detail": "Not Found"})

            def do_PATCH(self):
                u = urlparse(self.path)
                corpo = self._corpo()
                if self._falha_programada(u.path):
                    return
                if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                    return self._responder(401, {"detail": "nao autorizado"})
                rid = u.path.rsplit("/", 1)[-1]
                for r in mvp.relatos:
                    if r["id"] == rid:
                        r["status"] = json.loads(corpo)["status"]
                        r["atualizado_em"] = iso()
                        mvp.patches.append((rid, r["status"]))
                        return self._responder(200, {"id": rid, "status": r["status"]})
                self._responder(404, {"detail": "relato nao encontrado"})

        return Handler
