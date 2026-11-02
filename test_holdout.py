#!/usr/bin/env python3
"""Holdout cego da v4: 18 casos, escrito antes de medir. Regressao, nao alvo.

O primeiro holdout (`holdout.py`, 24 casos) deixou de ser prova independente no
momento em que o caso `relatorio_diario` serviu para acrescentar vocabulario a
v4. Este ficheiro existe para repor a honestidade da medicao: os 18 casos foram
escritos com o nivel que o dono aceitaria pagar, sem olhar ao score do
classificador, e o resultado publica-se seja qual for.

    python3 holdout2.py
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

PLUGIN = Path("/opt/data/plugins/vipernet-model-manager")


def _carregar(nome, caminho):
    spec = importlib.util.spec_from_file_location(nome, caminho)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


a = _carregar("analise_hold2", PLUGIN / "analise.py")
LIMIARES = {"media": 3, "alta": 6}


def m(*textos):
    return [{"role": "user", "content": t} for t in textos]


HOLDOUT = [
    # conversa trivial — nao vale um modelo pago
    ("cumprimento",        m("olá, tudo bem?"), "leve"),
    ("agradecimento",      m("perfeito, obrigado"), "leve"),
    ("pedido_de_hora",     m("que horas sao?"), "leve"),
    ("nome_do_host",       m("como se chama o host do HA?"), "leve"),
    ("leitura_de_sensor",  m("qual e a temperatura da sala?"), "leve"),
    ("explicacao_curta",   m("o que e o keepalived, numa frase?"), "leve"),

    # manutencao fina — produz, mas e de minutos
    ("typo_readme",        m("corrige o typo no README"), "media"),
    ("atualiza_lista",     m("atualiza a lista de hosts no README"), "media"),
    ("comentario_codigo",  m("acrescenta um comentario a esta funcao"), "media"),
    ("revisao_de_texto",   m("revis o texto deste email antes de enviar"), "media"),
    ("relatorio_semanal",  m("faz o resumo semanal do consumo da bateria"), "media"),

    # producao real — um modelo capaz
    ("parser_novo",        m("escreve um parser de configuracao INI em Python"), "alta"),
    ("automacao_nova",     m("cria uma automacao que carrega a bateria às 3h"), "alta"),
    ("plano_migracao",     m("planeia a migracao do NUC para o PVE2 com janela e rollback"), "alta"),
    ("refactor_modulo",    m("refatora o gestor.py para separar leitura de escrita"), "alta"),
    ("diagnostico_falha",  m("porque e que a automacao da bateria parou de disparar?"), "alta"),

    # decisao de arquitetura
    ("arquitetura_ha",     m("que arquitetura recomendas para a automacao solar?"), "alta"),
    ("comparacao_proxies", m("NPM ou Traefik para este homelab? justifica"), "media"),
]


def main() -> int:
    print(f"holdout2 (cego): {len(HOLDOUT)} casos\n")
    acertos = 0
    for nome, msgs, esperado in HOLDOUT:
        d = a.decidir(msgs, limiares=LIMIARES)
        ok = d["nivel"] == esperado
        acertos += ok
        print(f"  {'OK' if ok else 'X '} {nome:20s} {d['nivel']:6s} score={d['score']:2d} "
              f"esperado={esperado:6s} {d['sinais']}")
    # Baseline declarada: 17/18 (94%). O unico erro conhecido — "NPM ou Traefik,
    # justifica" — e discutivel no rotulo (decisao de arquitetura com 6 pontos),
    # e NAO se calibra contra ele: e o que aconteceu ao holdout1 e o destruiu.
    # O teste falha se a qualidade descer abaixo do medido.
    print(f"\n{acertos}/{len(HOLDOUT)} ({100*acertos//len(HOLDOUT)}%) | baseline 17")
    return 0 if acertos >= 17 else 1


if __name__ == "__main__":
    raise SystemExit(main())
