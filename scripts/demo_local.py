"""Demonstração local de ponta a ponta, sem tocar a produção.

Sobe um MVP falso (mesmas rotas e regras do Caminho Livre), copia o lote de exemplo para uma pasta
temporária e roda o robô em 3 cenários: sucesso + decisão, reexecução idempotente e MVP fora do ar.

    python scripts/demo_local.py
"""
import shutil
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(RAIZ / "src"), str(RAIZ / "tests")]

from mvp_falso import TOKEN, MvpFalso  # noqa: E402
from robo.config import Config  # noqa: E402
from robo.robo import executar  # noqa: E402


def titulo(texto: str) -> None:
    print(f"\n{'=' * 78}\n  {texto}\n{'=' * 78}")


def main() -> int:
    mvp = MvpFalso()
    tmp = Path(tempfile.mkdtemp(prefix="demo-robo-"))
    entrada = tmp / "entrada"
    entrada.mkdir()
    for arq in (RAIZ / "exemplos").glob("*.csv"):
        shutil.copy(arq, entrada)
    # Relato antigo perto do hospital Pró Matre: deve virar prioridade ALTA
    mvp.adicionar(-23.5658, -46.6495, "rampa_bloqueada", dias_atras=3)
    # Relato já aberto no mesmo ponto de uma linha do CSV: deve ser detectado como duplicata
    mvp.adicionar(-23.5661, -46.6497, "travessia_barreira")
    cfg = Config(mvp_url=mvp.url, mvp_admin_token=TOKEN, open_meteo_url=mvp.url + "/meteo",
                 overpass_url=mvp.url + "/overpass", dir_entrada=entrada, dir_saida=tmp / "saida",
                 dir_logs=tmp / "logs", estado_db=tmp / "saida" / "estado.db", http_backoff=0.5)
    try:
        titulo("1/3  Execução normal: importa o lote, decide e prioriza")
        executar("executar", cfg=cfg)
        print(f"\nRelatos no MVP: {len(mvp.relatos)} | mudanças de status: {mvp.patches}")

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
