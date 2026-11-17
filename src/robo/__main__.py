"""Linha de comando: python rodar.py [executar|importar|triagem] [--simular] [--demonstracao]"""
import argparse
import json
import sys

from .config import Config
from .robo import COMANDOS, executar


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="robo", description="Robô de importação e triagem do Caminho Livre")
    parser.add_argument("comando", nargs="?", default="executar", choices=COMANDOS,
                        help="executar = importar + triagem (padrão)")
    parser.add_argument("--simular", action="store_true", help="não altera nada no MVP nem move arquivos")
    parser.add_argument("--demonstracao", action="store_true",
                        help="marca os relatos criados com [demonstração] (para planilhas de exemplo)")
    args = parser.parse_args(argv)
    cfg = Config.do_ambiente()
    cfg.marcar_demonstracao = cfg.marcar_demonstracao or args.demonstracao
    codigo, resumo = executar(args.comando, args.simular, cfg)
    print("\nRESUMO DA EXECUÇÃO")
    print(json.dumps({k: resumo[k] for k in ("run_id", "status", "contadores")}, ensure_ascii=False, indent=2))
    return codigo


if __name__ == "__main__":
    sys.exit(main())
