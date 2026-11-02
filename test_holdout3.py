"""Holdout 3 — medicao cega. Nao afinar contra este ficheiro.

Escrito depois da v4.2, com frases em registo diferente do holdout 2 (mais
coloquial e mais formal, misturando pedidos reais do dono). Serve para medir,
nao para calibrar: se falhar, reporta-se a taxa e a causa; so se corrige o
conceito quando a causa for um vocabulario ausente, e entao este ficheiro
deixa de contar como amostra independente.
"""
import sys

import analise as a


def m(t):
    return [{"role": "user", "content": t}]


CASOS = [
    # leve
    ("bom_dia", m("bom dia, tudo em ordem?"), "leve"),
    ("obrigado", m("obrigado"), "leve"),
    ("hora", m("diz-me as horas"), "leve"),
    ("sensor_temp", m("qual e a temperatura da sala?"), "leve"),
    ("repetir", m("podes repetir isso?"), "leve"),
    ("quem_e", m("quem es tu?"), "leve"),
    # media
    ("typo_skill", m("ha um typo na descricao da skill de bateria"), "media"),
    ("trocar_icone", m("troca o icone desse cartao para uma bateria"), "media"),
    ("resumo_semanal", m("prepara o resumo semanal do consumo"), "media"),
    ("explicar_ota", m("explica-me o que e o ota do zigbee"), "media"),
    ("porque_escolha", m("porque optaste por sqlite e nao postgres?"), "media"),
    ("rev_texto", m("da uma olhada a este paragrafo e diz se esta claro"), "media"),
    # alta
    ("criar_skill", m("cria uma skill nova para gerir os certificados da CA"), "alta"),
    ("plano_firmware", m("planeia a atualizacao de firmware dos 24 hosts com janela"), "alta"),
    ("nao_arranca", m("o container do n8n nao arranca depois do reboot"), "alta"),
    ("desenhar_rede", m("desenha a segmentacao de vlans para a rede de casa"), "alta"),
    ("migrar_bd", m("migra a base de dados do sqlite para o postgres sem perder historico"), "alta"),
    ("auditar_seguranca", m("audita a exposicao publica dos servicos do homelab"), "alta"),
    ("parecer_cliente", m("escreve o parecer tecnico para o cliente sobre esta integracao"), "alta"),
    ("otimizar_consumo", m("otimiza o consumo noturno reduzindo o standby dos equipamentos"), "alta"),
]


def correr() -> int:
    ok = 0
    for nome, mensagens, esperado in CASOS:
        r = a.pontuar(mensagens)
        nivel = a.nivel_de_score(r["score"])
        marca = "OK " if nivel == esperado else "X  "
        ok += nivel == esperado
        print(f"  {marca}{nome:20} {nivel:5} score={r['score']:2} "
              f"esperado={esperado:5} {r['sinais']}")
    print(f"\n{ok}/{len(CASOS)} ({100 * ok // len(CASOS)}%)")
    return 0 if ok == len(CASOS) else 1


if __name__ == "__main__":
    sys.exit(correr())
