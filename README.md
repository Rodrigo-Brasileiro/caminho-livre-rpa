# Caminho Livre · Robô de importação e triagem (RPA)

Global Solution FIAP 2026.2 (CitizenTech), Engenharia de Software 4º ano.
Disciplina **Robotic Process Automation**, atividade *Do Processo Manual à Automação Inteligente*.

Este repositório traz o componente **Automação** do MVP [Caminho Livre](https://caminholivre-gs.azurewebsites.net): um robô em Python que tira do gestor duas tarefas manuais. A primeira é redigitar no mapa os relatos de barreiras que chegam por planilha. A segunda é decidir, relato por relato, o que atacar primeiro.

| | |
|---|---|
| **MVP (público)** | https://caminholivre-gs.azurewebsites.net |
| **Repositório do MVP** | https://github.com/santos-nikolas/caminho-livre |
| **Vídeo da automação** | _adicionar o link do vídeo_ |
| **Relatório técnico** | [`docs/relatorio-rpa.pdf`](docs/relatorio-rpa.pdf) |
| **Desenho do processo (BPMN)** | [`docs/processo-robo.bpmn`](docs/processo-robo.bpmn) · [imagem](assets/bpmn-processo-robo.png) |

## Integrantes

| Nome completo | RM |
|---|---|
| Guilherme Rocha Bianchini | RM97974 |
| Nikolas Rodrigues Moura dos Santos | RM551566 |
| Thiago Jardim de Oliveira | RM551624 |
| Rodrigo Brasileiro | RM98952 |

## Ficha Técnica do MVP (resumo)

| Campo | Conteúdo |
|---|---|
| Produto | Caminho Livre: mapa colaborativo de barreiras de acessibilidade em calçadas e travessias |
| Cidadão e território | Pedestres com deficiência ou mobilidade reduzida, idosos e cuidadores. Piloto no entorno do campus FIAP Paulista (SP), cerca de 1,2 km × 1,5 km |
| Problema | Barreiras são relatadas de forma dispersa e ninguém acompanha se foram resolvidas |
| Papel desta automação | Leva para o MVP os relatos que chegam por outros canais e entrega ao gestor uma fila priorizada e explicada |
| ODS | 10 (redução das desigualdades) e 11 (metas 11.2 e 11.7) |

## O processo manual que o robô substitui

| Antes (manual) | Depois (robô) |
|---|---|
| A central 156, as vistorias e as ONGs parceiras mandam planilhas. Alguém redigita cada linha no mapa | O robô lê a pasta `entrada/` a cada hora, valida, limpa e cria os relatos pela API |
| Planilhas trazem nome e telefone de quem relatou | Dados pessoais são descartados antes do envio (LGPD) |
| A mesma barreira entra duas vezes, por canais diferentes | Duplicatas a até 15 m, da mesma categoria, são detectadas e não entram |
| O gestor abre o mapa e decide no olho o que é urgente | Cada relato aberto recebe um score explicável, cruzado com hospitais, escolas, metrô (OpenStreetMap) e previsão de chuva (Open-Meteo) |
| Casos urgentes esperam alguém perceber | Relatos de prioridade ALTA passam para "em análise" no MVP e o gestor recebe um push |

## Arquitetura

```mermaid
flowchart LR
  ag[GitHub Actions<br/>cron de hora em hora] --> robo
  csv[(entrada/*.csv<br/>156, vistorias, ONGs)] --> robo
  subgraph robo [Robô Python]
    imp[1. Importação<br/>valida, LGPD, deduplica] --> tri[2. Triagem<br/>score explicável]
  end
  imp -- POST /api/reports --> mvp
  robo -- GET /health, /api/config, /api/reports --> mvp
  tri -- PATCH /api/admin/reports/id<br/>ALTA → em_analise --> mvp[MVP Caminho Livre<br/>FastAPI + SQLite no Azure]
  om[Open-Meteo<br/>chuva 24 h] --> tri
  osm[OpenStreetMap / Overpass<br/>hospitais, escolas, metrô] --> tri
  tri --> fila[(saida/fila_priorizada.json/.csv<br/>saida/indicadores.json)]
  tri -- push --> ntfy[ntfy.sh<br/>gestor]
  robo --> logs[(logs/*.jsonl<br/>saida/execucoes/*.json)]
```

**Por que Python e não UiPath ou Power Automate:** todas as fontes têm API (o MVP, o Open-Meteo e o OpenStreetMap), então não há tela para o robô clicar. Em Python o robô fica versionado, testado no CI e roda de graça no agendador do GitHub Actions, que a equipe já usa no componente de DevOps. O robô usa **só a biblioteca padrão** do Python 3.11+, então não há nada para instalar.

## Como rodar

```bash
git clone <este repositório> && cd <pasta>
cp .env.example .env          # opcional: MVP_ADMIN_TOKEN para mudar status, NTFY_URL para push

python rodar.py executar --simular   # lê tudo e mostra o que faria, sem alterar o MVP
python rodar.py executar             # importa entrada/*.csv e faz a triagem
python rodar.py importar             # só a etapa 1
python rodar.py triagem              # só a etapa 2

python scripts/demo_local.py         # demonstração completa contra um MVP falso local (sucesso, decisão e falha)
```

Para testar a importação, copie `exemplos/relatos-central156-novembro.csv` (e `exemplos/planilha-errada.csv`, para ver a quarentena) para `entrada/`.

Código de saída: `0` para SUCESSO ou SUCESSO_COM_ALERTAS, `1` para FALHA. O agendador marca a execução como vermelha quando a saída é `1`.

### Agendamento (mínima intervenção humana)

`.github/workflows/robo.yml` roda os testes a cada push e, de hora em hora, executa o robô contra o MVP público. O estado de idempotência fica no cache do Actions e os logs e saídas viram artefatos da execução. Segredos do repositório: `MVP_ADMIN_TOKEN` e `NTFY_URL` (opcional).

## Estrutura

```
src/robo/            código-fonte
  robo.py            orquestrador: trava, etapas, status final
  importacao.py      etapa 1: lote de CSVs → API do MVP
  triagem.py         etapa 2: prioridade, mudança de status, fila e indicadores
  regras.py          regras de negócio puras (validação, duplicata, score)
  fontes.py          conectores: MVP, Open-Meteo, Overpass, ntfy
  http_cliente.py    timeout, novas tentativas e backoff exponencial
  auditoria.py       logs JSON Lines e resumo por execução
  estado.py          SQLite local: idempotência e cache
  lgpd.py            mascaramento e minimização de dados pessoais
rodar.py             ponto de entrada
dados/               snapshot versionado dos locais sensíveis (OSM, ODbL)
exemplos/            lotes de exemplo, marcados como demonstração
docs/                BPMN, PDD e relatório técnico
assets/              imagens e evidências visuais
logs/amostras/       logs reais gerados, sem dados pessoais
tests/               testes automatizados e evidências
scripts/             demonstração local
```

## Regras de negócio

**Validação de cada linha (decisão):** a linha precisa de coordenada numérica (aceita `-23,5646`, do Excel em português), dentro do território do piloto, que o robô lê de `GET /api/config`, e uma categoria reconhecida. Sinônimos como "Calçada obstruída", "rampa" ou "faixa sem rebaixamento" são normalizados. Linha inválida é rejeitada, com o motivo no log, e as outras linhas seguem.

**Duplicata (decisão):** um relato aberto (pendente ou em análise) da mesma categoria a até 15 m. A linha não é criada e o log aponta o relato existente.

**Idempotência:** cada linha gera uma impressão digital (SHA-256 dos dados já normalizados e sem dado pessoal). Reenviar a mesma planilha não duplica nada.

**Prioridade (decisão), explicável ponto a ponto:**

| Fator | Pontos |
|---|---|
| Categoria: rampa bloqueada / travessia com barreira / calçada obstruída | 40 / 35 / 25 |
| Cada dia aberto | +5 (teto 30) |
| Hospital, clínica, escola, assistência social ou acesso ao metrô a ≤ 100 m / ≤ 200 m | +20 / +10 |
| Chuva prevista ≥ 5 mm nas próximas 24 h | +10 |
| Outros relatos abertos a até 30 m | +10 cada (teto 20) |

ALTA ≥ 70 (prazo sugerido de 2 dias), MÉDIA ≥ 45 (7 dias), BAIXA < 45 (15 dias). Cada item da fila traz a lista de `motivos`, por exemplo `"a 52 m de hospital 'Pró Matre Paulista' (+20)"`.

**Ação (decisão):** só relatos ALTA ainda pendentes mudam para `em_analise`. **Pessoa no circuito:** o robô nunca marca `resolvido` nem `descartado`. Essas decisões são do gestor.

## Exceções e resiliência

| Falha | Como o robô reage |
|---|---|
| MVP fora do ar, timeout ou 5xx | 3 tentativas com backoff exponencial (2 s, 4 s). Se persistir: status FALHA, código 1, push para o gestor, e o arquivo fica na entrada |
| MVP cai no meio de um lote | Para, mantém o arquivo e, na próxima execução, envia só as linhas que faltaram (idempotência) |
| MVP recusa uma linha (422) | Registra o motivo, marca a linha como rejeitada e segue com as outras |
| Token de gestor inválido (401) | FALHA explícita `token_invalido`, sem tentar de novo |
| Sem token configurado | Prioriza e grava a fila, mas não muda status, com um alerta no log |
| Arquivo vazio, sem colunas obrigatórias, binário ou `.xlsx` | Vai para `entrada/quarentena/` com um `.motivo.txt` |
| Pasta de entrada ausente | Alerta, e a triagem roda mesmo assim |
| Open-Meteo fora | Segue sem o fator chuva e registra `fatores_indisponiveis` na fila |
| Overpass (OSM) fora | Tenta um espelho, depois o cache local, depois o snapshot versionado em `dados/`, e só então segue sem o fator |
| Duas execuções ao mesmo tempo | Trava em arquivo: a segunda é ignorada, com registro |
| Erro inesperado | Capturado, registrado com o tipo e terminado com status FALHA. Nada falha em silêncio |

## Logs e auditoria

- `logs/robo-AAAA-MM-DD.jsonl`: um evento por linha, com `ts` (UTC), `run_id`, `nivel`, `etapa`, `evento`, `mensagem`, arquivo e linha ou `relato_id`.
- `saida/execucoes/<run_id>.json`: resumo da execução (início, fim, status, contadores, alertas e erros).
- Amostras reais em [`logs/amostras/`](logs/amostras/).
- Campos como `contato`, `nome`, `telefone`, `email` e `descricao` são removidos antes da gravação, e toda mensagem passa por um mascaramento de telefone, e-mail e CPF.

## Integração com o MVP

| Direção | Contrato |
|---|---|
| Robô → MVP | `POST /api/reports` cria os relatos importados. `PATCH /api/admin/reports/{id}` com `{"status":"em_analise"}` e o token de gestor |
| MVP → Robô | `GET /health`, `GET /api/config` (território), `GET /api/reports` |
| Robô → gestor / BI | `saida/fila_priorizada.json` e `.csv` (fila ordenada com motivos) e `saida/indicadores.json` (indicadores de impacto da ficha técnica) |

O resultado pode ser conferido no próprio mapa: os relatos importados aparecem como pins novos e os de prioridade ALTA mudam para "em análise".

## Testes

```bash
PYTHONPATH=src:tests python -m unittest discover -s tests -v
```

São 41 testes, sem rede externa. Os testes de ponta a ponta usam um MVP falso (`tests/mvp_falso.py`) com as mesmas rotas e regras do MVP real e falhas programáveis.

| Cenário | Exemplos |
|---|---|
| Sucesso | importação de lote, idempotência, fila e indicadores gerados, modo simulação |
| Decisão | linhas inválidas rejeitadas com motivo, duplicata, ALTA → em análise e BAIXA mantida, chuva sobe a prioridade, robô nunca resolve nem descarta |
| Falha | MVP fora, MVP instável que se recupera, queda no meio do lote, quarentena, Open-Meteo fora, Overpass fora (cache e snapshot), token errado, execução concorrente |
| LGPD | telefone, e-mail e nome não chegam ao MVP nem ao log |

Saída da última execução em [`tests/evidencias/resultado-testes.txt`](tests/evidencias/resultado-testes.txt).

## Segurança e LGPD

- Token de gestor só em variável de ambiente ou segredo do GitHub, nunca no código. O `.env` está no `.gitignore`.
- Minimização: a coluna `contato` nunca é lida para envio. Telefone, e-mail e CPF dentro da descrição são trocados por `[removido]` antes do envio (o MVP também sanitiza, então são duas camadas).
- Os logs não guardam a descrição nem o contato. A idempotência usa um hash dos dados já mascarados.
- Coordenadas são arredondadas a 4 casas (~11 m), a mesma precisão do MVP.
- O robô só usa HTTPS para os serviços externos e se identifica com User-Agent próprio.
- Os dados de exemplo são fictícios e marcados com `[demonstração]`.

## Fontes de dados

- **MVP Caminho Livre:** relatos dos cidadãos (API própria).
- **OpenStreetMap via Overpass API:** hospitais, clínicas, escolas, assistência social e acessos ao metrô. Licença ODbL, © colaboradores do OpenStreetMap.
- **Open-Meteo:** previsão horária de precipitação, uso não comercial gratuito, sem chave.
