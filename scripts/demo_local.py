"""Demonstração local de ponta a ponta, sem tocar a produção.

Sobe um MVP falso (mesmas rotas e regras do Caminho Livre) iniciado com uma cópia pública dos relatos
de produção (exemplos/mvp-producao-copia.json) e com os locais sensíveis reais do OpenStreetMap
(dados/pois-snapshot.json). Copia as planilhas de exemplo para uma pasta temporária e roda o robô em
3 cenários: sucesso + decisão, reexecução idempotente e MVP fora do ar.

A previsão de chuva é simulada com 8,4 mm em 24 h, o valor real da execução agendada de 16/11/2026.

    python scripts/demo_local.py            # roda as 3 cenas direto
    python scripts/demo_local.py --pausar   # espera Enter antes de cada cena (para gravar o vídeo)
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(RAIZ / "src"), str(RAIZ / "tests")]

from mvp_falso import TOKEN, MvpFalso  # noqa: E402
from robo.config import Config  # noqa: E402
from robo.robo import executar  # noqa: E402

CHUVA_16_11_MM = 8.4
OSM_POR_TIPO = {"hospital": ("amenity", "hospital"), "clínica": ("amenity", "clinic"), "escola": ("amenity", "school"),
                "equipamento de assistência social": ("amenity", "social_facility"),
                "acesso ao metrô": ("railway", "subway_entrance")}


def preparar_mvp(mvp: MvpFalso) -> str:
    copia = json.loads((RAIZ / "exemplos" / "mvp-producao-copia.json").read_text(encoding="utf-8"))
    mvp.relatos.extend(copia["itens"])
    snapshot = json.loads((RAIZ / "dados" / "pois-snapshot.json").read_text(encoding="utf-8"))
    mvp.pois = []
    for p in snapshot["pois"]:
        tipo, num = p["osm_id"].split("/")
        chave, valor = OSM_POR_TIPO[p["tipo"]]
        mvp.pois.append({"type": tipo, "id": int(num), "lat": p["lat"], "lon": p["lon"],
                         "tags": {chave: valor, "name": p["nome"]}})
    mvp.chuva_por_hora = CHUVA_16_11_MM / 24
    return (f"MVP local iniciado com a cópia dos {len(copia['itens'])} relatos de produção "
            f"(copiados em {copia['copiado_em'][:10]}) e {len(mvp.pois)} locais sensíveis do OpenStreetMap.\n"
            f"Previsão de chuva simulada: {CHUVA_16_11_MM} mm em 24 h, a mesma da execução real de 16/11.")


PAUSAR = "--pausar" in sys.argv


def titulo(texto: str) -> None:
    if PAUSAR:
        input(f"\n>>> Enter para a cena: {texto} ")
    print(f"\n{'=' * 78}\n  {texto}\n{'=' * 78}")


def main() -> int:
    mvp = MvpFalso()
    descricao = preparar_mvp(mvp)
    tmp = Path(tempfile.mkdtemp(prefix="demo-robo-"))
    entrada = tmp / "entrada"
    entrada.mkdir()
    for arq in (RAIZ / "exemplos").glob("*.csv"):
        shutil.copy(arq, entrada)
    cfg = Config(mvp_url=mvp.url, mvp_admin_token=TOKEN, open_meteo_url=mvp.url + "/meteo",
                 overpass_url=mvp.url + "/overpass", dir_entrada=entrada, dir_saida=tmp / "saida",
                 dir_logs=tmp / "logs", estado_db=tmp / "saida" / "estado.db", http_backoff=0.5)
    try:
        titulo("1/3  Execução normal: importa o lote, decide e prioriza")
        print(descricao + "\n")
        executar("executar", cfg=cfg)
        print(f"\nRelatos no MVP: {len(mvp.relatos)} | movidos para em análise: {len(mvp.patches)}")

        titulo("2/3  Mesma planilha enviada de novo: nada é duplicado")
        for arq in (RAIZ / "exemplos").glob("relatos-*.csv"):
            shutil.copy(arq, entrada / ("reenvio-" + arq.name))
        executar("importar", cfg=cfg)
        print(f"\nRelatos no MVP: {len(mvp.relatos)} (igual ao anterior)")

        titulo("3/3  MVP fora do ar: 3 tentativas, FALHA registrada, nada se perde")
        mvp.fora.update({"/health", "/api/reports"})
        codigo, _ = executar("executar", cfg=cfg)
        print(f"\nCódigo de saída: {codigo} (1 = falha, o agendador marca a execução como vermelha)")
        print(f"\nArquivos gerados em: {tmp}")
    finally:
        mvp.parar()
    return 0


if __name__ == "__main__":
    sys.exit(main())
