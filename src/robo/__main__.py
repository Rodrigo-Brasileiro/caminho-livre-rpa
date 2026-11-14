"""Linha de comando: python rodar.py [executar|importar|triagem] [--simular]"""
import argparse
import json
import sys

from .robo import COMANDOS, executar


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="robo", description="Robô de importação e triagem do Caminho Livre")
    parser.add_argument("comando", nargs="?", default="executar", choices=COMANDOS,
                        help="executar = importar + triagem (padrão)")
    parser.add_argument("--simular", action="store_true", help="não altera nada no MVP nem move arquivos")
    args = parser.parse_args(argv)
    codigo, resumo = executar(args.comando, args.simular)
    print("\nRESUMO DA EXECUÇÃO")
    print(json.dumps({k: resumo[k] for k in ("run_id", "status", "contadores")}, ensure_ascii=False, indent=2))
    return codigo


if __name__ == "__main__":
    sys.exit(main())
