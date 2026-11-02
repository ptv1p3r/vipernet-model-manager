"""Holdout independente 2, escrito depois da v4.1 — nao calibrar contra ele.

Regra: se um caso falhar, corrige-se o conceito (vocabulario/peso), nunca se
muda a etiqueta para o caso passar. Cada vez que este ficheiro for usado para
afinar, deixa de servir: escreve-se outro.
"""
import sys

import analise as a


def m(t):
    return [{"role": "user", "content": t}]


CASOS = [
    # --- leve: conversa, consulta, confirmacao ---
    ("cumprimento", m("ola, tudo bem?"), "leve"),
    ("agradecer", m("obrigado, ficou otimo"), "leve"),
    ("data_hora", m("que horas sao?"), "leve"),
    ("ip_do_nas", m("qual e o ip do unraid?"), "leve"),
    ("estado_bateria", m("qual e o estado da bateria agora?"), "leve"),
    ("confirmar", m("certo"), "leve"),
    ("verificar_servico", m("o nginx esta a correr?"), "leve"),

    # --- media: manutencao fina, relatorio, explicacao ---
    ("typo_config", m("corrige o typo no config do adguard"), "media"),
    ("renomear_entidade", m("renomeia a entidade do sensor para bateria_solar_soc"), "media"),
    ("comentario", m("acrescenta um comentario a esse bloco de codigo"), "media"),
    ("resumo_diario", m("faz o resumo diario da producao solar"), "media"),
    ("comparar_dois_modelos", m("compara o deepseek flash com o pro em preco"), "media"),
    ("explicar_conceito", m("explica como funciona o keepalived"), "media"),
    ("justificar_escolha", m("porque escolheste o npm em vez do traefik?"), "media"),

    # --- alta: producao, planeamento, diagnostico, arquitetura ---
    ("nova_automacao", m("cria uma automacao que carregue a bateria nas horas de vazio"), "alta"),
    ("migrar_lxc", m("migra o lxc do portainer para o pve2 sem downtime"), "alta"),
    ("plano_backup", m("define um plano de backups para os 19 repos com retencao"), "alta"),
    ("diagnostico_lento", m("o proxmox esta lento desde ontem, descobre a causa"), "alta"),
    ("refactor_script", m("refatora o script de sincronizacao para separar o parse do io"), "alta"),
    ("parecer_edp", m("preciso do parecer de arquitetura para o AAD do produto b2b"), "alta"),
    ("traceback", m("rebenta com isto: Traceback (most recent call last): File \"/opt/x.py\", line 3, in <module> KeyError: 'perfis'"), "alta"),
    ("trade_off", m("que trade-offs tem usar sqlite em vez de postgres aqui?"), "alta"),
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
