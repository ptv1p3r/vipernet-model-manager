# vipernet model manager

Escolhe **provider + modelo** em cada turno a partir da complexidade que o turno
mostra — não a partir de uma etiqueta que o dono escreveu — dentro do catálogo
que o dono declarou **para o perfil que está a ser servido**.

Três frases que resumem o contrato:

1. A **complexidade é medida** (última mensagem + estado do trabalho): tamanho do
   texto próprio, verbos de engenharia, blocos de código, tracebacks, caminhos,
   turnos e ferramentas usadas. `complexidade: alta` continua a existir como
   override explícito, mas o caminho normal é a análise.
2. O **catálogo é fechado e é do dono**. Fora das entradas declaradas não há
   rotas. Cada entrada diz que níveis de complexidade serve. Uma entrada cuja
   credencial não resolve é saltada, com registo.
3. Cada **perfil** decide se usa o plugin, como usa (estratégia) e com que lista.
   Um perfil ausente da config não é tocado.

## Instalação

```sh
ln -sfn /opt/data/plugins/vipernet-model-manager/cli.py /opt/data/bin/vmm
hermes plugins enable vipernet-model-manager            # no perfil que o deve usar
hermes -p pessoal  plugins enable vipernet-model-manager
hermes -p trabalho plugins enable vipernet-model-manager
```

`hermes plugins enable` recarrega o gateway sozinho. A config é lida a quente
(cache de 5 s), portanto editar o catálogo **não** exige reiniciar nada.

## Config

Ficheiro vivo: `/opt/data/plugin-data/vipernet-model-manager/config.json`
(override por `VMM_DIR`, útil para ensaios). Esquema:

```json
{
  "versao": 3,
  "activo": true,
  "registar": true,
  "analise": {"janela_mensagens": 8, "varredura_max_mensagens": 60,
               "limiares": {"media": 3, "alta": 7}, "descer_apos": 2},
  "perfis": {
    "trabalho": {
      "activo": true,
      "estrategia": "rodizio",
      "rodizio": {"por": "sessao"},
      "analise": {},
      "catalogo": [
        {"id": "deepseek-flash", "provider": "deepseek", "modelo": "deepseek-flash",
         "complexidade": ["leve", "media"]},
        {"id": "gpt-sol", "provider": "openrouter", "modelo": "openai/gpt-6.1-sol",
         "complexidade": "media"}
      ]
    }
  }
}
```

- `activo` (topo) — interruptor global; `registar` — escrever o ledger.
- `perfis.<nome>.activo` — interruptor daquele perfil.
- `estrategia`: `rodizio` (alterna no nível) ou `prioridade` (primeira entrada
  elegível). `rodizio.por`: `turno` ou `sessao`.
- `complexidade` por entrada: `"leve"`, `["leve","media"]`, `["alta"]`… Níveis:
  `leve`, `media`, `alta`.
- `analise` do perfil sobrepõe-se ao global chave a chave (limiares, janela,
  `descer_apos`).
- Perfil `"*"` serve de omissão para perfis não listados; um perfil exacto ganha
  sobre o `*`.
- Sem perfil na config e sem `*`: **não gerido** — o turno segue intacto.

## CLI `vmm`

```
vmm status [--json]                  estado global e por perfil
vmm on|off [--perfil NOME]           liga/desliga (sem --perfil: global)
vmm perfis                           geridos vs. existentes na máquina
vmm perfil add NOME [--copiar-de X]  acrescenta perfil (cópia opcional)
vmm perfil rm NOME                   deixa de ser gerido
vmm rota list [PERFIL] [--json]
vmm rota add PERFIL PROVIDER MODELO --complexidade leve,media [--id ID]
vmm rota rm PERFIL ID
vmm simular PERFIL "texto"           classifica e mostra as candidatas
vmm verificar [PERFIL]               resolve credenciais de cada rota (sem as imprimir)
vmm log [-n N] [--perfil NOME]
vmm caminho                          caminhos de config e ledger
vmm recarregar                       recarrega plugins no gateway
```

Códigos de saída: `0` ok · `1` há problema · `2` uso inválido · `3` não
encontrado.

## Escolha, por turno

1. resolve o perfil do turno (o gateway multiplexado serve vários no mesmo
   processo);
2. aplica os interruptores (global, perfil) — se algum está em baixo, nada muda;
3. classifica a complexidade e aplica **histerese**: sobe já, desce só depois de
   `descer_apos` avaliações abaixo do nível corrente (evita trocar de rota e
   estragar a cache por causa de uma mensagem curta);
4. percorre o catálogo do perfil na ordem da estratégia, saltando entradas cujo
   provider não tem credencial no escopo desse perfil;
5. troca o agente vivo (`agent.switch_model`): cliente HTTP, chave, `base_url`,
   `api_mode` e contabilidade mudam juntos — não é só reescrever o campo `model`.

Fail-open em toda a linha: sem decisão, sem rota ou com excepção, o pedido segue
intacto e a razão vai para `uso.err`.

## Ledger

`uso.jsonl` (append-only, uma linha JSON por evento) e `uso.err`. Eventos:
`troca`, `sem_troca`, `sem_rota`, `falha_troca`, `reset`, `desligado`,
`perfil_desligado`, `perfil_nao_gerido`, `sem_catalogo`, `resposta`.
O `resposta` regista o modelo que o **provider** diz que respondeu — é a prova
independente de que a troca valeu.

O ledger **não** leva texto de conversa: só perfil, sessão, nível, score, nomes
de sinais, providers, modelos, tempos e tokens.

## Limites conhecidos

- Importa quatro funções internas do Hermes (`resolve_runtime_provider`,
  `get_active_profile_name`, `reload_gateway_plugins`) — é a única via para
  resolver a credencial da rota. Pode partir num upgrade do Hermes; o selftest
  apanha-o (`Validate` avisa, não falha).
- A **contabilidade de custo** do Hermes continua a ler o modelo da sessão: a
  fatura interna não segue a troca. A prova vive no ledger.
- Modelos free do OpenRouter têm limites próprios (alguns recusam chat directo,
  outros impõem ritmo); a rota é saltada quando falha, mas o catálogo é
  responsabilidade do dono. `vmm verificar` mostra as credenciais, não a
  disponibilidade do modelo — para isso há que chamar o modelo.
- Histerese e rodízio vivem em memória do processo: um restart do gateway
  reinicia a contagem (o catálogo e os interruptores não).

## Auto-teste

```sh
PYTHONPATH=/opt/hermes python3 /opt/data/plugins/vipernet-model-manager/selftest.py
```

79 verificações: validação do esquema (com os casos negativos), semântica de
perfil não gerido/desligado, complexidade por modelo, rodízio, histerese, troca
com credencial do perfil, salto de rota sem credencial, ledger sem texto de
conversa, e o CLI a escrever config que o plugin volta a ler.
