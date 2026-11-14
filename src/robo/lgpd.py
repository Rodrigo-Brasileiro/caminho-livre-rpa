"""Proteção de dados pessoais (LGPD): minimização e mascaramento.

O robô nunca envia ao MVP nem grava em log: nome, telefone, e-mail, CPF ou a coluna "contato".
"""
import re

EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
CPF = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")
TELEFONE = re.compile(r"\+?\(?\d{2}\)?[\s.-]?\d{4,5}[\s.-]?\d{4}\b")

# Campos que nunca podem aparecer em log de auditoria nem na saída
CAMPOS_PROIBIDOS = {"contato", "nome", "telefone", "email", "e-mail", "cpf", "descricao", "descricao_original"}


def mascarar(texto: str | None) -> str | None:
    if not texto:
        return texto
    texto = "".join(c for c in texto if c.isprintable())
    for padrao in (EMAIL, CPF, TELEFONE):
        texto = padrao.sub("[removido]", texto)
    return texto.strip()


def limpar_campos(campos: dict) -> dict:
    """Remove campos proibidos e mascara qualquer string restante antes de registrar."""
    limpo = {}
    for chave, valor in campos.items():
        if chave.lower() in CAMPOS_PROIBIDOS:
            continue
        limpo[chave] = mascarar(valor) if isinstance(valor, str) else valor
    return limpo
