"""Testes das regras de negócio (decisões) e da proteção de dados."""
import unittest
from datetime import datetime, timedelta, timezone

from robo import lgpd, regras

BBOX = (-23.5700, -46.6600, -23.5590, -46.6450)
AGORA = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)


def relato(categoria="rampa_bloqueada", dias=0, lat=-23.5646, lon=-46.6522, status="pendente", rid="r1"):
    criado = (AGORA - timedelta(days=dias)).isoformat().replace("+00:00", "Z")
    return {"id": rid, "categoria": categoria, "criado_em": criado, "latitude": lat, "longitude": lon, "status": status}


class TestValidacao(unittest.TestCase):
    def test_categoria_com_acento_e_sinonimo(self):
        self.assertEqual(regras.normalizar_categoria("Calçada Obstruída"), "calcada_obstruida")
        self.assertEqual(regras.normalizar_categoria("RAMPA"), "rampa_bloqueada")
        self.assertEqual(regras.normalizar_categoria("faixa sem rebaixamento"), "travessia_barreira")
        self.assertIsNone(regras.normalizar_categoria("buraco no asfalto"))

    def test_coordenada_com_virgula_de_planilha(self):
        self.assertEqual(regras.ler_coordenada("-23,5646"), -23.5646)
        self.assertIsNone(regras.ler_coordenada("abc"))
        self.assertIsNone(regras.ler_coordenada("nan"))

    def test_linha_valida(self):
        v = regras.validar_linha({"latitude": "-23,5646", "longitude": "-46,6522", "categoria": "Rampa",
                                  "descricao": "Rampa com entulho", "canal": "Central 156"}, BBOX)
        self.assertTrue(v.valida)
        self.assertEqual(v.categoria, "rampa_bloqueada")
        self.assertEqual(v.descricao, "Rampa com entulho (via central_156)")
        self.assertEqual(len(v.hash), 24)

    def test_decisao_fora_do_territorio(self):
        v = regras.validar_linha({"latitude": "-22.9", "longitude": "-43.2", "categoria": "rampa"}, BBOX)
        self.assertFalse(v.valida)
        self.assertEqual(v.motivo, "fora do território do piloto")

    def test_decisao_categoria_desconhecida(self):
        v = regras.validar_linha({"latitude": "-23.5646", "longitude": "-46.6522", "categoria": "buraco"}, BBOX)
        self.assertFalse(v.valida)
        self.assertEqual(v.motivo, "categoria desconhecida")

    def test_decisao_coordenada_ausente(self):
        v = regras.validar_linha({"latitude": "", "longitude": "-46.6522", "categoria": "rampa"}, BBOX)
        self.assertFalse(v.valida)


class TestLgpd(unittest.TestCase):
    def test_remove_telefone_email_cpf(self):
        texto = lgpd.mascarar("Ligar p/ Maria (11) 98765-4321, maria@mail.com, CPF 123.456.789-09")
        self.assertNotIn("98765", texto)
        self.assertNotIn("@", texto)
        self.assertNotIn("123.456", texto)

    def test_nao_mascara_identificador_de_relato(self):
        uuid = "0e905123-4073-ad8e-2dfbf09388c4"
        self.assertEqual(lgpd.mascarar(f"relato {uuid}"), f"relato {uuid}")
        self.assertEqual(lgpd.mascarar("retorno no 11 97777-0000 ou 11977770000"), "retorno no [removido] ou [removido]")

    def test_contato_marca_pii_e_nao_entra_na_descricao(self):
        v = regras.validar_linha({"latitude": "-23.5646", "longitude": "-46.6522", "categoria": "rampa",
                                  "descricao": "Entulho", "contato": "11 99999-0000"}, BBOX)
        self.assertTrue(v.pii_removida)
        self.assertNotIn("99999", v.descricao)

    def test_campos_proibidos_saem_do_log(self):
        limpo = lgpd.limpar_campos({"contato": "x", "descricao": "y", "arquivo": "a.csv"})
        self.assertEqual(limpo, {"arquivo": "a.csv"})


class TestDuplicata(unittest.TestCase):
    def test_mesmo_ponto_mesma_categoria_aberto(self):
        existente = relato()
        self.assertIs(regras.procurar_duplicata(-23.56465, -46.65222, "rampa_bloqueada", [existente]), existente)

    def test_categoria_diferente_nao_e_duplicata(self):
        self.assertIsNone(regras.procurar_duplicata(-23.5646, -46.6522, "calcada_obstruida", [relato()]))

    def test_relato_resolvido_nao_bloqueia_novo(self):
        self.assertIsNone(regras.procurar_duplicata(-23.5646, -46.6522, "rampa_bloqueada", [relato(status="resolvido")]))

    def test_ponto_a_50m_nao_e_duplicata(self):
        self.assertIsNone(regras.procurar_duplicata(-23.5650, -46.6522, "rampa_bloqueada", [relato()]))


class TestPrioridade(unittest.TestCase):
    def test_alta_rampa_perto_de_hospital_ha_2_dias(self):
        poi = {"tipo": "hospital", "nome": "Pró Matre", "distancia_m": 60}
        p = regras.calcular_prioridade(relato(dias=2), agora=AGORA, poi_proximo=poi)
        self.assertEqual((p.score, p.nivel, p.prazo_dias), (70, "ALTA", 2))
        self.assertTrue(any("hospital" in m for m in p.motivos))

    def test_media_travessia_com_chuva(self):
        p = regras.calcular_prioridade(relato("travessia_barreira"), agora=AGORA, chuva_mm=12.0)
        self.assertEqual((p.score, p.nivel), (45, "MEDIA"))

    def test_baixa_calcada_nova_sem_fatores(self):
        p = regras.calcular_prioridade(relato("calcada_obstruida"), agora=AGORA)
        self.assertEqual((p.score, p.nivel, p.prazo_dias), (25, "BAIXA", 15))

    def test_chuva_abaixo_do_limite_nao_soma(self):
        p = regras.calcular_prioridade(relato("calcada_obstruida"), agora=AGORA, chuva_mm=4.9)
        self.assertEqual(p.score, 25)

    def test_idade_tem_teto(self):
        p = regras.calcular_prioridade(relato("calcada_obstruida", dias=60), agora=AGORA)
        self.assertEqual(p.score, 25 + regras.TETO_IDADE)

    def test_recorrencia(self):
        todos = [relato(rid="a"), relato(rid="b", lat=-23.56462), relato(rid="c", lat=-23.5700)]
        self.assertEqual(regras.contar_recorrencias(todos[0], todos), 1)


if __name__ == "__main__":
    unittest.main()
