"""Holdout 4 — ultima medicao cega da v4.3. NAO afinar contra este ficheiro.

Escrito apos a v4.3 e medido uma vez. O numero que sai daqui e o que se reporta
como taxa fora da amostra; se falhar, a causa fica registada como lacuna
conhecida em vez de se corrigir o vocabulario para o caso passar.
"""
import sys

import analise as a


def m(t):
    return [{"role": "user", "content": t}]


CASOS = [
    # leve
    ("saudacao", m("boas, como vai isso?"), "leve"),
    ("ok", m("ok, percebi"), "leve"),
    ("sensor_porta", m("a porta da entrada esta fechada?"), "leve"),
    ("valor_soc", m("diz-me o soc da bateria"), "leve"),
    ("ajuda_curta", m("o que fazes?"), "leve"),

    # media
    ("acentos", m("poe acentos nesse texto"), "media"),
    ("ordem_cartoes", m("muda a ordem dos cartoes do dashboard"), "media"),
    ("relatorio_mensal", m("gera o relatorio mensal de energia"), "media"),
    ("duvida_keepalived", m("explica a diferenca entre vrrp e keepalived"), "media"),
    ("razao_decisao", m("porque e que escolheste o flash para isto?"), "media"),
    ("ajuste_limiar", m("ajusta o limiar do sensor de humidade para 60"), "media"),

    # alta
    ("nova_integracao", m("integra o medidor modbus no home assistant"), "alta"),
    ("plano_ups", m("define a estrategia de ups para os dois nos proxmox"), "alta"),
    ("servico_morto", m("o servico do adguard morreu outra vez esta noite"), "alta"),
    ("arquitetura_solar", m("qual a melhor arquitetura para o excedente solar?"), "alta"),
    ("script_sync", m("escreve o script que sincroniza as duas arvores no gitea"), "alta"),
    ("diagnostico_zigbee", m("a malha zigbee tem dispositivos offline, investiga"), "alta"),
    ("migracao_nas", m("migra os dados do unraid para o novo pool com verificacao"), "alta"),
    ("revisao_geral", m("revê a configuracao do nginx e diz o que exposemos"), "alta"),
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
