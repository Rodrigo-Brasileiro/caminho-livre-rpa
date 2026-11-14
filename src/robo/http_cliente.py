"""Cliente HTTP com timeout, novas tentativas e backoff exponencial (só biblioteca padrão)."""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "caminho-livre-rpa/1.0 (+https://caminholivre-gs.azurewebsites.net)"


class ErroHttp(Exception):
    """Resposta 4xx: erro do pedido. Não adianta tentar de novo."""

    def __init__(self, status: int, url: str, detalhe: str = ""):
        self.status, self.url, self.detalhe = status, url, detalhe
        super().__init__(f"HTTP {status} em {_sem_query(url)}: {detalhe}"[:300])


class ServicoIndisponivel(Exception):
    """Falha de rede, timeout ou 5xx que persistiu depois de todas as tentativas."""

    def __init__(self, url: str, tentativas: int, causa: str):
        self.url, self.tentativas, self.causa = url, tentativas, causa
        super().__init__(f"{_sem_query(url)} indisponível após {tentativas} tentativa(s): {causa}"[:300])


def _sem_query(url: str) -> str:
    return url.split("?", 1)[0]


class ClienteHttp:
    def __init__(self, timeout: float = 15, tentativas: int = 3, backoff: float = 2, ao_tentar_de_novo=None):
        self.timeout, self.tentativas, self.backoff = timeout, max(1, tentativas), backoff
        self.ao_tentar_de_novo = ao_tentar_de_novo  # callback(url, tentativa, causa) para o log de auditoria

    def requisitar(self, metodo: str, url: str, *, params: dict | None = None, corpo_json=None,
                   corpo_form: dict | None = None, token: str | None = None):
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        dados = None
        if corpo_json is not None:
            dados = json.dumps(corpo_json).encode()
            headers["Content-Type"] = "application/json"
        elif corpo_form is not None:
            dados = urllib.parse.urlencode(corpo_form).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        if token:
            headers["Authorization"] = f"Bearer {token}"

        causa = ""
        for tentativa in range(1, self.tentativas + 1):
            pedido = urllib.request.Request(url, data=dados, headers=headers, method=metodo)
            try:
                with urllib.request.urlopen(pedido, timeout=self.timeout) as resp:
                    bruto = resp.read()
                    return json.loads(bruto) if bruto else None
            except urllib.error.HTTPError as erro:
                detalhe = _detalhe(erro)
                if erro.code < 500 and erro.code != 429:
                    raise ErroHttp(erro.code, url, detalhe) from None
                causa = f"HTTP {erro.code}"
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as erro:
                causa = type(erro).__name__ + ": " + str(getattr(erro, "reason", erro))
            except json.JSONDecodeError:
                causa = "resposta não é JSON válido"
            if tentativa < self.tentativas:
                if self.ao_tentar_de_novo:
                    self.ao_tentar_de_novo(url, tentativa, causa)
                time.sleep(self.backoff * (2 ** (tentativa - 1)))
        raise ServicoIndisponivel(url, self.tentativas, causa)


def _detalhe(erro: urllib.error.HTTPError) -> str:
    try:
        corpo = json.loads(erro.read() or b"{}")
        return str(corpo.get("detail", ""))
    except (json.JSONDecodeError, AttributeError, ValueError):
        return ""
