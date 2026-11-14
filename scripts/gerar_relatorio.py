"""Gera o relatório técnico (HTML e PDF) a partir do template, com trechos reais de logs e testes.

    python scripts/gerar_relatorio.py --repo URL_DO_GITHUB --video URL_DO_VIDEO

O PDF sai em docs/relatorio-rpa.pdf quando há Chrome ou Edge disponível (Windows via WSL ou Linux).
"""
import argparse
import html
import json
import re
import shutil
import subprocess
from datetime import date
from pathlib import Path
from string import Template

RAIZ = Path(__file__).resolve().parents[1]
DOCS = RAIZ / "docs"
AMOSTRAS = RAIZ / "logs" / "amostras"

TIPO = {"TestSucesso": "Sucesso", "TestDecisao": "Decisão", "TestValidacao": "Decisão", "TestPrioridade": "Decisão",
        "TestDuplicata": "Decisão", "TestFalha": "Falha", "TestLgpd": "LGPD", "TestLgpdNoFluxo": "LGPD"}
ORDEM = ["Sucesso", "Decisão", "Falha", "LGPD"]


def tabela_testes() -> tuple[str, int, str]:
    texto = (RAIZ / "tests" / "evidencias" / "resultado-testes.txt").read_text(encoding="utf-8")
    linhas = re.findall(r"^(test_\w+) \((?:[\w.]+)\.(Test\w+)\.\w+\) \.\.\. (\w+)", texto, re.M)
    total = int(re.search(r"Ran (\d+) tests", texto).group(1))
    resultado = "OK" if re.search(r"^OK", texto, re.M) else "FALHOU"
    linhas.sort(key=lambda t: (ORDEM.index(TIPO.get(t[1], "Falha")), t[0]))
    html_linhas = []
    for nome, classe, status in linhas:
        legivel = nome.removeprefix("test_").replace("_", " ")
        html_linhas.append(f"  <tr><td>{TIPO.get(classe, classe)}</td><td>{html.escape(legivel)}</td>"
                           f"<td>{'✔ passou' if status == 'ok' else html.escape(status)}</td></tr>")
    return "\n".join(html_linhas), total, resultado


def log_demo(max_linhas: int = 30) -> str:
    eventos = [json.loads(linha) for linha in (AMOSTRAS / "demo-local-sucesso-decisao-falha.jsonl").read_text(
        encoding="utf-8").splitlines()]
    interessantes = {"inicio", "mvp_no_ar", "arquivo_quarentena", "dado_pessoal_removido", "relato_criado",
                     "linha_duplicada", "linha_rejeitada", "linha_ja_processada", "movido_para_em_analise",
                     "relato_priorizado", "fila_gravada", "nova_tentativa", "mvp_indisponivel", "fim"}
    saida = []
    for e in eventos:
        if e["evento"] not in interessantes:
            continue
        extras = {k: v for k, v in e.items() if k not in ("ts", "run_id", "nivel", "etapa", "evento", "mensagem", "mvp", "arquivo", "categoria")}
        ref = " ".join(f"{k}={str(v)[:8] if k.endswith('id') or k == 'relato_existente' else v}" for k, v in extras.items())
        saida.append(f"{e['ts'][11:19]} {e['nivel']:<5} {e['etapa']:<10} {e['evento']:<22} {e['mensagem'][:60]} {ref}".rstrip())
    if len(saida) > max_linhas:
        meio = max_linhas // 2
        saida = saida[:meio] + [f"   ... ({len(saida) - max_linhas} linhas omitidas) ..."] + saida[-meio:]
    return html.escape("\n".join(saida))


def exemplo_fila() -> str:
    fila = json.loads((AMOSTRAS / "fila_priorizada-producao.json").read_text(encoding="utf-8"))
    partes = [f"contexto: chuva 24 h = {fila['contexto']['chuva_prevista_24h_mm']} mm · "
              f"locais sensíveis = {fila['contexto']['locais_sensiveis_considerados']}"]
    for item in fila["itens"]:
        partes.append(f"\n{item['prioridade']:<5} score={item['score']:<3} {item['categoria']:<19} id={item['id'][:8]}")
        partes += [f"      · {m}" for m in item["motivos"]]
    return html.escape("\n".join(partes))


def resumo_producao() -> str:
    resumo = json.loads((AMOSTRAS / "resumo-execucao-producao.json").read_text(encoding="utf-8"))
    return html.escape(json.dumps({k: resumo[k] for k in ("run_id", "simulacao", "inicio", "fim", "status",
                                                          "contadores", "alertas")}, ensure_ascii=False, indent=1))


def figura_integracao() -> str:
    extras = []
    for nome, legenda in (("mvp-integracao.png", "Figura 3: relatos criados e status alterados pelo robô no MVP."),
                          ("actions-execucao.png", "Figura 4: execução agendada no GitHub Actions.")):
        if (RAIZ / "assets" / nome).exists():
            extras.append(f'<figure><img src="../../assets/{nome}"><figcaption>{legenda}</figcaption></figure>')
    return "\n".join(extras)


def navegador() -> str | None:
    for caminho in ("/mnt/c/Program Files/Google/Chrome/Application/chrome.exe",
                    "/mnt/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
                    "google-chrome", "chromium", "microsoft-edge"):
        if Path(caminho).exists() or shutil.which(caminho):
            return caminho
    return None


def caminho_para(nav: str, p: Path) -> str:
    if nav.startswith("/mnt/"):
        return subprocess.run(["wslpath", "-w", str(p)], capture_output=True, text=True, check=True).stdout.strip()
    return str(p)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="(link do repositório)")
    ap.add_argument("--video", default="(link do vídeo)")
    args = ap.parse_args()
    testes, total, resultado = tabela_testes()
    conteudo = Template((DOCS / "relatorio" / "template.html").read_text(encoding="utf-8")).substitute(
        REPO_URL=html.escape(args.repo), VIDEO_URL=html.escape(args.video), DATA=date.today().strftime("%d/%m/%Y"),
        TABELA_TESTES=testes, N_TESTES=total, RESULTADO=resultado, LOG_DEMO=log_demo(),
        EXEMPLO_FILA_PRODUCAO=exemplo_fila(), RESUMO_PRODUCAO=resumo_producao(), FIGURA_INTEGRACAO=figura_integracao())
    saida_html = DOCS / "relatorio" / "relatorio-rpa.html"
    saida_html.write_text(conteudo, encoding="utf-8")
    print(f"HTML: {saida_html}")
    nav = navegador()
    if not nav:
        print("Chrome/Edge não encontrado: abra o HTML no navegador e use Imprimir > Salvar como PDF.")
        return
    pdf = DOCS / "relatorio-rpa.pdf"
    url = "file:///" + caminho_para(nav, saida_html).replace("\\", "/")
    subprocess.run([nav, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={caminho_para(nav, pdf)}", url],
                   cwd="/mnt/c" if nav.startswith("/mnt/") else None, capture_output=True, timeout=120)
    print(f"PDF: {pdf}" if pdf.exists() else "Falha ao gerar o PDF")


if __name__ == "__main__":
    main()
