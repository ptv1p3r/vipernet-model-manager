/**
 * Vipernet Model Manager — painel do dashboard.
 *
 * Le o estado real do plugin do agente pela API montada em
 * /api/plugins/vipernet-model-manager/ — nao ha estado paralelo no browser.
 * O unico gesto de escrita e o interruptor (global ou por perfil); o catalogo,
 * as associacoes de tarefa e o log sao leitura.
 *
 * Plain IIFE, sem build step. Usa window.__HERMES_PLUGIN_SDK__ (React + shadcn)
 * e registra-se em window.__HERMES_PLUGINS__.register(nome, Componente).
 */
(function () {
  "use strict";

  const SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK || !window.__HERMES_PLUGINS__) return;

  const React = SDK.React;
  const h = React.createElement;
  const hooks = SDK.hooks || {};
  const useState = hooks.useState || React.useState;
  const useEffect = hooks.useEffect || React.useEffect;
  const useCallback = hooks.useCallback || React.useCallback;

  const C = SDK.components || {};
  const Card = C.Card || "div";
  const CardContent = C.CardContent || "div";
  const Badge = C.Badge || "span";
  const Button = C.Button || "button";

  const NOME = "vipernet-model-manager";
  const BASE = "/api/plugins/" + NOME;

  // Identidade do plugin para o cabecalho. Pura e testavel sem browser: o
  // endpoint manda `plugin` com a versao do plugin.yaml, o autor e o
  // repositorio publico; aqui so se decide o que mostrar e se ha deriva entre
  // a versao do plugin e a do manifest do painel.
  function metaPlugin(estado) {
    var p = (estado && estado.plugin) || {};
    var versao = p.versao || "";
    var manifest = p.versao_manifest || (estado && estado.versao_painel) || "";
    return {
      versao: versao || "—",
      autor: p.autor || "—",
      github: p.github || "",
      manifest: manifest || "—",
      divergente: !!(versao && manifest && versao !== manifest),
    };
  }

  // Cabecalho de identidade: versao do plugin, autor e repositorio publico.
  // Vive fora do componente para se testar com dados reais sem um React a
  // serio — o componente limita-se a chama-la.
  function linhaIdentidade(meta) {
    return h(
      "div",
      { className: "vmm-meta" },
      h("span", { className: "vmm-meta-versao" }, "v" + meta.versao),
      h("span", { className: "vmm-meta-sep" }, "·"),
      h("span", { className: "vmm-meta-autor" }, meta.autor),
      meta.github ? h("span", { className: "vmm-meta-sep" }, "·") : null,
      meta.github
        ? h(
            "a",
            {
              className: "vmm-meta-link",
              href: meta.github,
              target: "_blank",
              rel: "noopener noreferrer",
              title: "Repositorio publico do autor",
            },
            meta.github.replace(/^https?:\/\/(www\.)?github\.com\//, "github.com/")
          )
        : null,
      meta.divergente
        ? h(
            "span",
            { className: "vmm-meta-deriva", title: "plugin.yaml e manifest.json divergem" },
            " · painel v" + meta.manifest
          )
        : null
    );
  }

  function pedir(caminho, opcoes) {
    var opts = Object.assign({}, opcoes || {});
    // O SDK.fetchJSON entrega o init tal e qual ao fetch(): um body objecto vira a
    // string "[object Object]" (text/plain) e o servidor responde 422 "Input should
    // be a valid dictionary". Serializa-se aqui e marca-se o Content-Type — o mesmo
    // padrao do painel kanban.
    if (opts.body !== undefined && opts.body !== null) {
      if (typeof opts.body !== "string") opts.body = JSON.stringify(opts.body);
      opts.headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
    }
    if (SDK.fetchJSON) return SDK.fetchJSON(BASE + caminho, opts);
    // Fallback minimo: o host injecta o token de sessão no header em modo loopback.
    const tok = window.__HERMES_SESSION_TOKEN__;
    const headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
    if (tok) headers["X-Hermes-Session-Token"] = tok;
    const resto = Object.assign({}, opts);
    delete resto.headers;
    return fetch(BASE + caminho, Object.assign({ credentials: "include", headers: headers }, resto))
      .then(function (r) {
        if (!r.ok) throw new Error(r.status + ": " + r.statusText);
        return r.json();
      });
  }

  function hora(ts) {
    if (!ts) return "—";
    const d = new Date(ts);
    if (isNaN(d.getTime())) return String(ts);
    try {
      return d.toLocaleString("pt-PT", {
        timeZone: "Europe/Lisbon",
        day: "2-digit", month: "2-digit",
        hour: "2-digit", minute: "2-digit", second: "2-digit",
      });
    } catch (_) {
      return d.toISOString();
    }
  }

  function Chip(props) {
    return h(
      Badge,
      { variant: props.tone || "secondary", className: "vmm-chip" },
      props.children
    );
  }

  function Interruptor(props) {
    return h(
      Button,
      {
        size: "sm",
        variant: props.activo ? "outline" : "default",
        className: "vmm-switch",
        disabled: props.aGravar,
        onClick: props.onClick,
        title: props.title || "",
      },
      props.aGravar ? "…" : props.activo ? "Ligado — desligar" : "Desligado — ligar"
    );
  }

  // ---- estrategia: lista fechada -----------------------------------------
  // As opcoes vem do backend (`/estado.opcoes.estrategia`), nao de uma copia
  // escrita aqui. Assim um valor novo aparece no `select` sem mexer no painel,
  // e nunca aparece uma opcao que a validacao recusaria — a lista que se ve e
  // a lista que o `gestor` aceita.
  function Seletor(props) {
    const opcoes = props.opcoes || [];
    const valor = props.valor == null ? "" : String(props.valor);
    // Um valor em vigor que ja nao esta na lista (config antiga, valor
    // removido) tem de aparecer: sem isto o `select` mostra a primeira opcao e
    // mente sobre o que esta a decidir.
    const falta = valor && !opcoes.some(function (o) { return o.valor === valor; });
    const filhos = [];
    if (falta) filhos.push(h("option", { key: "__f", value: valor }, valor + " (desconhecido)"));
    opcoes.forEach(function (o) {
      filhos.push(h("option", { key: o.valor, value: o.valor }, o.rotulo || o.valor));
    });
    return h(
      "select",
      {
        className: "vmm-select",
        value: valor,
        disabled: !!props.disabled,
        title: props.title || "",
        "aria-label": props.rotulo || props.title || "estratégia",
        onChange: function (e) { props.onChange(e.target.value); },
      },
      filhos
    );
  }

  // Texto do que esta em vigor, com a proveniencia. Um perfil que herda tem de
  // o dizer: sem isso, o utilizador muda o global e nao percebe porque e que
  // aquele cartao nao mudou.
  function rotuloEstrategia(est) {
    const e = est || {};
    const modo = String(e.modo || "rodizio");
    const por = String(e.por || "sessao");
    const origem = String(e.origem || "padrao");
    const onde = origem === "proprio" ? "própria do perfil"
      : origem === "herdado" ? "herdada do global"
      : origem === "global" ? "definida globalmente"
      : "por omissão";
    const oque = modo === "rodizio"
      ? "rodação " + (por === "turno" ? "por turno" : "por sessão")
      : "prioridade (primeira da lista)";
    return oque + " · " + onde;
  }

  // ---- indicador modelo/heuristica do cartao -----------------------------
  // A pergunta do cartao nao e "esta ligado?", e "esta a decidir?". Um
  // classificador ligado que falha cai na heuristica, e um perfil sem bloco
  // herda o global sem o mostrar em lado nenhum. Por isso o rotulo diz as duas
  // coisas: o que devia decidir e a evidencia do ledger de que decidiu.
  function _hhmm(iso) {
    try {
      const d = new Date(iso);
      if (isNaN(d.getTime())) return "";
      return d.toLocaleTimeString("pt-PT", { hour: "2-digit", minute: "2-digit" });
    } catch (e) { return ""; }
  }
  function _haMin(iso) {
    try {
      const t = Date.parse(iso);
      if (isNaN(t)) return null;
      return Math.max(0, Math.round((Date.now() - t) / 60000));
    } catch (e) { return null; }
  }

  function rotuloAnalise(a) {
    const vazio = {
      modo: "heuristica",
      titulo: "heurística",
      tom: "secondary",
      detalhe: "",
      evidencia: "",
      alerta: "",
    };
    if (!a || typeof a !== "object") return vazio;
    const c = a.classificador || {};
    const nomes = {
      modelo: "modelo",
      analise: "heurística",
      tarefa: "tarefa",
      marcador: "marcador",
      analise_fallback_tarefa: "tarefa sem rota",
      reset: "sem decisão",
    };
    const origens = a.origens || {};
    const contagens = [];
    Object.keys(origens).forEach(function (k) {
      const n = origens[k] || 0;
      if (n > 0) contagens.push(n + " " + (nomes[k] || k));
    });
    let evidencia = a.respostas > 0
      ? "últimos " + a.respostas + " turnos: " + contagens.join(" · ")
      : "";
    const taxa = a.taxa || {};
    const lidas = (taxa.leituras || 0) + (taxa.falhas || 0);
    if (lidas > 0) {
      const taxaTxt = (taxa.leituras || 0) + "/" + lidas + " leituras ok"
        + (taxa.falhas ? " (" + Math.round((taxa.falhas / lidas) * 100) + "% falhas)" : "");
      evidencia = evidencia ? evidencia + " · classificador: " + taxaTxt : "classificador: " + taxaTxt;
    }
    const rasto = a.rasto || {};
    // O aviso so vale para a ultima chamada: um erro antigo nao pode ficar a
    // assombrar um classificador que voltou a acertar (foi medido: 149 leituras
    // boas e 16 falhas no mesmo modelo, intermitente).
    const alertaBruto = c.modo === "modelo" && rasto.falhou ? (a.erro || "falhou") : "";
    // Provider inexistente no perfil (ou credencial ausente) nao e avaria: o
    // classificador esta declarado mas nao consegue decidir. Mostra-se como
    // facto no detalhe, sem o ⚠ reservado a falhas intermitentes.
    const motivoConfig = c.modo === "modelo" && !alertaBruto ? String(a.motivo || "") : "";

    if (c.modo === "modelo") {
      const onde = c.origem === "proprio" ? "próprio do perfil" : "herdado do global";
      const extra = [];
      // Com o transporte de decisoes o esforco e o formato nao existem: o modelo
      // devolve escolha e confianca, nao texto livre. Mostrar "esforço baixo"
      // seria descrever uma alavanca que nao esta ligada a nada.
      if (c.via === "decisoes") {
        extra.push("via decisões");
        if (c.confianca_min) extra.push("confiança ≥ " + c.confianca_min);
      } else {
        if (c.esforco) extra.push("esforço " + c.esforco);
        // O formato so se mostra quando nao e o de fabrica: `auto` e o normal e
        // nao vale um chip. `texto` e uma escolha, e essa ve-se.
        if (c.formato_json && c.formato_json !== "auto") extra.push("formato " + c.formato_json);
      }
      extra.push(onde);
      if (motivoConfig) extra.push("sem efeito — " + motivoConfig);
      if (rasto.lido) {
        const conf = rasto.confianca;
        const confTxt = (typeof conf === "number") ? " (" + Math.round(conf * 100) + "%)" : "";
        extra.push("leu " + rasto.lido + confTxt
          + (rasto.ms ? " em " + Math.round(rasto.ms) + " ms" : ""));
      }
      // Uma leitura de outro classificador (ou anterior ao erro actual) nao e
      // estado: vai rotulada como historico, com a idade e quem a produziu.
      // Sem isto o cartao dizia "sem efeito — provider indisponivel · leu alta
      // em 1355 ms" — duas verdades de momentos diferentes na mesma linha.
      const hist = a.historico || null;
      if (hist && hist.lido) {
        const idade = hist.idade_s;
        const quando = idade == null ? ""
          : (idade < 90 ? " há " + Math.round(idade) + " s"
            : idade < 5400 ? " há " + Math.round(idade / 60) + " min"
            : " há " + Math.round(idade / 3600) + " h");
        extra.push("histórico: " + hist.lido + quando
          + " com " + (hist.provider || "?") + "/" + (hist.modelo || "?"));
      }
      // Um motivo antigo tambem e historia. A config mudou depois dele, ou
      // passou tempo sem um turno que o confirmasse: apresentá-lo como «sem
      // efeito» de hoje foi o que manteve no cartão, durante duas horas, um
      // erro de provider que já não existia.
      if (!motivoConfig && hist && hist.motivo_antigo && hist.motivo_antigo.motivo) {
        const idadeM = hist.motivo_antigo.idade_s;
        const quandoM = idadeM == null ? ""
          : (idadeM < 90 ? " há " + Math.round(idadeM) + " s"
            : idadeM < 5400 ? " há " + Math.round(idadeM / 60) + " min"
            : " há " + Math.round(idadeM / 3600) + " h");
        // Só a etiqueta do motivo (antes do «:»): o texto completo do erro do
        // provider é uma linha de traceback que nao cabe num cartao.
        const etiqueta = String(hist.motivo_antigo.motivo).split(":")[0];
        extra.push("histórico: " + etiqueta + quandoM
          + " — " + (hist.motivo_antigo.porque || "desactualizado"));
      }
      return {
        modo: "modelo",
        titulo: "modelo · " + (c.provider || "?") + "/" + (c.modelo || "?"),
        tom: "default",
        detalhe: extra.join(" · "),
        evidencia: evidencia,
        alerta: (function () {
          if (!alertaBruto) return "";
          const hora = _hhmm(rasto.ts);
          const idade = _haMin(rasto.ts);
          const quando = hora
            ? " às " + hora + (idade !== null ? " (há " + (idade < 1 ? "instantes" : idade + " min") + ")" : "")
            : "";
          return "o classificador falhou" + quando + " — caiu na heurística: " + alertaBruto;
        })(),
      };
    }
    const motivo = String(c.motivo || "");
    let detalhe = "sem modelo definido — decide a heurística";
    if (motivo === "desligado") detalhe = "classificador desligado — decide a heurística";
    else if (motivo) detalhe = "classificador sem efeito (" + motivo + ") — decide a heurística";
    return {
      modo: "heuristica",
      titulo: "heurística",
      tom: "secondary",
      detalhe: detalhe,
      evidencia: evidencia,
      alerta: "",
    };
  }

  function CartaoPerfil(props) {
    const p = props.dados;
    const nome = props.nome;
    const tarefas = p.tarefas || {};
    const nomesTarefa = Object.keys(tarefas);

    return h(
      Card,
      { className: "vmm-card" },
      h(
        CardContent,
        { className: "vmm-card-content" },
        h(
          "div",
          { className: "vmm-linha vmm-cabecalho-perfil" },
          h("div", { className: "vmm-titulo-perfil" },
            h("strong", null, nome),
            h(Chip, { tone: p.activo ? "default" : "secondary" }, p.activo ? "activo" : "desligado"),
            h("span", { className: "vmm-estrategia" },
              h(Seletor, {
                rotulo: "estratégia de " + nome,
                valor: (p.estrategia || {}).modo || "rodizio",
                opcoes: ((props.opcoes || {}).modo) || [],
                disabled: props.aGravar,
                onChange: function (v) { props.onEstrategia(nome, { modo: v }); },
              }),
              h(Seletor, {
                rotulo: "rotação de " + nome,
                valor: (p.estrategia || {}).por || "sessao",
                opcoes: ((props.opcoes || {}).por) || [],
                // `por` so governa a rotacao: em `prioridade` a escolha nao tem
                // efeito, e um select activo que nao faz nada e uma armadilha.
                disabled: props.aGravar || (p.estrategia || {}).modo !== "rodizio",
                title: "Só se aplica à rotação",
                onChange: function (v) { props.onEstrategia(nome, { por: v }); },
              }),
              h("span", { className: "vmm-mudo" }, rotuloEstrategia(p.estrategia)),
              (p.estrategia || {}).origem === "proprio"
                ? h(Button, {
                    size: "sm", variant: "ghost", className: "vmm-herdar",
                    disabled: props.aGravar,
                    title: "Apagar a escolha deste perfil e voltar a herdar o global",
                    onClick: function () { props.onEstrategia(nome, null, true); },
                  }, "herdar do global")
                : null
            )
          ),
          h(Interruptor, {
            activo: p.activo,
            aGravar: props.aGravar,
            onClick: function () { props.onToggle(nome, !p.activo); },
            title: "Ligar/desligar o gestor neste perfil",
          })
        ),

        p.ultimo
          ? h(
              "div",
              { className: "vmm-ultimo" },
              "ultimo turno: ",
              h("strong", null, (p.ultimo.provider || "?") + " / " + (p.ultimo.modelo || "?")),
              " · nivel " + (p.ultimo.nivel || "—"),
              " · origem " + (p.ultimo.origem || "—"),
              " · " + hora(p.ultimo.ts)
            )
          : h("div", { className: "vmm-mudo" }, "sem turnos registados neste perfil"),

        (function () {
          const r = rotuloAnalise(p.analise);
          return h(
            "div",
            { className: "vmm-analise vmm-analise-" + r.modo },
            h("span", { className: "vmm-rotulo" }, "análise da complexidade"),
            h(Chip, { tone: r.tom }, r.titulo),
            r.detalhe ? h("span", { className: "vmm-mudo" }, r.detalhe) : null,
            r.evidencia ? h("span", { className: "vmm-mudo vmm-evidencia" }, r.evidencia) : null,
            r.alerta ? h("span", { className: "vmm-alerta" }, "⚠ " + r.alerta) : null
          );
        })(),

        nomesTarefa.length
          ? h(
              "div",
              { className: "vmm-tarefas" },
              h("span", { className: "vmm-rotulo" }, "Associacoes de tarefa"),
              h(
                "div",
                { className: "vmm-chips" },
                nomesTarefa.map(function (t) {
                  const def = tarefas[t] || {};
                  const termos = def.termos || [];
                  return h(
                    Chip,
                    { key: t, tone: "outline" },
                    t + " · prioridade " + (def.prioridade != null ? def.prioridade : 0) +
                      (termos.length ? " · " + termos.length + " termos" : "")
                  );
                })
              )
            )
          : h("div", { className: "vmm-mudo" }, "sem associacoes de tarefa — decide so a analise de complexidade"),

        // Superficie de sessao (cron, api_server, cli...): e o criterio que
        // ganha a tarefa e a analise, porque nao se adivinha — vem do core.
        (function () {
          const sups = perfil.superficies || {};
          const nomes = Object.keys(sups);
          if (!nomes.length) return null;
          return h(
            "div",
            { className: "vmm-tarefas" },
            h("span", { className: "vmm-rotulo" }, "Superficies de sessao"),
            h(
              "div",
              { className: "vmm-chips" },
              nomes.map(function (k) {
                const r = sups[k] || {};
                const alvo = r.activo === false
                  ? "anulada"
                  : r.entrada
                    ? r.entrada
                    : r.nivel
                      ? "nivel " + r.nivel
                      : (r.provider || "") + "/" + (r.modelo || "");
                return h(
                  Chip,
                  { key: k, tone: "outline" },
                  k + " → " + alvo + (r.origem_regra === "global" ? " · global" : "")
                );
              })
            )
          );
        })(),

        h(
          "table",
          { className: "vmm-tabela" },
          h(
            "thead",
            null,
            h(
              "tr",
              null,
              h("th", null, "modelo"),
              h("th", null, "provider"),
              h("th", null, "complexidade"),
              h("th", null, "tarefas"),
              h("th", null, "acesso"),
              h("th", null, "esforço")
            )
          ),
          h(
            "tbody",
            null,
            (p.catalogo || []).map(function (m) {
              const cx = m.complexidade || [];
              const cxLista = Array.isArray(cx) ? cx : [cx];
              return h(
                "tr",
                { key: m.id },
                h("td", { className: "vmm-mono" }, m.id),
                h("td", { className: "vmm-mono vmm-mudo" }, m.provider + " / " + m.modelo),
                h("td", null, cxLista.map(function (n) { return h(Chip, { key: n }, n); })),
                h("td", null, (m.tarefas || []).length
                  ? (m.tarefas || []).map(function (t) { return h(Chip, { key: t, tone: "outline" }, t); })
                  : h("span", { className: "vmm-mudo" }, "—")),
                h("td", null, h(Chip, { tone: m.acesso === "subscricao" ? "outline" : "secondary" }, m.acesso || "api")),
                h("td", null, m.esforco
                  ? h(Chip, { tone: "outline" }, m.esforco)
                  : h("span", { className: "vmm-mudo" }, "—"))
              );
            })
          )
        )
      )
    );
  }

  function TabelaLog(props) {
    const linhas = props.linhas || [];
    if (!linhas.length) return h("div", { className: "vmm-mudo" }, "sem registos");
    return h(
      "table",
      { className: "vmm-tabela vmm-log" },
      h(
        "thead",
        null,
        h(
          "tr",
          null,
          h("th", null, "quando"),
          h("th", null, "perfil"),
          h("th", null, "modelo"),
          h("th", null, "provider"),
          h("th", null, "nivel"),
          h("th", null, "origem"),
          h("th", null, "superficie"),
          h("th", null, "sessao")
        )
      ),
      h(
        "tbody",
        null,
        linhas.map(function (l, i) {
          return h(
            "tr",
            { key: (l.ts || "") + i },
            h("td", { className: "vmm-nowrap" }, hora(l.ts)),
            h("td", null, h(Chip, { tone: "outline" }, l.perfil || "—")),
            h("td", { className: "vmm-mono" }, l.modelo || "—"),
            h("td", { className: "vmm-mono vmm-mudo" }, l.provider || "—"),
            h("td", null, l.nivel || "—"),
            h("td", null, l.origem || "—"),
            h("td", { className: "vmm-mudo" }, l.superficie || "—"),
            h("td", { className: "vmm-mono vmm-mudo vmm-curto" }, l.sessao || "—")
          );
        })
      )
    );
  }

  // ---- classificador da complexidade (dentro do editor) -------------------
  // O bloco vive em `analise.classificador` (global) e em
  // `perfis.<nome>.analise.classificador` (por perfil). Edita-se o JSON que ja
  // esta no editor: nao ha rota nova, logo nao exige reiniciar o gateway —
  // grava-se pelo mesmo /config, depois de validado.
  const CLASSIF_OFF = { activo: false, provider: "", modelo: "" };

  function lerJSON(texto) {
    try {
      const c = JSON.parse(texto);
      if (!c || typeof c !== "object" || Array.isArray(c)) return null;
      return c;
    } catch (e) {
      return null;
    }
  }

  function blocoDe(cfg, perfil) {
    if (perfil === null) {
      return (cfg.analise && cfg.analise.classificador) || null;
    }
    const p = (cfg.perfis || {})[perfil] || {};
    return (p.analise && p.analise.classificador) || null;
  }

  // perfil === null -> global; bloco === null -> nao declarado (herda)
  function comBloco(cfg, perfil, bloco) {
    const novo = JSON.parse(JSON.stringify(cfg));
    if (perfil === null) {
      novo.analise = novo.analise || {};
      if (bloco === null) delete novo.analise.classificador;
      else novo.analise.classificador = bloco;
      return novo;
    }
    novo.perfis = novo.perfis || {};
    novo.perfis[perfil] = novo.perfis[perfil] || {};
    const p = novo.perfis[perfil];
    if (bloco === null) {
      if (p.analise) {
        delete p.analise.classificador;
        if (!Object.keys(p.analise).length) delete p.analise;
      }
      return novo;
    }
    p.analise = p.analise || {};
    p.analise.classificador = bloco;
    return novo;
  }

  function paresModelo(cfg) {
    const vistos = {};
    const saida = [];
    Object.keys(cfg.perfis || {}).forEach(function (n) {
      const cat = (cfg.perfis[n] || {}).catalogo || [];
      cat.forEach(function (m) {
        if (!m || !m.provider || !m.modelo) return;
        const chave = m.provider + "/" + m.modelo;
        if (vistos[chave]) return;
        vistos[chave] = true;
        saida.push({ provider: m.provider, modelo: m.modelo });
      });
    });
    return saida;
  }

  //: Chaves cujo valor vazio TEM significado proprio. `temperatura: null` quer
  //: dizer "nao enviar o campo" (modelos de raciocinio recusam-no), portanto
  //: nao pode cair no mesmo balde do `esforco: ""`.
  var VAZIO_COM_SENTIDO = ["temperatura"];

  // Reconstroi o bloco `analise.classificador` a partir do que la esta, em vez
  // de enumerar campos. A lista fixa de chaves foi o mesmo defeito tres vezes:
  // primeiro perdia `timeout_s`/`max_tokens`/`janela_mensagens`, depois
  // `via`/`estado`/`confianca_min`, agora `preset`/`temperatura`/
  // `parametros_extra`. Uma lista de chaves nunca acompanha o classificador;
  // copiar tudo e sobrepor o que muda acompanha sempre.
  function baseDoBloco(actual) {
    var fonte = actual || {};
    var novo = {
      activo: !!fonte.activo,
      provider: fonte.provider || "",
      modelo: fonte.modelo || "",
    };
    Object.keys(fonte).forEach(function (k) {
      if (k === "activo" || k === "provider" || k === "modelo") return;
      var v = fonte[k];
      if (VAZIO_COM_SENTIDO.indexOf(k) >= 0) {
        if (Object.prototype.hasOwnProperty.call(fonte, k)) novo[k] = v;
        return;
      }
      // Um campo vazio deixado no JSON era validado como valor invalido:
      // `esforco: ""` e `max_chars: ""` tem de sair, nao ficar.
      if (v === undefined || v === "" || v === null) return;
      novo[k] = v;
    });
    return novo;
  }

  function SecaoClassificador(props) {
    const cfg = lerJSON(props.configJSON);
    if (!cfg) {
      return h("div", { className: "vmm-classif" },
        h("div", { className: "vmm-mudo" }, "Classificador por modelo: corrija o JSON para poder editar aqui."));
    }
    const pares = paresModelo(cfg);
    const globalBloco = blocoDe(cfg, null);
    const linhas = [null].concat(Object.keys(cfg.perfis || {}).sort());

    function aplicar(perfil, bloco) {
      props.onMudar(JSON.stringify(comBloco(cfg, perfil, bloco), null, 2));
    }

    function mudarModo(perfil, modo) {
      const actual = blocoDe(cfg, perfil) || {};
      if (modo === "herdar") return aplicar(perfil, null);
      if (modo === "desligado") {
        const desligado = baseDoBloco(actual);
        desligado.activo = false;
        return aplicar(perfil, desligado);
      }
      const primeiro = pares[0] || { provider: "", modelo: "" };
      const ligado = baseDoBloco(actual);
      ligado.activo = true;
      ligado.provider = actual.provider || primeiro.provider;
      ligado.modelo = actual.modelo || primeiro.modelo;
      aplicar(perfil, ligado);
    }

    function mudarCampo(perfil, campo, valor) {
      const actual = blocoDe(cfg, perfil) || CLASSIF_OFF;
      const novo = baseDoBloco(actual);
      // O esforco sai do JSON quando volta a "nao declarado" — fica explicito
      // porque o `baseDoBloco` copiaria um `esforco` declarado.
      if (campo === "esforco" && !valor) delete novo.esforco;
      else novo[campo] = valor;
      aplicar(perfil, novo);
    }

    return h("div", { className: "vmm-classif" },
      h("div", { className: "vmm-classif-titulo" },
        h("strong", null, "Análise da complexidade"),
        h("span", { className: "vmm-mudo" },
          "  sem modelo (ou desligado) decide a heurística; com modelo, decide o modelo — "
          + "a associação de tarefa continua a ganhar.")
      ),
      linhas.map(function (perfil) {
        const bloco = blocoDe(cfg, perfil);
        const modo = !bloco ? "herdar" : (bloco.activo ? "ligado" : "desligado");
        const proprio = bloco && bloco.activo;
        const herda = perfil !== null && !bloco && globalBloco && globalBloco.activo;
        return h("div", { key: perfil === null ? "@global" : perfil, className: "vmm-classif-linha" },
          h("span", { className: "vmm-classif-nome" },
            (perfil === null ? "global" : perfil),
            herda ? h("span", { className: "vmm-mudo" },
              " · herda " + globalBloco.provider + "/" + globalBloco.modelo) : null
          ),
          h("select", {
            className: "vmm-select",
            value: modo,
            onChange: function (e) { mudarModo(perfil, e.target.value); },
          },
            (perfil === null
              ? [["desligado", "desligado (heurística)"], ["ligado", "decide um modelo"]]
              : [["herdar", "herdar do global"], ["desligado", "desligado (heurística)"],
                 ["ligado", "decide um modelo"]]
            ).map(function (o) { return h("option", { key: o[0], value: o[0] }, o[1]); })
          ),
          proprio ? h("input", {
            className: "vmm-input",
            list: "vmm-modelos",
            placeholder: "provider",
            value: bloco.provider || "",
            onChange: function (e) { mudarCampo(perfil, "provider", e.target.value); },
          }) : null,
          proprio ? h("input", {
            className: "vmm-input",
            list: "vmm-pares",
            placeholder: "modelo",
            value: bloco.modelo || "",
            onChange: function (e) { mudarCampo(perfil, "modelo", e.target.value); },
          }) : null,
          proprio ? h("select", {
            className: "vmm-select",
            value: bloco.esforco || "",
            onChange: function (e) { mudarCampo(perfil, "esforco", e.target.value); },
          },
            [["", "esforço: não declarado"], ["baixo", "esforço: baixo"],
             ["medio", "esforço: médio"], ["alto", "esforço: alto"]]
              .map(function (o) { return h("option", { key: o[0], value: o[0] }, o[1]); })
          ) : null,
          proprio ? h("select", {
            className: "vmm-select",
            title: "resposta em JSON: auto pede response_format e repete sem ele se o provider recusar",
            value: bloco.formato_json || "auto",
            onChange: function (e) { mudarCampo(perfil, "formato_json", e.target.value); },
          },
            [["auto", "formato: JSON automático"], ["on", "formato: JSON obrigatório"],
             ["off", "formato: texto"]]
              .map(function (o) { return h("option", { key: o[0], value: o[0] }, o[1]); })
          ) : null
        );
      }),
      h("datalist", { id: "vmm-modelos" },
        pares.map(function (p) { return h("option", { key: "p" + p.provider, value: p.provider }); })),
      h("datalist", { id: "vmm-pares" },
        pares.map(function (p) {
          return h("option", { key: "m" + p.provider + p.modelo, value: p.modelo,
                               label: p.provider });
        }))
    );
  }

  function Pagina() {
    const [estado, setEstado] = useState(null);
    const [log, setLog] = useState([]);
    const [limite, setLimite] = useState(40);
    const [erro, setErro] = useState(null);
    const [aGravar, setAGravar] = useState(false);
    const [aCarregar, setACarregar] = useState(false);
    const [mostrarEditor, setMostrarEditor] = useState(false);
    const [configJSON, setConfigJSON] = useState("");
    // Versao do config que este editor leu: volta no "Gravar" para o servidor
    // poder recusar uma gravacao baseada em estado antigo.
    const [configRev, setConfigRev] = useState("");
    const [errosValidacao, setErrosValidacao] = useState([]);
    const [aValidar, setAValidar] = useState(false);
    // Mensagem de sucesso da ultima validacao/gravacao (null = sem aviso).
    // Distinta de "erro": verde, e limpa assim que o texto volta a mudar.
    const [validacaoOk, setValidacaoOk] = useState(null);

    const carregar = useCallback(function () {
      setACarregar(true);
      return Promise.all([
        pedir("/estado"),
        pedir("/log?limite=" + limite),
      ])
        .then(function (r) {
          setEstado(r[0]);
          setLog((r[1] && r[1].linhas) || []);
          setErro(null);
        })
        .catch(function (e) { setErro(String(e && e.message ? e.message : e)); })
        .then(function () { setACarregar(false); });
    }, [limite]);

    useEffect(function () { carregar(); }, [carregar]);

    function alternar(ambito, perfil, activo) {
      setAGravar(true);
      const corpo = { ambito: ambito, activo: activo };
      if (perfil) corpo.perfil = perfil;
      pedir("/toggle", { method: "POST", body: corpo })
        .then(function () { return carregar(); })
        .catch(function (e) { setErro(String(e && e.message ? e.message : e)); })
        .then(function () { setAGravar(false); });
    }

    // Grava so o bloco de estrategia (nunca a config inteira): um `select` nao
    // pode ter o poder de repor tudo o que outra sessao escreveu. O `rev` que
    // veio do `/estado` volta no corpo para o servidor recusar (409) uma
    // escolha feita sobre um estado que ja mudou.
    function mudarEstrategia(ambito, perfil, campos, herdar) {
      setAGravar(true);
      const corpo = { ambito: ambito, rev: (estado && estado.rev) || "" };
      if (perfil) corpo.perfil = perfil;
      if (herdar) corpo.herdar = true;
      else Object.assign(corpo, campos || {});
      pedir("/estrategia", { method: "POST", body: corpo })
        .then(function () { return carregar(); })
        .catch(function (e) {
          const msg = String(e && e.message ? e.message : e);
          setErro(msg.indexOf("409") !== -1
            ? "A estratégia mudou noutro sítio — recarreguei o estado. Escolha outra vez."
            : "Erro ao gravar a estratégia: " + msg);
          return carregar();
        })
        .then(function () { setAGravar(false); });
    }

    function carregarConfig() {
      pedir("/config")
        .then(function (r) {
          // O backend devolve {"config": ..., "rev": ...}; o rev volta no
          // "Gravar" para que esta aba nao apague o que outra escreveu.
          const c = (r && r.config) ? r.config : r;
          setConfigJSON(JSON.stringify(c, null, 2));
          setConfigRev((r && r.rev) ? String(r.rev) : "");
          setErrosValidacao([]);
        })
        .catch(function (e) { setErro("Erro ao carregar config: " + String(e && e.message ? e.message : e)); });
    }

    function validarConfig() {
      setErrosValidacao([]);
      setValidacaoOk(null);
      setAValidar(true);
      try {
        const cfg = JSON.parse(configJSON);
        // Garantia extra: o corpo deve ser um objecto (não array, não string)
        if (cfg === null || typeof cfg !== "object" || Array.isArray(cfg)) {
          throw new Error("O JSON deve ser um objecto (não array, não null, não string)");
        }
        pedir("/validar", { method: "POST", body: cfg })
          .then(function (r) {
            if (r && r.erros && r.erros.length) {
              setErrosValidacao(r.erros);
              setValidacaoOk(null);
              setErro("Configuração inválida: " + r.erros.join("; "));
            } else {
              setErro(null);
              setValidacaoOk(mensagemValido(cfg));
            }
          })
          .catch(function (e) {
            setValidacaoOk(null);
            setErro("Erro ao validar: " + String(e && e.message ? e.message : e));
          })
          .then(function () { setAValidar(false); });
      } catch (e) {
        setAValidar(false);
        setValidacaoOk(null);
        setErro("JSON inválido: " + String(e && e.message ? e.message : e));
      }
    }

    // Resume o que a validacao aceitou, para o aviso verde dizer algo medido
    // e nao apenas "ok". Conta perfis e rotas do proprio JSON validado.
    function mensagemValido(cfg) {
      const perfis = (cfg && cfg.perfis) || {};
      const nomes = Object.keys(perfis);
      let rotas = 0;
      let tarefas = 0;
      nomes.forEach(function (n) {
        const p = perfis[n] || {};
        rotas += Array.isArray(p.catalogo) ? p.catalogo.length : 0;
        tarefas += p.tarefas && typeof p.tarefas === "object" ? Object.keys(p.tarefas).length : 0;
      });
      return "Validado com sucesso — " + nomes.length + " perfil(es), " +
        rotas + " rota(s), " + tarefas + " tarefa(s), 0 erros";
    }

    function salvarConfig() {
      try {
        const cfg = JSON.parse(configJSON);
        // Garantia extra: o corpo deve ser um objecto (não array, não string)
        if (cfg === null || typeof cfg !== "object" || Array.isArray(cfg)) {
          throw new Error("O JSON deve ser um objecto (não array, não null, não string)");
        }
        // Envia-se a versao que esta aba leu. Se o config mudou entretanto
        // (outra aba, o CLI, o ciclo), o servidor recusa com 409 em vez de
        // deixar esta aba repor o passado.
        pedir("/config", { method: "POST", body: { config: cfg, rev: configRev } })
          .then(function (r) {
            setConfigRev((r && r.rev) ? String(r.rev) : "");
            setMostrarEditor(false);
            setConfigJSON("");
            setErrosValidacao([]);
            setValidacaoOk("Configuração gravada com sucesso — o gestor já a lê");
            carregar();
          })
          .catch(function (e) {
            const msg = String(e && e.message ? e.message : e);
            if (msg.indexOf("409") !== -1) {
              setValidacaoOk(null);
              setErro("A configuração foi alterada noutro sítio desde que abriu este editor "
                + "— recarreguei a versão actual. Reveja o que quer mudar e grave outra vez.");
              carregarConfig();
              carregar();
              return;
            }
            setErro("Erro ao salvar: " + msg);
          });
      } catch (e) {
        setErro("JSON inválido: " + String(e && e.message ? e.message : e));
      }
    }

    function fecharEditor() {
      setMostrarEditor(false);
      setConfigJSON("");
      setErrosValidacao([]);
      setValidacaoOk(null);
    }

    if (!estado && !erro) {
      return h("div", { className: "vmm-pagina" }, h("div", { className: "vmm-mudo" }, "a ler o estado do gestor…"));
    }

    const perfis = (estado && estado.perfis) || {};
    const nomes = Object.keys(perfis);
    // Calculado uma vez: a identidade nao muda a meio de um render.
    const meta = metaPlugin(estado);

    return h(
      "div",
      { className: "vmm-pagina" },
      h(
        "div",
        { className: "vmm-topo" },
        h(
          "div",
          null,
          h("h1", { className: "vmm-h1" }, "Model Manager"),
          h(
            "div",
            { className: "vmm-sub" },
            "Escolhe provider e modelo por complexidade do trabalho e por tipo de tarefa, dentro do catalogo de cada perfil. ",
            "O modelo que serviu cada turno fica registado em baixo."
          ),
          // Identidade do plugin. A versao vem do plugin.yaml (a mesma que o
          // loader e o catalogo leem) e nao de uma copia escrita a mao aqui.
          linhaIdentidade(meta)
        ),
        h(
          "div",
          { className: "vmm-accoes" },
          h(
            "div",
            { className: "vmm-global" },
            h(Chip, { tone: estado && estado.activo ? "default" : "secondary" },
              estado && estado.activo ? "gestor activo" : "gestor desligado"),
            h(Interruptor, {
              activo: !!(estado && estado.activo),
              aGravar: aGravar,
              onClick: function () { alternar("global", null, !(estado && estado.activo)); },
              title: "Ligar/desligar o gestor em todos os perfis",
            })
          ),
          h(Button, {
            size: "sm",
            variant: "outline",
            onClick: function() { setMostrarEditor(true); carregarConfig(); }
          },
          "Editar configuração"
        ),
        h(Button, { size: "sm", variant: "outline", onClick: carregar, disabled: aCarregar },
            aCarregar ? "a actualizar…" : "Actualizar")
        )
      ),

      // Estrategia global: o sitio onde se muda uma vez e todos os perfis
      // herdam. Fica antes dos cartoes porque e a regra que os governa — e
      // cada cartao diz se a sua e esta ou se e propria.
      h(
        Card,
        { className: "vmm-card" },
        h(
          CardContent,
          { className: "vmm-card-content" },
          h(
            "div",
            { className: "vmm-linha vmm-cabecalho-perfil" },
            h(
              "div",
              { className: "vmm-titulo-perfil" },
              h("strong", null, "estratégia global"),
              h(
                "span",
                { className: "vmm-estrategia" },
                h(Seletor, {
                  rotulo: "estratégia global",
                  valor: (estado.estrategia_global || {}).modo || "rodizio",
                  opcoes: (((estado.opcoes || {}).estrategia) || {}).modo || [],
                  disabled: aGravar,
                  onChange: function (v) { mudarEstrategia("global", null, { modo: v }); },
                }),
                h(Seletor, {
                  rotulo: "rotação global",
                  valor: (estado.estrategia_global || {}).por || "sessao",
                  opcoes: (((estado.opcoes || {}).estrategia) || {}).por || [],
                  disabled: aGravar || (estado.estrategia_global || {}).modo !== "rodizio",
                  title: "Só se aplica à rotação",
                  onChange: function (v) { mudarEstrategia("global", null, { por: v }); },
                }),
                h("span", { className: "vmm-mudo" },
                  rotuloEstrategia(estado.estrategia_global)
                  + " · os perfis sem escolha própria seguem esta")
              )
            )
          )
        )
      ),

      erro ? h("div", { className: "vmm-erro" }, "erro: " + erro) : null,

      // Aviso de sucesso fora do editor: sobrevive ao fecho depois de gravar.
      (validacaoOk && !mostrarEditor) ? h(
        "div",
        { className: "vmm-ok", role: "status", "aria-live": "polite" },
        "✓ " + validacaoOk
      ) : null,

      mostrarEditor && h(
        Card,
        { className: "vmm-card-editor" },
        h(
          CardContent,
          { className: "vmm-card-content" },
          h(
            "div",
            { className: "vmm-linha vmm-editor-cabecalho" },
            h("h3", { className: "vmm-editor-titulo" }, "Editor de Configuração"),
            h(Button, { size: "sm", variant: "ghost", onClick: fecharEditor }, "Fechar")
          ),
          h(
            "div",
            { className: "vmm-editor-corpo" },
            h(SecaoClassificador, {
              configJSON: configJSON,
              onMudar: function (novo) {
                setConfigJSON(novo);
                // o texto mudou: o "validado" anterior deixa de ser verdade
                setValidacaoOk(null);
                setErrosValidacao([]);
              },
            }),
            h("textarea", {
              value: configJSON,
              onChange: function (e) {
                setConfigJSON(e.target.value);
                // o texto mudou: o "validado" anterior deixa de ser verdade
                setValidacaoOk(null);
                setErrosValidacao([]);
              },
              className: "vmm-editor-textarea",
              style: { width: "100%", height: "350px", fontFamily: "monospace", fontSize: "12px", lineHeight: "1.5", padding: "8px", border: "1px solid var(--border)", borderRadius: "4px", resize: "vertical" }
            }),
            errosValidacao.length > 0 && h(
              "div",
              { className: "vmm-editor-erros" },
              errosValidacao.map(function (e, i) { return h("div", { key: i, className: "vmm-erro-item" }, "• " + e); })
            ),
            validacaoOk && h(
              "div",
              { className: "vmm-editor-ok", role: "status", "aria-live": "polite" },
              "✓ " + validacaoOk
            )
          ),
          h(
            "div",
            { className: "vmm-editor-accoes" },
            h(Button, {
              size: "sm",
              variant: validacaoOk ? "default" : "outline",
              onClick: validarConfig,
              disabled: aValidar,
              className: validacaoOk ? "vmm-btn-ok" : undefined,
              title: "Validar a configuração contra o schema, sem gravar"
            }, aValidar ? "A validar…" : (validacaoOk ? "✓ Validado" : "Validar")),
            h(Button, { size: "sm", variant: "default", onClick: salvarConfig, disabled: aGravar }, aGravar ? "A gravar…" : "Salvar"),
            h(Button, { size: "sm", variant: "ghost", onClick: fecharEditor }, "Cancelar")
          )
        )
      ),

      h(
        "div",
        { className: "vmm-grelha" },
        nomes.map(function (n) {
          return h(CartaoPerfil, {
            key: n,
            nome: n,
            dados: perfis[n],
            aGravar: aGravar,
            opcoes: ((estado.opcoes || {}).estrategia) || {},
            onToggle: function (perfil, activo) { alternar("perfil", perfil, activo); },
            onEstrategia: function (perfil, campos, herdar) {
              mudarEstrategia("perfil", perfil, campos, herdar);
            },
          });
        })
      ),

      h(
        Card,
        { className: "vmm-card" },
        h(
          CardContent,
          { className: "vmm-card-content" },
          h(
            "div",
            { className: "vmm-linha" },
            h("div", null,
              h("strong", null, "Registo de utilizacao"),
              h("span", { className: "vmm-mudo" }, "  ultimas " + limite + " respostas — modelo, provider, perfil, nivel e origem")
            ),
            h(
              "div",
              { className: "vmm-accoes" },
              [20, 40, 100, 200].map(function (n) {
                return h(Button, {
                  key: n, size: "sm",
                  variant: n === limite ? "default" : "outline",
                  onClick: function () { setLimite(n); },
                }, String(n));
              })
            )
          ),
          h(TabelaLog, { linhas: log }),
          estado && estado.ledger
            ? h("div", { className: "vmm-mudo vmm-pe" },
                estado.ledger.caminho + " · " + Math.round((estado.ledger.bytes || 0) / 1024) + " KiB")
            : null
        )
      ),

      h("div", { className: "vmm-pe vmm-mudo" },
        "Catalogo e associacoes de tarefa vivem em plugin-data/vipernet-model-manager/config.json — este painel permite editar a configuração. Versao do plugin: " +
        meta.versao + " · painel: " + meta.manifest +
        " · autor: " + meta.autor +
        (meta.github ? " · " + meta.github : ""))
    );
  }

  window.__HERMES_PLUGINS__.register(NOME, Pagina);

  // Exposicao para teste: as funcoes que decidem o que vai para o JSON da
  // configuracao sao puras (sem React) e sao o sitio onde um erro se paga caro
  // — escrevem no bloco que o middleware le. Testadas em node, sem browser.
  if (typeof window !== "undefined" && window.__VMM_TEST__) {
    window.__VMM_TEST__.lerJSON = lerJSON;
    window.__VMM_TEST__.blocoDe = blocoDe;
    window.__VMM_TEST__.baseDoBloco = baseDoBloco;
    window.__VMM_TEST__.comBloco = comBloco;
    window.__VMM_TEST__.paresModelo = paresModelo;
    window.__VMM_TEST__.rotuloAnalise = rotuloAnalise;
    window.__VMM_TEST__.rotuloEstrategia = rotuloEstrategia;
    window.__VMM_TEST__.opcoesEstrategia = function (estado) {
      return ((estado || {}).opcoes || {}).estrategia || {};
    };
    window.__VMM_TEST__.metaPlugin = metaPlugin;
    window.__VMM_TEST__.linhaIdentidade = linhaIdentidade;
  }
})();
