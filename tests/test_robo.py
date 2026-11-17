"""Testes de ponta a ponta: o robô inteiro contra um MVP falso, nos cenários de sucesso, decisão e falha."""
import json
import socket
import tempfile
import unittest
from pathlib import Path

from robo.auditoria import FALHA, SUCESSO, SUCESSO_COM_ALERTAS
from robo.config import Config
from robo.robo import executar

from mvp_falso import TOKEN, MvpFalso

CABECALHO = "latitude;longitude;categoria;descricao;canal;contato\n"
LINHAS_OK = [
    "-23,5646;-46,6522;Rampa;Rampa com entulho;Central 156;\n",
    "-23.5628;-46.6551;calçada obstruída;Mesas na calçada;Vistoria;\n",
    "-23.5661;-46.6497;travessia;Canteiro sem rebaixamento;ONG parceira;\n",
]


def porta_fechada() -> str:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{s.getsockname()[1]}"


class BaseRobo(unittest.TestCase):
    def setUp(self):
        self.mvp = MvpFalso()
        self.tmp = Path(tempfile.mkdtemp(prefix="robo-teste-"))
        self.entrada = self.tmp / "entrada"
        self.entrada.mkdir()
        self.cfg = self.config()

    def tearDown(self):
        self.mvp.parar()

    def config(self, **mudancas) -> Config:
        base = dict(mvp_url=self.mvp.url, mvp_admin_token=TOKEN, open_meteo_url=self.mvp.url + "/meteo",
                    overpass_url=self.mvp.url + "/overpass", dir_entrada=self.entrada,
                    dir_saida=self.tmp / "saida", dir_logs=self.tmp / "logs",
                    estado_db=self.tmp / "saida" / "estado.db", http_timeout=3, http_tentativas=3, http_backoff=0)
        base.update(mudancas)
        return Config(**base)

    def csv(self, nome: str, *linhas: str, cabecalho: str = CABECALHO) -> Path:
        caminho = self.entrada / nome
        caminho.write_text(cabecalho + "".join(linhas), encoding="utf-8")
        return caminho

    def rodar(self, comando="executar", simulacao=False, cfg=None):
        return executar(comando, simulacao, cfg or self.cfg, eco=False)

    def log(self) -> list[dict]:
        linhas = []
        for arq in (self.tmp / "logs").glob("*.jsonl"):
            linhas += [json.loads(linha) for linha in arq.read_text(encoding="utf-8").splitlines()]
        return linhas

    def eventos(self) -> list[str]:
        return [e["evento"] for e in self.log()]


class TestSucesso(BaseRobo):
    def test_importa_lote_e_move_para_processados(self):
        self.csv("lote.csv", *LINHAS_OK)
        codigo, resumo = self.rodar("importar")
        self.assertEqual(codigo, 0)
        self.assertEqual(resumo["status"], SUCESSO)
        self.assertEqual(resumo["contadores"]["relatos_criados"], 3)
        self.assertEqual(len(self.mvp.relatos), 3)
        self.assertFalse((self.entrada / "lote.csv").exists())
        self.assertEqual(len(list((self.entrada / "processados").glob("*lote.csv"))), 1)

    def test_demonstracao_marca_os_relatos_criados(self):
        self.csv("lote.csv", LINHAS_OK[0])
        self.rodar("importar", cfg=self.config(marcar_demonstracao=True))
        self.assertTrue(self.mvp.relatos[-1]["descricao_sanitizada"].endswith("[demonstração]"))

    def test_idempotente_rodar_duas_vezes_nao_duplica(self):
        self.csv("lote.csv", *LINHAS_OK)
        self.rodar("importar")
        self.csv("lote-reenviado.csv", *LINHAS_OK)  # alguém mandou a mesma planilha de novo
        _, resumo = self.rodar("importar")
        self.assertEqual(resumo["contadores"].get("relatos_criados", 0), 0)
        self.assertEqual(resumo["contadores"]["linhas_ja_processadas"], 3)
        self.assertEqual(len(self.mvp.relatos), 3)

    def test_executar_completo_gera_fila_e_indicadores(self):
        self.csv("lote.csv", *LINHAS_OK)
        codigo, _ = self.rodar()
        self.assertEqual(codigo, 0)
        fila = json.loads((self.cfg.dir_saida / "fila_priorizada.json").read_text(encoding="utf-8"))
        self.assertEqual(len(fila["itens"]), 3)
        self.assertEqual([i["score"] for i in fila["itens"]], sorted((i["score"] for i in fila["itens"]), reverse=True))
        self.assertTrue((self.cfg.dir_saida / "fila_priorizada.csv").exists())
        ind = json.loads((self.cfg.dir_saida / "indicadores.json").read_text(encoding="utf-8"))
        self.assertEqual(ind["relatos_recebidos"], 3)
        resumos = list((self.cfg.dir_saida / "execucoes").glob("*.json"))
        self.assertEqual(len(resumos), 1)

    def test_simulacao_nao_altera_nada(self):
        self.csv("lote.csv", *LINHAS_OK)
        self.mvp.adicionar(-23.5658, -46.6495, "rampa_bloqueada", dias_atras=3)
        _, resumo = self.rodar(simulacao=True)
        self.assertEqual(len(self.mvp.relatos), 1)
        self.assertEqual(self.mvp.patches, [])
        self.assertTrue((self.entrada / "lote.csv").exists())
        self.assertEqual(resumo["contadores"]["linhas_simuladas"], 3)


class TestDecisao(BaseRobo):
    def test_linhas_invalidas_sao_rejeitadas_e_as_validas_seguem(self):
        self.csv("misto.csv", LINHAS_OK[0],
                 "-22.9068;-43.1729;rampa;No Rio de Janeiro;Central 156;\n",
                 "-23.5630;-46.6540;buraco no asfalto;Fora do escopo;Vistoria;\n",
                 ";-46.6540;rampa;Sem latitude;Vistoria;\n")
        _, resumo = self.rodar("importar")
        self.assertEqual(resumo["status"], SUCESSO_COM_ALERTAS)
        self.assertEqual(resumo["contadores"]["relatos_criados"], 1)
        self.assertEqual(resumo["contadores"]["linhas_rejeitadas"], 3)
        motivos = {e["mensagem"] for e in self.log() if e["evento"] == "linha_rejeitada"}
        self.assertEqual(motivos, {"fora do território do piloto", "categoria desconhecida",
                                   "coordenada ausente ou não numérica"})

    def test_duplicata_de_relato_ja_aberto_nao_e_criada(self):
        existente = self.mvp.adicionar(-23.5646, -46.6522, "rampa_bloqueada")
        self.csv("lote.csv", LINHAS_OK[0], LINHAS_OK[0].replace("Central 156", "Vistoria"))
        _, resumo = self.rodar("importar")
        self.assertEqual(resumo["contadores"]["linhas_duplicadas"], 2)
        self.assertEqual(len(self.mvp.relatos), 1)
        dup = [e for e in self.log() if e["evento"] == "linha_duplicada"]
        self.assertEqual(dup[0]["relato_existente"], existente["id"])

    def test_alta_vai_para_em_analise_e_baixa_continua_pendente(self):
        alta = self.mvp.adicionar(-23.5658, -46.6495, "rampa_bloqueada", dias_atras=3)  # a ~15 m do hospital
        baixa = self.mvp.adicionar(-23.5628, -46.6551, "calcada_obstruida")
        _, resumo = self.rodar("triagem")
        self.assertEqual(self.mvp.patches, [(alta["id"], "em_analise")])
        self.assertEqual(baixa["status"], "pendente")
        fila = json.loads((self.cfg.dir_saida / "fila_priorizada.json").read_text(encoding="utf-8"))
        primeiro = fila["itens"][0]
        self.assertEqual((primeiro["id"], primeiro["prioridade"], primeiro["acao_do_robo"]),
                         (alta["id"], "ALTA", "movido_para_em_analise"))
        self.assertIn("hospital", " ".join(primeiro["motivos"]))
        self.assertEqual(fila["itens"][1]["prioridade"], "BAIXA")
        self.assertEqual(resumo["contadores"]["relatos_movidos_em_analise"], 1)

    def test_chuva_forte_sobe_prioridade(self):
        self.mvp.chuva_por_hora = 1.0  # 24 mm em 24 h
        self.mvp.pois = []
        self.mvp.adicionar(-23.5661, -46.6497, "travessia_barreira")
        self.rodar("triagem")
        fila = json.loads((self.cfg.dir_saida / "fila_priorizada.json").read_text(encoding="utf-8"))
        self.assertEqual(fila["itens"][0]["prioridade"], "MEDIA")
        self.assertTrue(any("chuva" in m for m in fila["itens"][0]["motivos"]))

    def test_robo_nunca_resolve_nem_descarta(self):
        self.mvp.adicionar(-23.5658, -46.6495, "rampa_bloqueada", dias_atras=30)
        self.rodar("triagem")
        self.assertTrue(all(status == "em_analise" for _, status in self.mvp.patches))

    def test_sem_token_so_prioriza(self):
        self.mvp.adicionar(-23.5658, -46.6495, "rampa_bloqueada", dias_atras=3)
        codigo, resumo = self.rodar("triagem", cfg=self.config(mvp_admin_token=""))
        self.assertEqual(codigo, 0)
        self.assertEqual(self.mvp.patches, [])
        self.assertIn("sem_token", self.eventos())

    def test_sem_ntfy_o_aviso_fica_registrado_como_desativado(self):
        self.mvp.adicionar(-23.5658, -46.6495, "rampa_bloqueada", dias_atras=3)
        _, resumo = self.rodar("triagem")
        self.assertIn("notificacao_desativada", self.eventos())
        self.assertEqual(resumo["status"], SUCESSO)


class TestFalha(BaseRobo):
    def test_push_que_falha_vira_alerta_e_nao_silencio(self):
        self.mvp.adicionar(-23.5658, -46.6495, "rampa_bloqueada", dias_atras=3)
        _, resumo = self.rodar("triagem", cfg=self.config(ntfy_url=porta_fechada()))
        self.assertEqual(self.mvp.patches[0][1], "em_analise")
        self.assertIn("notificacao_falhou", self.eventos())
        self.assertEqual(resumo["status"], SUCESSO_COM_ALERTAS)

    def test_mvp_fora_do_ar_termina_em_falha_registrada(self):
        self.csv("lote.csv", *LINHAS_OK)
        codigo, resumo = self.rodar(cfg=self.config(mvp_url=porta_fechada()))
        self.assertEqual(codigo, 1)
        self.assertEqual(resumo["status"], FALHA)
        self.assertIn("mvp_indisponivel", self.eventos())
        self.assertEqual(self.eventos().count("nova_tentativa"), 2)  # 3 tentativas = 2 novas
        self.assertTrue((self.entrada / "lote.csv").exists())  # arquivo não se perde

    def test_mvp_instavel_recupera_com_nova_tentativa(self):
        self.mvp.falhas["/api/reports"] = [503, 503]
        self.csv("lote.csv", *LINHAS_OK)
        codigo, resumo = self.rodar("importar")
        self.assertEqual(codigo, 0)
        self.assertEqual(resumo["contadores"]["relatos_criados"], 3)
        self.assertEqual(self.eventos().count("nova_tentativa"), 2)

    def test_mvp_cai_no_meio_do_lote_e_retoma_sem_duplicar(self):
        self.csv("lote.csv", *LINHAS_OK)
        codigo, resumo = self._rodar_com_queda()  # o MVP cai depois do primeiro relato criado
        self.assertEqual(codigo, 1)
        self.assertEqual(resumo["contadores"]["relatos_criados"], 1)
        self.assertTrue((self.entrada / "lote.csv").exists())
        self.mvp.fora.clear()
        codigo, resumo = self.rodar("importar")
        self.assertEqual(codigo, 0)
        self.assertEqual(resumo["contadores"]["relatos_criados"], 2)
        self.assertEqual(resumo["contadores"]["linhas_ja_processadas"], 1)
        self.assertEqual(len(self.mvp.relatos), 3)

    def _rodar_com_queda(self):
        import robo.fontes as fontes
        criar_original = fontes.MvpApi.criar
        chamadas = {"n": 0}
        mvp = self.mvp

        def criar_e_cair(api, *args):
            chamadas["n"] += 1
            if chamadas["n"] > 1:
                mvp.fora.add("/api/reports")
            return criar_original(api, *args)

        fontes.MvpApi.criar = criar_e_cair
        try:
            return self.rodar("importar")
        finally:
            fontes.MvpApi.criar = criar_original

    def test_arquivo_invalido_vai_para_quarentena(self):
        self.csv("sem-colunas.csv", "foo;bar\n", cabecalho="nome;telefone\n")
        (self.entrada / "planilha.xlsx").write_bytes(b"PK\x03\x04\x00\x00binario")
        (self.entrada / "vazio.csv").write_text("", encoding="utf-8")
        self.csv("bom.csv", LINHAS_OK[0])
        codigo, resumo = self.rodar("importar")
        self.assertEqual(codigo, 0)
        self.assertEqual(resumo["contadores"]["arquivos_quarentena"], 3)
        self.assertEqual(resumo["contadores"]["relatos_criados"], 1)
        motivos = sorted(p.read_text(encoding="utf-8").strip()
                         for p in (self.entrada / "quarentena").glob("*.motivo.txt"))
        self.assertEqual(motivos, ["arquivo vazio", "colunas obrigatórias ausentes: categoria, latitude, longitude",
                                   "formato não suportado (.xlsx); envie CSV"])

    def test_pasta_de_entrada_ausente_nao_derruba_o_robo(self):
        self.entrada.rmdir()
        codigo, resumo = self.rodar("importar")
        self.assertEqual(codigo, 0)
        self.assertIn("pasta_ausente", self.eventos())

    def test_open_meteo_fora_degrada_sem_parar(self):
        self.mvp.fora.add("/meteo")
        self.mvp.adicionar(-23.5658, -46.6495, "rampa_bloqueada", dias_atras=3)
        codigo, resumo = self.rodar("triagem")
        self.assertEqual(codigo, 0)
        self.assertEqual(resumo["status"], SUCESSO_COM_ALERTAS)
        fila = json.loads((self.cfg.dir_saida / "fila_priorizada.json").read_text(encoding="utf-8"))
        self.assertEqual(fila["contexto"]["fatores_indisponiveis"], ["chuva"])
        self.assertEqual(len(fila["itens"]), 1)

    def test_overpass_fora_usa_cache_antigo(self):
        self.mvp.adicionar(-23.5658, -46.6495, "rampa_bloqueada", dias_atras=3)
        self.rodar("triagem")  # popula o cache
        self.mvp.fora.add("/overpass")
        cfg = self.config(cache_pois_horas=0)  # força o cache a ser considerado vencido
        self.rodar("triagem", cfg=cfg)
        self.assertIn("pois_cache_vencido", self.eventos())

    def test_overpass_fora_sem_cache_usa_snapshot_versionado(self):
        self.mvp.fora.add("/overpass")
        self.mvp.adicionar(-23.5658, -46.6495, "rampa_bloqueada", dias_atras=3)
        codigo, _ = self.rodar("triagem")
        self.assertEqual(codigo, 0)
        self.assertIn("pois_snapshot", self.eventos())
        fila = json.loads((self.cfg.dir_saida / "fila_priorizada.json").read_text(encoding="utf-8"))
        self.assertGreater(fila["contexto"]["locais_sensiveis_considerados"], 0)

    def test_overpass_fora_sem_cache_nem_snapshot_segue_sem_o_fator(self):
        self.mvp.fora.add("/overpass")
        self.mvp.adicionar(-23.5658, -46.6495, "rampa_bloqueada", dias_atras=3)
        codigo, _ = self.rodar("triagem", cfg=self.config(pois_snapshot=self.tmp / "nao-existe.json"))
        self.assertEqual(codigo, 0)
        fila = json.loads((self.cfg.dir_saida / "fila_priorizada.json").read_text(encoding="utf-8"))
        self.assertEqual(fila["contexto"]["fatores_indisponiveis"], ["locais_sensiveis"])

    def test_token_errado_termina_em_falha(self):
        self.mvp.adicionar(-23.5658, -46.6495, "rampa_bloqueada", dias_atras=3)
        codigo, resumo = self.rodar("triagem", cfg=self.config(mvp_admin_token="errado"))
        self.assertEqual(codigo, 1)
        self.assertIn("token_invalido", self.eventos())

    def test_execucao_concorrente_e_ignorada(self):
        (self.cfg.dir_saida).mkdir(parents=True, exist_ok=True)
        (self.cfg.dir_saida / ".robo.lock").write_text("123")
        codigo, resumo = self.rodar()
        self.assertEqual(codigo, 0)
        self.assertIn("ignorada", self.eventos())


class TestLgpdNoFluxo(BaseRobo):
    def test_dado_pessoal_nao_vai_ao_mvp_nem_ao_log(self):
        self.csv("lote.csv", "-23.5646;-46.6522;rampa;Ligar p/ Joana 11 98765-4321 joana@mail.com;156;"
                             "Joana Silva (11) 98765-4321\n")
        self.rodar("importar")
        enviado = json.dumps(self.mvp.criados_via_post)
        log = "\n".join(p.read_text(encoding="utf-8") for p in (self.tmp / "logs").glob("*.jsonl"))
        for sensivel in ("98765", "joana@mail.com", "Joana Silva"):
            self.assertNotIn(sensivel, enviado)
            self.assertNotIn(sensivel, log)
        self.assertIn("dado_pessoal_removido", self.eventos())


if __name__ == "__main__":
    unittest.main()
