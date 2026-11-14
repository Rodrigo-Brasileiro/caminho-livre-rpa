"""Atalho para rodar o robô sem instalar nada: python rodar.py [executar|importar|triagem] [--simular]"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from robo.__main__ import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
