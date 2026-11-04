---
name: painel-visibilidade-e-entrega
description: Como o painel do plugin mostra a config e como provar que o browser recebe o que esta no disco.
---

# Painel: visibilidade e entrega

Sempre-on rules (do contrato do plugin):

- A config monta-se **so** na pagina exclusiva de Configuracao — fechar grava e volta ao painel principal.
- O painel principal mostra **so** estado e log; zero controlos que escrevem config.
- Provar pela **leitura independente do remoto**: clone do Gitea por `?ref=` ou `ls-remote`, nao pelo auto-relato do script.
- Medir `sha256` do `dist/index.js` e `dist/style.css` servido pelo IP direto (`.33`) e pelo nome (`.home.server` / VIP `.30`).
- Confirmar `cache-control: no-store` (o NPM Asset Caching sobrepoe-se ao que a app serve).
- A versao da descoberta (`/api/dashboard/plugins`) pode atrasar-se face ao manifest no arranque; o `index.js` servido e o manifest sao a fonte de verdade.

Componentes do painel (versao 3.22.1):

- `ConfigEditor`: componente que abre uma pagina exclusiva com todos os campos editaveis (`Activo`, `Classificador`, `Estrategia`), com botoes `Validar`, `Gravar`, `Cancelar`. Usa `Seletor` para as listas fechadas (`opcoes`) e `Interruptor` para `Activo`. O componente esta no `dashboard/dist/index.js` (linha 153+).
- `Seletor`: lista fechada do backend (`/estado.opcoes`); aceita valores que ja nao estao na lista (mostra "(desconhecido)") para nao mentir sobre o que esta a decidir.
- `Interruptor`: botao de ligar/desligar do perfil ou do classificador; mostra `Ligado` / `Desligado` com `outline` / `default`.

Pitfalls:

- Se o componente `ConfigEditor` nao aparecer no painel, o ficheiro `dist/index.js` pode nao ter sido recarregado pelo contentor do dashboard (restart necessario).
- Se o botao `⚙` nao aparecer, a integracao no cartao do perfil pode nao ter sido aplicada; verificar se o componente esta ligado ao `props.perfil`.
- Se a config nao gravar, o endpoint `/config` pode nao aceitar o JSON; usar `JSON.stringify` no corpo do pedido e `Content-Type: application/json`.
- Se o `sha256` do ficheiro servido nao coincidir com o do disco, o browser pode estar a servir uma copia antiga (fazer `Ctrl+Shift+R`).
