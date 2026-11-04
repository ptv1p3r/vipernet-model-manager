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

  // Aviso de config lida com erros: o ficheiro no disco tem algo que esta
  // versao nao conhece (editado a mao, ou escrito por uma versao futura) e o
  // painel mostra o valor NORMALIZADO, nao o do ficheiro. Sem este aviso o
  // utilizador grava por cima e nunca sabe o que perdeu — era o ultimo ponto
  // cego da guarda de chaves. Aparece nas duas paginas.
  function AvisoConfig(props) {
    const erros = (props.estado && props.estado.erros_config) || [];
    if (!erros.length) return null;
    return h(
      "div",
      { className: "vmm-erro vmm-erros-lista", role: "alert" },
      h(
        "div",
        null,
        "O config no disco tem " + erros.length + " problema(s) que esta versão "
          + "não reconhece — o painel está a mostrar a versão validada, que pode não "
          + "coincidir com o ficheiro, e gravar por cima vai ser recusado. "
          + "Corrija o ficheiro."
      ),
      erros.slice(0, 6).map(function (e, i) {
        return h("div", { key: i }, "• " + e);
      })
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

  // ---- ConfigEditor: edicao completa do perfil ----------------------------
  function ConfigEditor(props) {
    const [config, setConfig] = useState(props.config || {});
    const [validado, setValidado] = useState(false);

    function gravar() {
      props.onGravar && props.onGravar(config);
    }

    function cancelar() {
      props.onCancelar && props.onCancelar();
    }

    function validar() {
      setValidado(true);
    }

    return h(
      "div",
      { className: "vmm-config-editor" },
      h("h3", {}, "Configuracao do Perfil: " + (props.perfil || "global")),
      h("div", { className: "vmm-config-campos" },
        h("label", {}, "Activo"),
        h(Interruptor, { activo: !!config.activo, onClick: function() {
          setConfig(Object.assign({}, config, { activo: !config.activo }));
        }}),
        h("label", {}, "Classificador"),
        h(Seletor, {
          opcoes: props.opcoes || [],
          valor: config.classificador || "",
          onChange: function(v) {
            setConfig(Object.assign({}, config, { classificador: v }));
          }
        }),
        h("label", {}, "Estrategia"),
        h(Seletor, {
          opcoes: props.opcoesEstrategia || [],
          valor: config.estrategia || "",
          onChange: function(v) {
            setConfig(Object.assign({}, config, { estrategia: v }));
          }
        })
      ),
      h("div", { className: "vmm-config-botoes" },
        h(Button, { size: "sm", variant: "outline", onClick: validar }, "Validar"),
        h(Button, { size: "sm", variant: "default", onClick: gravar }, "Gravar"),
        h(Button, { size: "sm", variant: "secondary", onClick: cancelar }, "Cancelar")
      ),
      validado ? h("div", { className: "vmm-config-resultado" }, "Configuracao validada com sucesso.") : null
    );
  }
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

  // --- superficies de sessao ------------------------------------------------
  // O criterio que `cron` precisa nao e uma palavra no texto: e o contexto em
  // que a sessao corre (`cron`, `api_server`, `cli`, ...), que o core entrega
  // de forma deterministica. Por isso a regra e por superficie, nao por termo:
  // 7,7% das mensagens contem "cron" e a maioria nao tem nada a ver com cron.
  function opcoesAlvo(catalogo, niveis) {
    const lista = [
      { valor: "", rotulo: "— sem regra (herdar) —" },
      { valor: "__nunca", rotulo: "nunca: nao tratar esta superficie" },
    ];
    (catalogo || []).forEach(function (m) {
      lista.push({
        valor: "entrada:" + m.id,
        rotulo: m.id + " · " + m.provider + "/" + m.modelo,
      });
    });
    (niveis || []).forEach(function (n) {
      lista.push({ valor: "nivel:" + n, rotulo: "nível " + n });
    });
    return lista;
  }

  function valorDaRegra(r) {
    if (!r) return "";
    if (r.activo === false) return "__nunca";
    if (r.entrada) return "entrada:" + r.entrada;
    if (r.provider || r.modelo) return (r.provider || "") + "/" + (r.modelo || "");
    if (r.nivel) return "nivel:" + r.nivel;
    return "";
  }

  function rotuloRegra(r, catalogo) {
    if (!r) return "sem regra";
    if (r.activo === false) return "nunca";
    if (r.entrada) {
      const m = (catalogo || []).filter(function (x) { return x.id === r.entrada; })[0];
      return r.entrada + (m ? " (" + m.provider + "/" + m.modelo + ")" : " (fora do catálogo)");
    }
    if (r.nivel) return "nível " + r.nivel;
    return (r.provider || "") + "/" + (r.modelo || "");
  }

  function Superficies(props) {
    const estadoNovo = React.useState("");
    const nova = estadoNovo[0];
    const setNova = estadoNovo[1];
    const regras = props.regras || {};
    const nomes = Object.keys(regras).sort();
    const opcoes = opcoesAlvo(props.catalogo, props.niveis);
    const idLista = "vmm-sup-" + (props.perfil || "x");

    function adicionar() {
      const nome = String(nova || "").trim().toLowerCase();
      const primeiro = (props.catalogo || [])[0];
      if (!nome || !primeiro) return;
      setNova("");
      // Nasce ligada a primeira rota do catalogo: uma superficie sem regra nao
      // exprime nada, e a linha fica logo editavel para escolher outra.
      props.onMudar(nome, "entrada:" + primeiro.id);
    }

    return h(
      "div",
      { className: "vmm-superficies" },
      h("span", { className: "vmm-rotulo" }, "Superfícies de sessão"),
      nomes.length
        ? nomes.map(function (s) {
            const r = regras[s] || {};
            const herdada = r.origem_regra === "global";
            return h(
              "div",
              { className: "vmm-sup-linha", key: s },
              h(
                "span",
                {
                  className: "vmm-sup-nome",
                  title: herdada
                    ? "regra herdada do global — escolher aqui cria a do perfil"
                    : "regra deste perfil",
                },
                s,
                herdada ? h("span", { className: "vmm-mudo" }, " · global") : null
              ),
              h(Seletor, {
                valor: valorDaRegra(r),
                opcoes: opcoes,
                disabled: props.aGravar,
                rotulo: "rota para " + s,
                title: rotuloRegra(r, props.catalogo),
                onChange: function (v) { props.onMudar(s, v); },
              })
            );
          })
        : h("div", { className: "vmm-mudo" },
            "sem regras de superfície — decide a tarefa e a complexidade"),
      h(
        "div",
        { className: "vmm-sup-add" },
        h("input", {
          className: "vmm-select",
          list: idLista,
          value: nova,
          placeholder: "superfície (ex.: cron)",
          disabled: props.aGravar,
          "aria-label": "superfície a acrescentar",
          onChange: function (e) { setNova(e.target.value); },
          onKeyDown: function (e) { if (e.key === "Enter") adicionar(); },
        }),
        h("datalist", { id: idLista },
          (props.conhecidas || []).map(function (c) {
            return h("option", { key: c, value: c });
          })),
        h(Button, {
          size: "sm",
          variant: "ghost",
          disabled: props.aGravar || !String(nova || "").trim() || !(props.catalogo || []).length,
          title: "Acrescentar a superfície e depois escolher a rota na linha",
          onClick: adicionar,
        }, "acrescentar")
      )
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
      ? "rotação " + (por === "turno" ? "por turno" : "por sessão")
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

  // ---- uma rota do catalogo: pagina propria ------------------------------
  // A conversao forma <-> entrada do catalogo e pura (sem React) porque e o
  // sitio onde um erro se paga caro: escreve no JSON que o middleware le. As
  // chaves vazias nao vao para o ficheiro — declarar e o que da garantia,
  // declarar nada nao proibe nada, e um `esforco: ""` seria um valor invalido.
  function entradaParaForm(entrada) {
    var x = entrada || {};
    var cx = x.complexidade != null ? x.complexidade : x.niveis;
    if (!Array.isArray(cx)) cx = cx == null || cx === "" ? [] : [cx];
    return {
      id: x.id == null ? "" : String(x.id),
      provider: x.provider == null ? "" : String(x.provider),
      modelo: x.modelo == null ? "" : String(x.modelo),
      complexidade: cx.map(function (n) { return String(n); }),
      acesso: x.acesso == null ? "api" : String(x.acesso),
      esforco: x.esforco == null ? "" : String(x.esforco),
      tarefas: Array.isArray(x.tarefas) ? x.tarefas.map(function (t) { return String(t); }) : [],
      desligado: x.desligado === true,
      contexto_max: x.contexto_max == null ? "" : String(x.contexto_max),
      latencia_p95_ms: x.latencia_p95_ms == null ? "" : String(x.latencia_p95_ms),
      modalidades: Array.isArray(x.modalidades) ? x.modalidades.map(function (m) { return String(m); }) : [],
      json_nativo: x.json_nativo === true ? "sim" : (x.json_nativo === false ? "nao" : ""),
      custo: x.custo == null ? "" : String(x.custo),
      ordem: x.ordem == null ? "" : String(x.ordem),
      nota: x.nota == null ? "" : String(x.nota),
      // Superficies: mapa superficie -> booleano. Copia propria (nao a
      // referencia do estado) porque apagar uma chave no formulario nao pode
      // mexer no resumo que o painel mostra por tras da pagina.
      superficies: (x.superficies && typeof x.superficies === "object"
        && !Array.isArray(x.superficies)) ? Object.assign({}, x.superficies) : {},
    };
  }

  // Ordem canonica das listas fechadas: o JSON nao pode mudar de forma (nem de
  // ordem) so porque o dono clicou nos niveis por outra ordem.
  function emOrdem(lista, ordem) {
    var escolhidos = (lista || []).filter(function (x) { return (ordem || []).indexOf(x) >= 0; });
    var conhecidos = (ordem || []).filter(function (o) { return escolhidos.indexOf(o) >= 0; });
    // Um valor fora da lista actual (config antiga) nao se perde: vai no fim.
    var resto = (lista || []).filter(function (x) { return (ordem || []).indexOf(x) < 0; });
    return conhecidos.concat(resto);
  }

  function numeroOuTexto(txt) {
    var limpo = String(txt == null ? "" : txt).trim();
    if (!limpo) return null;
    var n = Number(limpo);
    return isFinite(n) ? n : limpo;  // texto invalido fica texto: o gestor diz o porque
  }

  function formParaEntrada(form, opcoes) {
    var f = form || {};
    var o = opcoes || {};
    var entrada = {
      id: String(f.id == null ? "" : f.id).trim(),
      provider: String(f.provider == null ? "" : f.provider).trim(),
      modelo: String(f.modelo == null ? "" : f.modelo).trim(),
      complexidade: emOrdem(f.complexidade, o.niveis),
      acesso: f.acesso || "api",
      tarefas: emOrdem(f.tarefas, (o.tarefas || []).map(function (t) { return t.valor; })),
    };
    if (f.esforco) entrada.esforco = String(f.esforco);
    if (f.desligado) entrada.desligado = true;
    var contexto = numeroOuTexto(f.contexto_max);
    if (contexto !== null) entrada.contexto_max = contexto;
    var latencia = numeroOuTexto(f.latencia_p95_ms);
    if (latencia !== null) entrada.latencia_p95_ms = latencia;
    var mods = emOrdem(f.modalidades, o.modalidades);
    if (mods.length) entrada.modalidades = mods;
    if (f.json_nativo === "sim") entrada.json_nativo = true;
    else if (f.json_nativo === "nao") entrada.json_nativo = false;
    // Custo e ordem entram na chave de escolha (custo, depois ordem, depois id).
    // Vazio e "nao declarado": a entrada ordena depois das que declaram custo.
    var custo = numeroOuTexto(f.custo);
    if (custo !== null) entrada.custo = custo;
    var ordemEntrada = numeroOuTexto(f.ordem);
    if (ordemEntrada !== null) entrada.ordem = ordemEntrada;
    var nota = String(f.nota == null ? "" : f.nota).trim();
    if (nota) entrada.nota = nota;
    // Superficies do modelo: so booleanos. Uma chave ausente e heranca (o
    // perfil decide); `false` e uma recusa explicita. Nao se escreve o mapa
    // quando fica vazio — declarar nada nao proibe nada.
    var mapaSup = f.superficies || {};
    var limpoSup = {};
    Object.keys(mapaSup).forEach(function (k) {
      if (typeof mapaSup[k] === "boolean") limpoSup[k] = mapaSup[k];
    });
    if (Object.keys(limpoSup).length) entrada.superficies = limpoSup;
    return entrada;
  }

  // Alterna um valor numa lista fechada. Nao deixa escolher fora das opcoes: o
  // valor que entra e sempre um dos que a lista mostrou.
  function alternarOpcao(lista, valor, meter) {
    var actual = lista || [];
    if (meter) return actual.indexOf(valor) >= 0 ? actual.slice() : actual.concat([valor]);
    return actual.filter(function (x) { return x !== valor; });
  }

  function Campo(props) {
    return h(
      "div",
      { className: "vmm-campo" },
      h("span", { className: "vmm-campo-rotulo" }, props.rotulo),
      props.children,
      props.ajuda ? h("span", { className: "vmm-mudo vmm-campo-ajuda" }, props.ajuda) : null
    );
  }

  function EntradaTexto(props) {
    return h("input", {
      className: "vmm-input",
      type: props.tipo || "text",
      inputMode: props.tipo === "number" ? "numeric" : undefined,
      value: props.valor == null ? "" : String(props.valor),
      placeholder: props.placeholder || "",
      "aria-label": props.rotulo || "",
      onChange: function (e) { props.onChange(e.target.value); },
    });
  }

  // Lista fechada de escolha multipla: botoes com o estado a vista (aria-pressed)
  // em vez de um `<select multiple>`, que em teclado e rato e um mau negocio.
  function BotoesOpcao(props) {
    const escolhidos = props.valor || [];
    return h(
      "div",
      { className: "vmm-opcoes", role: "group", "aria-label": props.rotulo || "" },
      (props.opcoes || []).map(function (o) {
        const activo = escolhidos.indexOf(o) >= 0;
        return h(
          Button,
          {
            key: o,
            type: "button",
            size: "sm",
            variant: activo ? "default" : "outline",
            className: "vmm-opcao" + (activo ? " vmm-opcao-on" : ""),
            "aria-pressed": activo ? "true" : "false",
            onClick: function () { props.onChange(alternarOpcao(escolhidos, o, !activo)); },
          },
          o
        );
      })
    );
  }

  function opcoesDeLista(valores, vazio) {
    const lista = [];
    if (vazio) lista.push({ valor: "", rotulo: vazio });
    (valores || []).forEach(function (v) { lista.push({ valor: v, rotulo: v }); });
    return lista;
  }

  function PaginaModelo(props) {
    const form = props.form || {};
    const opcoes = props.opcoes || {};
    const meta = metaPlugin(props.estado || {});
    const novo = !!props.novo;
    const perfil = props.perfil || "";
    const titulo = novo ? "Novo modelo" : "Modelo";
    const mudar = function (campo) {
      return function (valor) { props.onCampo(campo, valor); };
    };
    const listaTarefas = (opcoes.tarefas || []);
    // Superficies do modelo: tri-estado por superficie — herdar (nao escreve
    // nada), pode servir (true), nao serve (false). A heranca e a ausencia da
    // chave; gravar o valor herdado como se fosse proprio matava a heranca.
    const mapaSup = form.superficies || {};
    const mudarSup = function (sup, valor) {
      var novoMapa = Object.assign({}, mapaSup);
      if (valor === "herdar") delete novoMapa[sup];
      else novoMapa[sup] = (valor === "sim");
      props.onCampo("superficies", novoMapa);
    };
    const opcoesTri = [
      { valor: "herdar", rotulo: "herdar do perfil" },
      { valor: "sim", rotulo: "pode servir" },
      { valor: "nao", rotulo: "não serve" },
    ];

    return h(
      "div",
      { className: "vmm-pagina vmm-pagina-modelo" },
      h(
        "div",
        { className: "vmm-topo" },
        h(
          "div",
          null,
          h("h1", { className: "vmm-h1" }, titulo + (novo ? " · " + perfil : " · " + (form.id || props.id || "—"))),
          h(
            "div",
            { className: "vmm-sub" },
            novo
              ? "Uma rota nova no catálogo do perfil " + perfil + ". Só entra no catálogo depois de gravar; "
                + "o id é o nome por que as superfícies e as tarefas lhe chamam."
              : "Todas as opções desta rota do catálogo do perfil " + perfil + ". "
                + "Gravar volta ao painel e passa a valer no turno seguinte."
          ),
          linhaIdentidade(meta)
        ),
        h(
          "div",
          { className: "vmm-accoes" },
          h(Button, {
            size: "sm",
            variant: "outline",
            onClick: props.onValidar,
            disabled: props.aGravar || props.aCarregar,
            title: "Validar esta rota contra o schema, sem gravar",
          }, props.aValidar ? "A validar…" : "Validar"),
          h(Button, {
            size: "sm",
            variant: "default",
            className: props.validacaoOk ? "vmm-btn-ok" : undefined,
            onClick: props.onGravar,
            disabled: props.aGravar || props.aCarregar || !String(form.id || "").trim(),
            title: "Validar, gravar e voltar ao painel",
          }, props.aGravar ? "A gravar…" : (props.validacaoOk ? "✓ Gravar e voltar" : "Gravar e voltar")),
          h(Button, {
            size: "sm",
            variant: "ghost",
            onClick: props.onDescartar,
            title: "Voltar ao painel sem gravar as alterações",
          }, "Descartar")
        )
      ),

      props.erro ? h("div", { className: "vmm-erro" }, "erro: " + props.erro) : null,

      props.validacaoOk
        ? h("div", { className: "vmm-ok", role: "status", "aria-live": "polite" }, "✓ " + props.validacaoOk)
        : null,

      (props.erros || []).length
        ? h(
            "div",
            { className: "vmm-erro vmm-erros-lista" },
            h("strong", null, "O gestor recusou:"),
            props.erros.map(function (e, i) { return h("div", { key: i }, "• " + e); })
          )
        : null,

      props.aCarregar
        ? h("div", { className: "vmm-mudo" }, "a ler a rota do catálogo…")
        : h(
            "div",
            { className: "vmm-modelo-grelha" },

            h(
              "div",
              { className: "vmm-bloco" },
              h("div", { className: "vmm-bloco-titulo" }, h("strong", null, "Identidade")),
              h(
                "div",
                { className: "vmm-modelo-campos" },
                h(Campo, { rotulo: "id", ajuda: novo ? "obrigatório; único dentro do perfil" : "renomear parte as regras de superfície que lhe chamam pelo nome" },
                  h(EntradaTexto, { valor: form.id, rotulo: "id da rota", placeholder: "ex.: deepseek-flash", onChange: mudar("id") })),
                h(Campo, { rotulo: "provider", ajuda: "lista fechada: os providers que a configuração já usa" },
                  h(Seletor, {
                    valor: form.provider,
                    rotulo: "provider",
                    opcoes: (opcoes.providers || []).map(function (p) { return { valor: p, rotulo: p }; }),
                    onChange: mudar("provider"),
                  })),
                h(Campo, { rotulo: "modelo", ajuda: "o id exacto do modelo no provider" },
                  h(EntradaTexto, { valor: form.modelo, rotulo: "modelo", placeholder: "nvidia/nemotron-3.5-lightning:free", onChange: mudar("modelo") }))
              )
            ),

            h(
              "div",
              { className: "vmm-bloco" },
              h("div", { className: "vmm-bloco-titulo" }, h("strong", null, "Roteamento")),
              h(
                "div",
                { className: "vmm-modelo-campos" },
                h(Campo, { rotulo: "complexidade", ajuda: "os níveis de turno que esta rota serve (pelo menos um)" },
                  h(BotoesOpcao, {
                    rotulo: "complexidade",
                    opcoes: opcoes.niveis || [],
                    valor: form.complexidade || [],
                    onChange: props.onCampoComplexidade,
                  })),
                h(Campo, { rotulo: "tarefas", ajuda: listaTarefas.length ? "uma tarefa associada vence a análise de complexidade" : "este perfil não declara tarefas — vêem do JSON do perfil" },
                  listaTarefas.length
                    ? h(BotoesOpcao, {
                        rotulo: "tarefas",
                        opcoes: listaTarefas.map(function (t) { return t.valor; }),
                        valor: form.tarefas || [],
                        onChange: props.onCampoTarefas,
                      })
                    : h("span", { className: "vmm-mudo" }, "— sem tarefas declaradas neste perfil —")),
                h(Campo, { rotulo: "acesso", ajuda: "subscrição exige provider openai-codex" },
                  h(Seletor, {
                    rotulo: "acesso",
                    opcoes: opcoesDeLista(opcoes.acessos, null),
                    valor: form.acesso,
                    onChange: mudar("acesso"),
                  })),
                h(Campo, { rotulo: "esforço", ajuda: "não declarado não é médio: é «não pedir nada»" },
                  h(Seletor, {
                    rotulo: "esforço",
                    opcoes: opcoesDeLista(opcoes.esforcos, "— não declarado —"),
                    valor: form.esforco,
                    onChange: mudar("esforco"),
                  })),
                h(Campo, { rotulo: "estado", ajuda: "desligado mantém a rota no catálogo mas fora da escolha" },
                  h(Seletor, {
                    rotulo: "estado",
                    opcoes: [{ valor: "nao", rotulo: "activo" }, { valor: "sim", rotulo: "desligado" }],
                    valor: form.desligado ? "sim" : "nao",
                    onChange: function (v) { props.onCampo("desligado", v === "sim"); },
                  })),
                h(Campo, { rotulo: "custo", ajuda: "preço por milhão de tokens; 0 é grátis. Em branco não é grátis: ordena depois das que declaram custo" },
                  h(EntradaTexto, { tipo: "number", valor: form.custo, rotulo: "custo", placeholder: "0", onChange: mudar("custo") })),
                h(Campo, { rotulo: "ordem", ajuda: "desempate entre custos iguais; o id fecha o empate, por isso a posição no ficheiro nunca conta" },
                  h(EntradaTexto, { tipo: "number", valor: form.ordem, rotulo: "ordem", placeholder: "0", onChange: mudar("ordem") }))
              )
            ),

            h(
              "div",
              { className: "vmm-bloco" },
              h("div", { className: "vmm-bloco-titulo" }, h("strong", null, "Superfícies")),
              h("div", { className: "vmm-mudo vmm-campo-ajuda" },
                "Quem serve a superfície continua a ser o perfil; aqui só se diz se ESTE modelo a pode servir. "
                + "«Herdar do perfil» não escreve nada — é a ausência da regra que herda."),
              h(
                "div",
                { className: "vmm-modelo-campos" },
                (opcoes.superficies || []).map(function (sup) {
                  var v = mapaSup[sup];
                  return h(Campo, {
                    rotulo: sup,
                    ajuda: v === undefined ? "herda do perfil" : (v ? "pode servir" : "não serve"),
                  }, h(Seletor, {
                    rotulo: "superfície " + sup,
                    opcoes: opcoesTri,
                    valor: v === undefined ? "herdar" : (v ? "sim" : "nao"),
                    onChange: function (val) { mudarSup(sup, val); },
                  }));
                }),
                h(Campo, { rotulo: "todas as outras", ajuda: "«não serve» aqui fecha o modelo às superfícies que não estão marcadas «pode servir»" },
                  h(Seletor, {
                    rotulo: "superfícies não nomeadas",
                    opcoes: opcoesTri,
                    valor: mapaSup["*"] === undefined ? "herdar" : (mapaSup["*"] ? "sim" : "nao"),
                    onChange: function (val) { mudarSup("*", val); },
                  }))
              )
            ),

            h(
              "div",
              { className: "vmm-bloco" },
              h("div", { className: "vmm-bloco-titulo" }, h("strong", null, "Limites declarados")),
              h("div", { className: "vmm-mudo vmm-campo-ajuda" },
                "Opcionais. Ausente é «não declarado» — a rota continua utilizável. Declarar é o que a triagem "
                + "usa para recusar uma rota impossível (contexto, modalidade, JSON, latência)."),
              h(
                "div",
                { className: "vmm-modelo-campos" },
                h(Campo, { rotulo: "contexto máximo (tokens)", ajuda: "acima disto a rota não recebe o pedido" },
                  h(EntradaTexto, { tipo: "number", valor: form.contexto_max, rotulo: "contexto máximo", placeholder: "128000", onChange: mudar("contexto_max") })),
                h(Campo, { rotulo: "latência p95 (ms)", ajuda: "acima do teto do perfil a rota é preterida" },
                  h(EntradaTexto, { tipo: "number", valor: form.latencia_p95_ms, rotulo: "latência p95", placeholder: "900", onChange: mudar("latencia_p95_ms") })),
                h(Campo, { rotulo: "modalidades", ajuda: "o que a rota aceita receber" },
                  h(BotoesOpcao, {
                    rotulo: "modalidades",
                    opcoes: opcoes.modalidades || [],
                    valor: form.modalidades || [],
                    onChange: props.onCampoModalidades,
                  })),
                h(Campo, { rotulo: "JSON nativo", ajuda: "«não» é uma afirmação: recusa pedidos que exigem JSON" },
                  h(Seletor, {
                    rotulo: "JSON nativo",
                    opcoes: [{ valor: "", rotulo: "— não declarado —" },
                             { valor: "sim", rotulo: "sim" }, { valor: "nao", rotulo: "não" }],
                    valor: form.json_nativo,
                    onChange: mudar("json_nativo"),
                  })),
                h(Campo, { rotulo: "nota", ajuda: "texto livre do dono, lido no painel" },
                  h(EntradaTexto, { valor: form.nota, rotulo: "nota", placeholder: "porque está aqui esta rota", onChange: mudar("nota") }))
              )
            )
          )
    );
  }

  // ---- pagina de um perfil ------------------------------------------------
  // Gemea da pagina de um modelo: um icone por perfil abre uma pagina propria
  // com TUDO o que o perfil declara. As rotas do catalogo continuam a ter
  // pagina propria, por modelo; aqui edita-se o resto — activacao, tarefas,
  // limites, classificador e estrategia.
  //
  // A distincao que a pagina tem de manter a vista e DECLARADO vs HERDADO. Um
  // perfil sem bloco de classificador herda o global; gravar o valor herdado
  // como se fosse proprio fixa o perfil ao valor do dia e a heranca morre em
  // silencio — ja aconteceu neste plugin. Por isso o formulario tem um selector
  // "herdar / declarar" e nao campos preenchidos com o que esta em vigor.

  // Uma so descricao dos campos do classificador: a pagina desenha-se a partir
  // dela e os testes contam-na. Acrescentar uma chave aqui faz aparecer o campo
  // sem tocar no resto — e um campo que o `analise_modelo.PADRAO` nao conheca
  // seria recusado na gravacao, por isso as chaves sao as dele.
  const CAMPOS_CLASSIFICADOR = [
    { chave: "provider", rotulo: "provider", tipo: "seletor", lista: "providers",
      ajuda: "quem serve o classificador; o provider tem de existir neste perfil" },
    { chave: "modelo", rotulo: "modelo", tipo: "texto",
      ajuda: "id exacto no provider (o Jev leva o til: ~typesafe/jev-latest)" },
    { chave: "via", rotulo: "via", tipo: "seletor", lista: "vias",
      ajuda: "«auto» descobre o transporte pelo preset do modelo" },
    { chave: "preset", rotulo: "preset", tipo: "seletor", lista: "presets",
      ajuda: "forma do pedido; vazio = inferir do id do modelo" },
    { chave: "estado", rotulo: "estado", tipo: "seletor", lista: "estados",
      ajuda: "que estado acompanha o pedido — medidas, nao transcricao" },
    { chave: "esforco", rotulo: "esforço", tipo: "seletor", lista: "esforcos",
      ajuda: "esforço de raciocínio pedido ao modelo" },
    { chave: "formato_json", rotulo: "formato JSON", tipo: "seletor", lista: "formatos_json",
      ajuda: "«auto» tenta JSON nativo e degrada para texto" },
    { chave: "confianca_min", rotulo: "piso de confiança", tipo: "numero",
      ajuda: "abaixo disto o classificador abstém-se; 0 = nunca por confiança" },
    { chave: "temperatura", rotulo: "temperatura", tipo: "numero",
      ajuda: "0 = determinístico; modelos de raciocínio recusam o campo" },
    { chave: "timeout_s", rotulo: "timeout (s)", tipo: "numero",
      ajuda: "acima disto o turno segue pela heurística" },
    { chave: "max_tokens", rotulo: "max tokens", tipo: "numero",
      ajuda: "orçamento da resposta do classificador" },
    { chave: "max_tokens_campo", rotulo: "nome do campo de tokens", tipo: "texto",
      ajuda: "alguns modelos usam max_completion_tokens" },
    { chave: "cache_ttl_s", rotulo: "cache do classificador (s)", tipo: "numero",
      ajuda: "cache do classificador, não do provider" },
    { chave: "janela_mensagens", rotulo: "janela de mensagens", tipo: "numero",
      ajuda: "quantas mensagens anteriores entram no pedido" },
    { chave: "max_chars", rotulo: "máximo de caracteres", tipo: "numero",
      ajuda: "tecto do texto enviado ao classificador" },
    { chave: "prompt_variante", rotulo: "variante do prompt", tipo: "texto",
      ajuda: "só o transporte chat a usa; em decisões a rubrica vai no criteria" },
    { chave: "caminho", rotulo: "caminho (chat)", tipo: "texto",
      ajuda: "vazio = /chat/completions do provider" },
    { chave: "caminho_decisoes", rotulo: "caminho (decisões)", tipo: "texto",
      ajuda: "vazio = /api/alpha/decisions" },
  ];

  // Chaves que sao objectos livres e nao cabem num campo simples: editam-se em
  // JSON, com o valor actual a vista. Escondê-las seria pior — o formulario
  // diria que mostra \"todas as opcoes\" e estaria a mentir.
  const CAMPOS_JSON_CLASSIFICADOR = [
    { chave: "parametros_extra", rotulo: "parâmetros extra", tipo: "json",
      ajuda: "objecto JSON fundido no corpo do pedido" },
    { chave: "headers_extra", rotulo: "headers extra", tipo: "json",
      ajuda: "objecto JSON de headers do pedido" },
  ];

  function vazio(v) {
    return v === null || v === undefined || v === "";
  }

  // `declarado` -> formulario. Numeros ficam vazios quando nao ha valor (para o
  // campo poder dizer \"nao declarado\" em vez de um 0 inventado), e os objectos
  // viram texto JSON legivel.
  function perfilParaForm(r) {
    r = r || {};
    const cls = (r.classificador && typeof r.classificador === "object") ? r.classificador : null;
    const est = (r.estrategia && typeof r.estrategia === "object") ? r.estrategia : null;
    const tp = (r.tarefa_persistente && typeof r.tarefa_persistente === "object") ? r.tarefa_persistente : null;
    const tarefas = Object.keys(r.tarefas || {}).map(function (id) {
      const t = (r.tarefas || {})[id] || {};
      return {
        id: id,
        termos: (t.termos || []).join(", "),
        prioridade: t.prioridade == null ? 0 : t.prioridade,
      };
    }).sort(function (a, b) { return a.id.localeCompare(b.id); });

    const campos = {};
    CAMPOS_CLASSIFICADOR.concat(CAMPOS_JSON_CLASSIFICADOR).forEach(function (c) {
      const v = cls ? cls[c.chave] : undefined;
      if (c.tipo === "json") {
        campos[c.chave] = (v && typeof v === "object" && Object.keys(v).length)
          ? JSON.stringify(v, null, 2) : "";
      } else {
        campos[c.chave] = vazio(v) ? "" : String(v);
      }
    });

    return {
      activo: !!r.activo,
      latencia_max_ms: r.latencia_max_ms ? String(r.latencia_max_ms) : "",
      tarefas: tarefas,
      clsHerdar: !cls,
      clsActivo: cls ? !!cls.activo : true,
      cls: campos,
      estHerdar: !est,
      estModo: est && est.modo ? String(est.modo) : "",
      estPor: est && est.por ? String(est.por) : "",
      // O bloco da tarefa persistente so mostra o que este perfil DECLARA: os
      // tres campos vazios querem dizer "herda essa chave", nao "herda tudo".
      tpHerdar: !tp,
      tpActivo: tp ? tp.activo !== false : true,
      tpTurnos: tp && tp.expira_turnos != null ? String(tp.expira_turnos) : "",
      tpMin: tp && tp.expira_min != null ? String(tp.expira_min) : "",
    };
  }

  // formulario -> corpo do POST. Devolve `{corpo, erro}`: um numero que nao e
  // numero ou um JSON que nao abre param aqui, com a mensagem do campo, em vez
  // de irem partir na validacao do gestor sem se saber qual.
  function formParaPerfil(form, perfil, rev) {
    const f = form || {};
    const corpo = { perfil: perfil, rev: rev || "" };
    const erro = [];

    corpo.activo = !!f.activo;

    const lat = String(f.latencia_max_ms == null ? "" : f.latencia_max_ms).trim();
    if (lat === "") {
      corpo.latencia_max_ms = 0;
    } else if (!/^\d+$/.test(lat)) {
      erro.push("latência máxima: tem de ser um inteiro em ms (0 = sem teto)");
    } else {
      corpo.latencia_max_ms = parseInt(lat, 10);
    }

    const tarefas = {};
    (f.tarefas || []).forEach(function (t, i) {
      const id = String(t.id || "").trim();
      if (!id) {
        erro.push("tarefa #" + (i + 1) + ": falta o id");
        return;
      }
      if (tarefas[id]) {
        erro.push("tarefa «" + id + "»: id repetido");
        return;
      }
      const pr = String(t.prioridade == null ? "" : t.prioridade).trim();
      if (pr !== "" && !/^-?\d+$/.test(pr)) {
        erro.push("tarefa «" + id + "»: a prioridade tem de ser um inteiro");
        return;
      }
      const termos = String(t.termos || "")
        .split(",")
        .map(function (x) { return x.trim(); })
        .filter(function (x) { return x; });
      tarefas[id] = { termos: termos, prioridade: pr === "" ? 0 : parseInt(pr, 10) };
    });
    corpo.tarefas = tarefas;

    if (f.clsHerdar) {
      corpo.classificador = "herdar";
    } else {
      const cls = { activo: !!f.clsActivo };
      CAMPOS_CLASSIFICADOR.forEach(function (c) {
        const bruto = (f.cls || {})[c.chave];
        const txt = String(bruto == null ? "" : bruto).trim();
        if (c.tipo === "numero") {
          if (txt === "") return;                       // ausente = o padrao decide
          const n = Number(txt.replace(",", "."));
          if (!isFinite(n)) {
            erro.push(c.rotulo + ": tem de ser um número");
            return;
          }
          cls[c.chave] = n;
        } else if (txt !== "") {
          cls[c.chave] = txt;
        }
      });
      CAMPOS_JSON_CLASSIFICADOR.forEach(function (c) {
        const txt = String((f.cls || {})[c.chave] || "").trim();
        if (txt === "") return;
        try {
          const obj = JSON.parse(txt);
          if (!obj || typeof obj !== "object" || Array.isArray(obj)) {
            erro.push(c.rotulo + ": tem de ser um objecto JSON");
            return;
          }
          cls[c.chave] = obj;
        } catch (e) {
          erro.push(c.rotulo + ": JSON inválido (" + e.message + ")");
        }
      });
      corpo.classificador = cls;
    }

    if (f.estHerdar) {
      corpo.estrategia = "herdar";
    } else {
      corpo.estrategia = {};
      if (f.estModo) corpo.estrategia.modo = f.estModo;
      if (f.estPor) corpo.estrategia.por = f.estPor;
    }

    if (f.tpHerdar) {
      corpo.tarefa_persistente = "herdar";
    } else {
      const tp = { activo: !!f.tpActivo };
      // Vazio = "herda esta chave". O limite e o mesmo que o `gestor` aplica:
      // recusar aqui dá a mensagem junto ao campo, em vez de um 400 anónimo.
      const turnos = String(f.tpTurnos == null ? "" : f.tpTurnos).trim();
      if (turnos !== "") {
        if (!/^\d+$/.test(turnos) || Number(turnos) < 1 || Number(turnos) > 50) {
          erro.push("tarefa persistente: o prazo em turnos tem de ser um inteiro de 1 a 50");
        } else {
          tp.expira_turnos = parseInt(turnos, 10);
        }
      }
      const minutos = String(f.tpMin == null ? "" : f.tpMin).trim();
      if (minutos !== "") {
        if (!/^\d+$/.test(minutos) || Number(minutos) < 1 || Number(minutos) > 1440) {
          erro.push("tarefa persistente: o prazo em minutos tem de ser um inteiro de 1 a 1440");
        } else {
          tp.expira_min = parseInt(minutos, 10);
        }
      }
      corpo.tarefa_persistente = tp;
    }

    return { corpo: corpo, erro: erro };
  }

  function PaginaPerfil(props) {
    const form = props.form || {};
    const opcoes = props.opcoes || {};
    const meta = metaPlugin(props.estado || {});
    const perfil = props.perfil || "";
    const efectivo = props.efectivo || {};
    const global = props.global || {};

    const opc = function (lista, sub) {
      const fonte = sub ? ((opcoes[lista] || {})[sub] || []) : (opcoes[lista] || []);
      return fonte.map(function (o) {
        return { valor: String(o.valor), rotulo: o.rotulo || String(o.valor) };
      });
    };

    const mudar = function (campo) {
      return function (valor) { props.onCampo(campo, valor); };
    };
    const mudarCls = function (campo) {
      return function (valor) { props.onCampoCls(campo, valor); };
    };

    const tarefas = form.tarefas || [];
    const cls = form.cls || {};
    const estrategiaEfectiva = efectivo.estrategia || {};
    const tpEfectiva = efectivo.tarefa_persistente || {};

    return h(
      "div",
      { className: "vmm-pagina vmm-pagina-perfil" },
      h(
        "div",
        { className: "vmm-topo" },
        h(
          "div",
          null,
          h("h1", { className: "vmm-h1" }, "Perfil · " + perfil),
          h(
            "div",
            { className: "vmm-sub" },
            "Tudo o que este perfil declara: activação, tarefas, limites, classificador, tarefa persistente e estratégia. "
              + "As rotas do catálogo têm página própria, por modelo. "
              + "Gravar volta ao painel e passa a valer no turno seguinte."
          ),
          linhaIdentidade(meta)
        ),
        h(
          "div",
          { className: "vmm-accoes" },
          h(Button, {
            size: "sm",
            variant: "outline",
            onClick: props.onValidar,
            disabled: props.aGravar || props.aCarregar,
            title: "Validar este perfil contra o schema, sem gravar",
          }, props.aValidar ? "A validar…" : "Validar"),
          h(Button, {
            size: "sm",
            variant: "default",
            className: props.validacaoOk ? "vmm-btn-ok" : undefined,
            onClick: props.onGravar,
            disabled: props.aGravar || props.aCarregar,
            title: "Validar, gravar e voltar ao painel",
          }, props.aGravar ? "A gravar…" : (props.validacaoOk ? "✓ Gravar e voltar" : "Gravar e voltar")),
          h(Button, {
            size: "sm",
            variant: "ghost",
            onClick: props.onDescartar,
            title: "Voltar ao painel sem gravar as alterações",
          }, "Descartar")
        )
      ),

      props.erro ? h("div", { className: "vmm-erro" }, "erro: " + props.erro) : null,

      props.validacaoOk
        ? h("div", { className: "vmm-ok", role: "status", "aria-live": "polite" }, "✓ " + props.validacaoOk)
        : null,

      (props.erros || []).length
        ? h(
            "div",
            { className: "vmm-erro vmm-erros-lista" },
            h("strong", null, "O gestor recusou:"),
            props.erros.map(function (e, i) { return h("div", { key: i }, "• " + e); })
          )
        : null,

      props.aCarregar
        ? h("div", { className: "vmm-mudo" }, "a ler o perfil…")
        : h(
            "div",
            { className: "vmm-perfil-grelha" },

            // --- 1. activacao e limites ------------------------------------
            h(
              "div",
              { className: "vmm-bloco" },
              h("div", { className: "vmm-bloco-titulo" }, h("strong", null, "Activação e limites")),
              h(
                "div",
                { className: "vmm-perfil-campos" },
                h(
                  "div",
                  { className: "vmm-campo" },
                  h("span", { className: "vmm-campo-rotulo" }, "gestor neste perfil"),
                  h(Seletor, {
                    rotulo: "activo",
                    valor: form.activo ? "true" : "false",
                    opcoes: [{ valor: "true", rotulo: "activo" }, { valor: "false", rotulo: "desligado" }],
                    onChange: function (v) { props.onCampo("activo", v === "true"); },
                  }),
                  h("span", { className: "vmm-mudo vmm-campo-ajuda" },
                    "desligado neste perfil, o turno segue com o modelo da sessão")
                ),
                h(Campo, {
                  rotulo: "latência máxima (ms)",
                  ajuda: "0 ou vazio = sem teto; acima disto a rota é descartada",
                }, h(EntradaTexto, {
                  valor: form.latencia_max_ms,
                  tipo: "number",
                  rotulo: "latência máxima em ms",
                  placeholder: "0",
                  onChange: mudar("latencia_max_ms"),
                }))
              ),
              h("div", { className: "vmm-mudo vmm-campo-ajuda" },
                "rotas no catálogo: " + (efectivo.n_modelos == null ? "—" : efectivo.n_modelos)
                  + " · editem-se na página de cada modelo")
            ),

            // --- 2. tarefas ------------------------------------------------
            h(
              "div",
              { className: "vmm-bloco" },
              h(
                "div",
                { className: "vmm-bloco-titulo" },
                h("strong", null, "Tarefas"),
                h("span", { className: "vmm-mudo" },
                  "  uma associação de tarefa manda sobre a complexidade e sobre a superfície")
              ),
              tarefas.length
                ? h("div", { className: "vmm-tabela-tarefas" },
                    tarefas.map(function (t, i) {
                      return h(
                        "div",
                        { className: "vmm-tarefa-linha", key: i },
                        h(EntradaTexto, {
                          valor: t.id,
                          rotulo: "id da tarefa",
                          placeholder: "arquitetura",
                          onChange: function (v) { props.onTarefa(i, "id", v); },
                        }),
                        h(EntradaTexto, {
                          valor: t.termos,
                          rotulo: "termos",
                          placeholder: "termos separados por vírgulas",
                          onChange: function (v) { props.onTarefa(i, "termos", v); },
                        }),
                        h(EntradaTexto, {
                          valor: t.prioridade,
                          tipo: "number",
                          rotulo: "prioridade",
                          placeholder: "0",
                          onChange: function (v) { props.onTarefa(i, "prioridade", v); },
                        }),
                        h(Button, {
                          size: "sm",
                          variant: "ghost",
                          className: "vmm-icone",
                          title: "Remover a tarefa " + (t.id || i + 1),
                          "aria-label": "Remover a tarefa " + (t.id || i + 1),
                          onClick: function () { props.onRemoverTarefa(i); },
                        }, "×")
                      );
                    })
                  )
                : h("div", { className: "vmm-mudo" }, "sem tarefas declaradas neste perfil"),
              h(Button, {
                size: "sm",
                variant: "outline",
                onClick: props.onNovaTarefa,
                title: "Acrescentar uma tarefa a este perfil",
              }, "+ tarefa"),
              h("span", { className: "vmm-mudo vmm-campo-ajuda" },
                " o id é o nome por que as rotas lhe chamam; só as tarefas daqui podem ser associadas")
            ),

            // --- 3. classificador ------------------------------------------
            h(
              "div",
              { className: "vmm-bloco" },
              h(
                "div",
                { className: "vmm-bloco-titulo" },
                h("strong", null, "Classificador"),
                h("span", { className: "vmm-mudo" },
                  "  em vigor: " + (efectivo.classificador
                    ? String(efectivo.classificador.provider || "?") + "/"
                      + String(efectivo.classificador.modelo || "?")
                      + " · " + String(efectivo.classificador.origem || "?")
                    : "—"))
              ),
              h(
                "div",
                { className: "vmm-perfil-campos" },
                h(Campo, {
                  rotulo: "definição",
                  ajuda: "herdar segue o global; declarar cria um bloco próprio neste perfil",
                }, h(Seletor, {
                  rotulo: "definição do classificador",
                  valor: form.clsHerdar ? "herdar" : "proprio",
                  opcoes: [
                    { valor: "herdar", rotulo: "herdar do global" },
                    { valor: "proprio", rotulo: "declarar neste perfil" },
                  ],
                  onChange: function (v) { props.onCampo("clsHerdar", v === "herdar"); },
                })),
                form.clsHerdar
                  ? h("div", { className: "vmm-mudo vmm-campo-ajuda" },
                      "herda " + String((global.classificador || {}).provider || "?") + "/"
                        + String((global.classificador || {}).modelo || "?"))
                  : h(Seletor, {
                      rotulo: "classificador activo",
                      valor: form.clsActivo ? "true" : "false",
                      opcoes: [{ valor: "true", rotulo: "activo" }, { valor: "false", rotulo: "desligado" }],
                      onChange: function (v) { props.onCampo("clsActivo", v === "true"); },
                    })
              ),
              form.clsHerdar
                ? null
                : h(
                    "div",
                    { className: "vmm-perfil-campos" },
                    CAMPOS_CLASSIFICADOR.map(function (c) {
                      const valor = cls[c.chave] == null ? "" : cls[c.chave];
                      return h(Campo, { rotulo: c.rotulo, ajuda: c.ajuda, key: c.chave },
                        c.tipo === "seletor"
                          ? h(Seletor, {
                              rotulo: c.rotulo,
                              valor: valor,
                              opcoes: [{ valor: "", rotulo: "— não declarado —" }].concat(opc(c.lista)),
                              onChange: mudarCls(c.chave),
                            })
                          : h(EntradaTexto, {
                              valor: valor,
                              tipo: c.tipo === "numero" ? "number" : "text",
                              rotulo: c.rotulo,
                              onChange: mudarCls(c.chave),
                            }));
                    }),
                    CAMPOS_JSON_CLASSIFICADOR.map(function (c) {
                      return h(Campo, { rotulo: c.rotulo, ajuda: c.ajuda, key: c.chave },
                        h("textarea", {
                          className: "vmm-input vmm-textarea",
                          rows: 3,
                          value: cls[c.chave] == null ? "" : cls[c.chave],
                          placeholder: "{}",
                          "aria-label": c.rotulo,
                          onChange: function (e) { props.onCampoCls(c.chave, e.target.value); },
                        }));
                    })
                  )
            ),

            // --- 4. tarefa persistente --------------------------------------
            // A tarefa detectada sobrevive ao turno, dentro do prazo. Edita-se
            // aqui o DELTA deste perfil: herdar segue o global chave a chave;
            // declarar escreve so o que difere, para nao fixar o perfil ao
            // valor do dia.
            h(
              "div",
              { className: "vmm-bloco" },
              h(
                "div",
                { className: "vmm-bloco-titulo" },
                h("strong", null, "Tarefa persistente"),
                h("span", { className: "vmm-mudo" },
                  "  em vigor: " + (tpEfectiva.activo === false ? "desligada" : "activa")
                    + " · " + String(tpEfectiva.expira_turnos == null ? "?" : tpEfectiva.expira_turnos) + " turnos"
                    + " · " + String(tpEfectiva.expira_min == null ? "?" : tpEfectiva.expira_min) + " min"
                    + " · " + String(tpEfectiva.origem || "?"))
              ),
              h(
                "div",
                { className: "vmm-perfil-campos" },
                h(Campo, {
                  rotulo: "definição",
                  ajuda: "herdar segue o global; declarar sobrepõe-se chave a chave neste perfil",
                }, h(Seletor, {
                  rotulo: "definição da tarefa persistente",
                  valor: form.tpHerdar ? "herdar" : "proprio",
                  opcoes: [
                    { valor: "herdar", rotulo: "herdar do global" },
                    { valor: "proprio", rotulo: "declarar neste perfil" },
                  ],
                  onChange: function (v) { props.onCampo("tpHerdar", v === "herdar"); },
                })),
                form.tpHerdar
                  ? h("div", { className: "vmm-mudo vmm-campo-ajuda" },
                      "herda " + ((global.tarefa_persistente || {}).activo === false ? "desligada" : "activa")
                        + " · " + String((global.tarefa_persistente || {}).expira_turnos) + " turnos · "
                        + String((global.tarefa_persistente || {}).expira_min) + " min")
                  : h("div", null,
                      h(Campo, { rotulo: "activação" },
                        h(Seletor, {
                          rotulo: "tarefa persistente activa neste perfil",
                          valor: form.tpActivo ? "true" : "false",
                          opcoes: [{ valor: "true", rotulo: "activa" },
                                   { valor: "false", rotulo: "desligada neste perfil" }],
                          onChange: function (v) { props.onCampo("tpActivo", v === "true"); },
                        })),
                      h(Campo, { rotulo: "prazo em turnos", ajuda: "vazio = herda o global; 1 a 50" },
                        h(EntradaTexto, {
                          valor: form.tpTurnos,
                          tipo: "number",
                          rotulo: "prazo em turnos",
                          placeholder: "6",
                          onChange: mudar("tpTurnos"),
                        })),
                      h(Campo, { rotulo: "prazo em minutos", ajuda: "vazio = herda o global; 1 a 1440" },
                        h(EntradaTexto, {
                          valor: form.tpMin,
                          tipo: "number",
                          rotulo: "prazo em minutos",
                          placeholder: "90",
                          onChange: mudar("tpMin"),
                        })),
                      h("div", { className: "vmm-mudo vmm-campo-ajuda" },
                        "conta desde a última detecção: herdar não renova o prazo. "
                        + "«complexidade: auto» numa mensagem termina a associação neste perfil."))
              )
            ),

            // --- 5. estrategia ---------------------------------------------
            h(
              "div",
              { className: "vmm-bloco" },
              h(
                "div",
                { className: "vmm-bloco-titulo" },
                h("strong", null, "Estratégia"),
                h("span", { className: "vmm-mudo" },
                  "  em vigor: " + String(estrategiaEfectiva.modo || "?")
                    + " / " + String(estrategiaEfectiva.por || "?")
                    + " · " + String(estrategiaEfectiva.origem || "?"))
              ),
              h(
                "div",
                { className: "vmm-perfil-campos" },
                h(Campo, {
                  rotulo: "definição",
                  ajuda: "herdar segue o global; declarar sobrepõe-se neste perfil",
                }, h(Seletor, {
                  rotulo: "definição da estratégia",
                  valor: form.estHerdar ? "herdar" : "proprio",
                  opcoes: [
                    { valor: "herdar", rotulo: "herdar do global" },
                    { valor: "proprio", rotulo: "declarar neste perfil" },
                  ],
                  onChange: function (v) { props.onCampo("estHerdar", v === "herdar"); },
                })),
                form.estHerdar
                  ? h("div", { className: "vmm-mudo vmm-campo-ajuda" },
                      "herda " + String((global.estrategia || {}).modo || "?")
                        + " / " + String((global.estrategia || {}).por || "?"))
                  : h("div", null,
                      h(Campo, { rotulo: "modo" },
                        h(Seletor, {
                          rotulo: "modo",
                          valor: form.estModo,
                          opcoes: [{ valor: "", rotulo: "— não declarado —" }].concat(opc("estrategia", "modo")),
                          onChange: mudar("estModo"),
                        })),
                      h(Campo, {
                        rotulo: "rotação",
                        ajuda: "por sessão mantém a sessão no mesmo modelo dentro do nível; por turno alterna e invalida a cache do provider",
                      }, h(Seletor, {
                        rotulo: "por",
                        valor: form.estPor,
                        opcoes: [{ valor: "", rotulo: "— não declarado —" }].concat(opc("estrategia", "por")),
                        onChange: mudar("estPor"),
                      })))
              )
            )
          )
    );
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
            // O ⚙ fica COLADO ao nome: e o perfil que ele edita. Na cauda do
            // titulo caia sozinho numa segunda linha (medido em 2026-11-03 numa
            // caixa de 535 px), porque a fila ja nao cabia — o mesmo defeito que
            // cortou o ✎ dos modelos. Ao lado da identidade nunca sobra.
            h("strong", null, nome),
            h(Button, {
              size: "sm",
              variant: "outline",
              className: "vmm-icone vmm-icone-perfil",
              title: "Editar todas as configurações do perfil " + nome,
              "aria-label": "Editar todas as configurações do perfil " + nome,
              onClick: function () { props.onPerfil(nome); },
            }, "⚙"),
            h(Chip, { tone: p.activo ? "default" : "secondary" }, p.activo ? "activo" : "desligado"),
            // So leitura. A escolha faz-se no editor de configuracao: este
            // cartao responde a "porque este modelo", nao serve para decidir.
            h("span", { className: "vmm-estrategia" },
              h("span", { className: "vmm-mudo" }, rotuloEstrategia(p.estrategia)),
              h("span", { className: "vmm-mudo" },
                (p.estrategia || {}).origem === "proprio"
                  ? " · próprio"
                  : " · herdado do global")
            ),
            // Uma rota nova abre uma pagina propria: o catalogo deste perfil e
            // o que se edita, nao o perfil (que se liga no interruptor ao lado).
            h(Button, {
              size: "sm",
              variant: "outline",
              className: "vmm-icone vmm-icone-novo",
              title: "Criar um modelo novo no catálogo de " + nome,
              "aria-label": "Criar um modelo novo no catálogo de " + nome,
              onClick: function () { props.onNovoModelo(nome); },
            }, "+ modelo")
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
                h("td", { className: "vmm-mono vmm-celula-modelo" },
                  m.id,
                  // O icone vive na primeira coluna, ao lado do id: a ultima
                  // coluna de uma tabela de 7 colunas fica fora do cartao e o
                  // overflow:hidden do cartao corta-a em silencio. Aqui nunca
                  // sai do visivel.
                  h(Button, {
                    size: "sm",
                    variant: "ghost",
                    className: "vmm-icone",
                    title: "Editar as opções de " + m.id,
                    "aria-label": "Editar as opções de " + m.id,
                    onClick: function () { props.onModelo(nome, m.id); },
                  }, "✎")),
                h("td", null,
                  h("span", { className: "vmm-mono vmm-mudo vmm-celula-provider" }, m.provider + " / " + m.modelo),
                  h("span", { className: "vmm-mudo vmm-celula-ordem" },
                    " · " + (m.custo == null ? "custo —" : "custo " + m.custo)
                    + (m.ordem == null ? "" : " · ordem " + m.ordem))),
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

  // ---- estrategia no editor ------------------------------------------------
  // A estrategia tem de estar no mesmo sitio que o classificador: quem abre o
  // editor para decidir "como se escolhe o modelo" procura as duas coisas
  // juntas. Le-se e escreve-se no JSON que o editor ja tem, por isso nao ha
  // gravacao parcial: o "Salvar" continua a ser o unico que escreve.
  function estrategiaDe(cfg, perfil) {
    if (!cfg) return null;
    if (perfil === null) return cfg.estrategia || null;
    const p = cfg.perfis && cfg.perfis[perfil];
    return (p && p.estrategia) || null;
  }

  function comEstrategia(cfg, perfil, campos, herdar) {
    const novo = JSON.parse(JSON.stringify(cfg));
    let alvo;
    if (perfil === null) {
      alvo = novo;
    } else {
      novo.perfis = novo.perfis || {};
      novo.perfis[perfil] = novo.perfis[perfil] || {};
      alvo = novo.perfis[perfil];
    }
    if (herdar) {
      delete alvo.estrategia;
      return novo;
    }
    const est = Object.assign({}, alvo.estrategia || {}, campos || {});
    // Um campo vazio e "nao declarado": sai, para nao ficar `""` na config,
    // que a validacao aceita mas o `select` mostra como escolha propria.
    Object.keys(est).forEach(function (k) {
      if (est[k] === null || est[k] === "") delete est[k];
    });
    if (Object.keys(est).length) alvo.estrategia = est;
    else delete alvo.estrategia;
    return novo;
  }

  // ---- tarefa persistente --------------------------------------------------
  // A tarefa associada a um turno continua a mandar nos turnos seguintes da
  // mesma sessao, dentro de um prazo. O bloco vive em `analise`, logo tem dois
  // sitios: o global (pagina de configuracao) e o delta por perfil (pagina do
  // perfil). O painel nao inventa valores: le o que la esta e escreve so o que
  // muda — um perfil que declare os tres valores iguais ao global fica preso ao
  // valor do dia, que e o que a validacao tenta evitar.
  const TAREFA_PERSISTENTE_PADRAO = { activo: true, expira_turnos: 6, expira_min: 90 };

  function tarefaPersistenteDe(cfg, perfil) {
    if (!cfg) return null;
    if (perfil === null) {
      return (cfg.analise && cfg.analise.tarefa_persistente) || null;
    }
    const p = (cfg.perfis || {})[perfil] || {};
    return (p.analise && p.analise.tarefa_persistente) || null;
  }

  function comTarefaPersistente(cfg, perfil, campos, herdar) {
    const novo = JSON.parse(JSON.stringify(cfg));
    let alvo;
    if (perfil === null) {
      alvo = novo;
    } else {
      novo.perfis = novo.perfis || {};
      novo.perfis[perfil] = novo.perfis[perfil] || {};
      alvo = novo.perfis[perfil];
    }
    alvo.analise = alvo.analise || {};
    if (herdar) {
      delete alvo.analise.tarefa_persistente;
    } else {
      const tp = Object.assign({}, alvo.analise.tarefa_persistente || {}, campos || {});
      // Um campo vazio e "nao declarado": sai, para herdar essa chave em vez de
      // a fixar com um valor que o select mostraria como escolha propria.
      Object.keys(tp).forEach(function (k) {
        if (tp[k] === null || tp[k] === undefined || tp[k] === "") delete tp[k];
      });
      if (Object.keys(tp).length) alvo.analise.tarefa_persistente = tp;
      else delete alvo.analise.tarefa_persistente;
    }
    if (!Object.keys(alvo.analise).length) delete alvo.analise;
    return novo;
  }

  // ---- blocos: complexidade e estrategia --------------------------------
  // Sao duas decisoes diferentes e passam a ter dois blocos diferentes: o
  // classificador decide o NIVEL do turno; a estrategia decide COMO se escolhe
  // o modelo dentro desse nivel. Numa so linha liam-se como uma coisa so, e
  // quem vinha configurar uma encontrava a outra a mistura.
  // As duas escrevem no mesmo `configJSON`, que continua a ser a unica fonte
  // de verdade: o "Salvar" do editor e o unico que grava.
  function linhasDe(cfg) {
    return [null].concat(Object.keys(cfg.perfis || {}).sort());
  }

  function tituloBloco(titulo, descricao) {
    return h("div", { className: "vmm-bloco-titulo" },
      h("strong", null, titulo),
      descricao ? h("span", { className: "vmm-mudo" }, " " + descricao) : null);
  }

  function origemDaEstrategia(cfg, perfil) {
    if (perfil === null) return null;
    return estrategiaDe(cfg, perfil) ? " · próprio" : " · herdado do global";
  }

  function SecaoClassificador(props) {
    const cfg = lerJSON(props.configJSON);
    if (!cfg) {
      return h("div", { className: "vmm-classif" },
        h("div", { className: "vmm-mudo" },
          "Complexidade e estratégia: corrija o JSON para poder editar aqui."));
    }
    const pares = paresModelo(cfg);
    const globalBloco = blocoDe(cfg, null);
    const linhas = linhasDe(cfg);
    // Listas fechadas de estrategia vindas do backend: a mesma lista que o
    // `gestor` aceita, nunca uma copia escrita aqui.
    const opcoesEst = props.opcoes || {};
    const modosEst = opcoesEst.modo || [];
    const porEst = opcoesEst.por || [];

    function aplicarEstrategia(perfil, campos, herdar) {
      props.onMudar(JSON.stringify(comEstrategia(cfg, perfil, campos, herdar), null, 2));
    }

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

    // ---- bloco 1: complexidade ------------------------------------------
    const blocoComplexidade = h("div", { className: "vmm-bloco", key: "complexidade" },
      tituloBloco("Complexidade",
        "quem decide o nível de cada turno: um modelo classificador ou a heurística."),
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

    // ---- bloco 2: tarefa persistente ------------------------------------
    // Uma tarefa detectada num turno deixava de mandar no turno seguinte (o
    // detector le so a ultima mensagem, o pin dura um turno): o modelo mudava a
    // meio da conversa quando o utilizador escrevia "continua". Este bloco
    // decide quanto tempo a associacao dura. Aqui so o global — o delta por
    // perfil vive na pagina do perfil, para a mesma decisao nao ter duas copias
    // no ecra.
    const tpGlobal = tarefaPersistenteDe(cfg, null) || {};
    const tpActivo = tpGlobal.activo !== false;
    const TURNOS_PRAZO = [1, 2, 3, 4, 6, 8, 10, 15, 20, 30, 50];
    const MINUTOS_PRAZO = [15, 30, 45, 60, 90, 120, 180, 240, 360, 720, 1440];
    function aplicarTarefa(campo, valor) {
      const campos = {};
      campos[campo] = valor;
      props.onMudar(JSON.stringify(comTarefaPersistente(cfg, null, campos, false), null, 2));
    }
    const blocoTarefa = h("div", { className: "vmm-bloco", key: "tarefa-persistente" },
      tituloBloco("Tarefa persistente",
        "a tarefa detectada num turno continua a mandar nos seguintes da mesma sessão, dentro do prazo."),
      h("div", { className: "vmm-classif-linha vmm-linha-estrategia" },
        h("span", { className: "vmm-classif-nome",
                    title: "Vale para todos os perfis que não declararem outra coisa. O delta de cada perfil edita-se na página do perfil." },
          "global",
          h("span", { className: "vmm-mudo" },
            Object.keys(tpGlobal).length ? " · declarado" : " · por omissão (6 turnos / 90 min)")),
        h(Seletor, {
          key: "tp-activo",
          rotulo: "tarefa persistente em todos os perfis",
          valor: tpActivo ? "true" : "false",
          opcoes: [{ valor: "true", rotulo: "activa" }, { valor: "false", rotulo: "desligada" }],
          onChange: function (v) { aplicarTarefa("activo", v === "true"); },
        }),
        h(Seletor, {
          key: "tp-turnos",
          rotulo: "prazo em turnos",
          valor: tpGlobal.expira_turnos == null ? "" : String(tpGlobal.expira_turnos),
          opcoes: [{ valor: "", rotulo: "turnos: por omissão" }].concat(
            TURNOS_PRAZO.map(function (n) { return { valor: String(n), rotulo: "dura " + n + " turnos" }; })),
          onChange: function (v) { aplicarTarefa("expira_turnos", v === "" ? "" : parseInt(v, 10)); },
        }),
        h(Seletor, {
          key: "tp-min",
          rotulo: "prazo em minutos",
          valor: tpGlobal.expira_min == null ? "" : String(tpGlobal.expira_min),
          opcoes: [{ valor: "", rotulo: "minutos: por omissão" }].concat(
            MINUTOS_PRAZO.map(function (n) {
              return { valor: String(n), rotulo: n >= 60 ? (n / 60) + " h" : n + " min" };
            })),
          onChange: function (v) { aplicarTarefa("expira_min", v === "" ? "" : parseInt(v, 10)); },
        })
      ),
      h("div", { className: "vmm-mudo vmm-campo-ajuda" },
        "conta desde a última detecção: herdar não renova o prazo. "
        + "«complexidade: auto» numa mensagem termina a associação; desligada, cada turno volta a decidir pela complexidade.")
    );

    // ---- bloco 3: estrategia --------------------------------------------
    const blocoEstrategia = h("div", { className: "vmm-bloco", key: "estrategia" },
      tituloBloco("Estratégia",
        "com o nível decidido, como se escolhe o modelo dentro dele."),
      linhas.map(function (perfil) {
        const est = estrategiaDe(cfg, perfil);
        const estGlobal = estrategiaDe(cfg, null);
        // Sem o prefixo "estratégia: " — a linha ja diz de que perfil e a
        // estrategia, e o prefixo empurrava o texto para fora do select
        // ("estratégia: herdar do global (rotaç…").
        const rotuloModo = {
          rodizio: "rotação",
          prioridade: "prioridade (primeira da lista)",
        };
        // forma curta, para o rotulo "herdar do global (rotação)".
        const curtoModo = { rodizio: "rotação", prioridade: "prioridade" };
        const opcoesModo = modosEst.map(function (o) {
          return { valor: o.valor, rotulo: rotuloModo[o.valor] || o.rotulo || o.valor };
        });
        const primeira = perfil === null
          ? [{ valor: "", rotulo: "não declarada" }]
          : [{ valor: "", rotulo: "herdar do global"
                + (estGlobal && estGlobal.modo ? " (" + (curtoModo[estGlobal.modo] || estGlobal.modo) + ")" : "") }];
        const porModo = (est && est.modo) || (perfil === null ? "" : (estGlobal && estGlobal.modo) || "");
        return h("div", { key: perfil === null ? "@global" : perfil, className: "vmm-classif-linha vmm-linha-estrategia" },
          h("span", { className: "vmm-classif-nome",
                      title: perfil === null
                        ? "O global não é um perfil: vale para todos os que não declaram nada. Mudar aqui move quem herda."
                        : "Só este perfil. Os outros não são tocados." },
            (perfil === null ? "global" : perfil),
            h("span", { className: "vmm-mudo" },
              perfil === null ? " · aplica-se a quem não declara" : origemDaEstrategia(cfg, perfil))
          ),
          h(Seletor, {
            key: "est-modo",
            rotulo: "estratégia de " + (perfil === null ? "todos os perfis" : perfil),
            valor: est ? est.modo || "" : "",
            opcoes: primeira.concat(opcoesModo),
            onChange: function (v) {
              aplicarEstrategia(perfil, v ? { modo: v } : null, !v);
            },
          }),
          h(Seletor, {
            key: "est-por",
            rotulo: "rotação de " + (perfil === null ? "todos os perfis" : perfil),
            valor: est ? est.por || "" : "",
            opcoes: [{ valor: "", rotulo: "rotação: por omissão (sessão)" }].concat(
              porEst.map(function (o) { return { valor: o.valor, rotulo: "rotação: " + (o.rotulo || o.valor) }; })
            ),
            // `por` so governa a rotacao: em `prioridade` um select activo
            // que nao faz nada e uma armadilha.
            disabled: porModo === "prioridade",
            title: "Só se aplica à rotação",
            onChange: function (v) { aplicarEstrategia(perfil, v ? { por: v } : null, false); },
          })
        );
      })
    );

    // ---- bloco 4: superficies -------------------------------------------
    // "Se a superficie da sessao for cron, o turno vai para este modelo." Fica
    // aqui, e nao nos cartoes: e configuracao como as outras duas. Escreve
    // pelo endpoint `/superficies` (cada regra e um delta com rev) e nao pelo
    // JSON do textarea, para nao apagar o que outra sessao escreveu.
    const perfisEstado = (props.estado || {}).perfis || {};
    const opcoesEstado = (props.estado || {}).opcoes || {};
    const blocoSuperficies = h("div", { className: "vmm-bloco", key: "superficies" },
      tituloBloco("Superfícies",
        "se a superfície da sessão for esta, o turno vai para este modelo (por perfil)."),
      linhas.map(function (perfil) {
        if (perfil === null) return null;
        const p = perfisEstado[perfil] || {};
        return h("div", { key: "sup-" + perfil, className: "vmm-sup-perfil" },
          h("div", { className: "vmm-classif-nome" }, perfil),
          h(Superficies, {
            perfil: perfil,
            regras: p.superficies || {},
            catalogo: p.catalogo || [],
            niveis: opcoesEstado.niveis || [],
            conhecidas: opcoesEstado.superficies || [],
            aGravar: !!props.aGravar,
            onMudar: function (sup, alvo) { props.onSuperficie(perfil, sup, alvo); },
          })
        );
      })
    );

    return h("div", { className: "vmm-classif" },
      props.versao
        ? h("div", { className: "vmm-mudo vmm-versao-linha" }, "painel " + props.versao)
        : null,
      blocoComplexidade,
      blocoTarefa,
      blocoEstrategia,
      blocoSuperficies
    );
  }

  // A configuracao vive numa pagina EXCLUSIVA. Quando o editor esta aberto a
  // pagina principal nao e renderizada — nao ha duas copias da config no ecra
  // nem um cartao de estado a competir com o formulario. Fechar grava e volta
  // ao painel: um "fechar" que descartasse em silencio perdia o trabalho que o
  // dono acabou de fazer, e um "gravar" que nao fecha deixava-o preso aqui.
  // Ha um "Descartar" explicito para quem quiser sair sem gravar.
  function PaginaConfig(props) {
    const estado = props.estado || {};
    const meta = metaPlugin(estado);
    const versao = (estado.plugin || {}).versao || estado.versao_painel || "";
    return h(
      "div",
      { className: "vmm-pagina vmm-pagina-config" },
      h(AvisoConfig, { estado: estado }),
      h(
        "div",
        { className: "vmm-topo" },
        h(
          "div",
          null,
          h("h1", { className: "vmm-h1" }, "Configuração"),
          h(
            "div",
            { className: "vmm-sub" },
            "Complexidade (quem decide o nível de cada turno), estratégia (como se escolhe o modelo "
              + "dentro do nível) e superfícies (por onde corre). Fechar grava e volta ao painel."
          ),
          linhaIdentidade(meta)
        ),
        h(
          "div",
          { className: "vmm-accoes" },
          h(Button, {
            size: "sm",
            variant: "outline",
            onClick: props.onValidar,
            disabled: props.aValidar,
            title: "Validar a configuração contra o schema, sem gravar",
          }, props.aValidar ? "A validar…" : "Validar"),
          h(Button, {
            size: "sm",
            variant: "default",
            onClick: props.onGravar,
            disabled: props.aGravar,
            className: props.validacaoOk ? "vmm-btn-ok" : undefined,
            title: "Gravar e voltar ao painel",
          }, props.aGravar ? "A gravar…" : (props.validacaoOk ? "✓ Gravar e voltar" : "Gravar e voltar")),
          h(Button, {
            size: "sm",
            variant: "ghost",
            onClick: props.onDescartar,
            title: "Voltar ao painel sem gravar as alterações",
          }, "Descartar")
        )
      ),

      props.erro ? h("div", { className: "vmm-erro" }, "erro: " + props.erro) : null,

      props.validacaoOk ? h(
        "div",
        { className: "vmm-ok", role: "status", "aria-live": "polite" },
        "✓ " + props.validacaoOk
      ) : null,

      // Os tres blocos, cada um com titulo proprio: duas decisoes diferentes
      // (quem classifica / como se escolhe) nao partilham o mesmo bloco.
      h(SecaoClassificador, {
        configJSON: props.configJSON,
        estado: estado,
        aGravar: props.aGravar,
        versao: versao,
        opcoes: ((estado.opcoes || {}).estrategia) || {},
        onSuperficie: props.onSuperficie,
        onMudar: props.onMudarJSON,
      }),

      h(
        Card,
        { className: "vmm-card-editor" },
        h(
          CardContent,
          { className: "vmm-card-content" },
          h("h3", { className: "vmm-editor-titulo" }, "JSON da configuração"),
          h(
            "div",
            { className: "vmm-editor-corpo" },
            h("textarea", {
              value: props.configJSON,
              onChange: function (e) { props.onMudarJSON(e.target.value); },
              className: "vmm-editor-textarea",
              style: { width: "100%", height: "350px", fontFamily: "monospace", fontSize: "12px", lineHeight: "1.5", padding: "8px", border: "1px solid var(--border)", borderRadius: "4px", resize: "vertical" }
            }),
            (props.errosValidacao || []).length > 0 && h(
              "div",
              { className: "vmm-editor-erros" },
              props.errosValidacao.map(function (e, i) {
                return h("div", { key: i, className: "vmm-erro-item" }, "• " + e);
              })
            )
          ),
          h(
            "div",
            { className: "vmm-editor-accoes" },
            h(Button, { size: "sm", variant: "ghost", onClick: props.onDescartar }, "Voltar sem gravar")
          )
        )
      )
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
    // Uma rota do catalogo aberta numa pagina propria. `null` = painel
    // principal. A pagina de configuracao e esta nao se sobrepoem: cada uma tem
    // o seu early return, para que o painel nunca fique por baixo do formulario.
    const [modeloAberto, setModeloAberto] = useState(null);
    const [modeloForm, setModeloForm] = useState({});
    const [modeloOpcoes, setModeloOpcoes] = useState({});
    const [modeloRev, setModeloRev] = useState("");
    const [modeloErro, setModeloErro] = useState(null);
    const [modeloErros, setModeloErros] = useState([]);
    const [modeloOk, setModeloOk] = useState(null);
    const [modeloAGravar, setModeloAGravar] = useState(false);
    const [modeloACarregar, setModeloACarregar] = useState(false);
    // Um perfil aberto na sua propria pagina. Estado separado do da pagina de
    // um modelo porque as duas nao se sobrepoem: cada uma tem o seu early
    // return e os seus proprios buffers — partilhar `form` obrigaria a limpar
    // campos de uma quando se abre a outra.
    const [perfilAberto, setPerfilAberto] = useState(null);
    const [perfilForm, setPerfilForm] = useState({});
    const [perfilOpcoes, setPerfilOpcoes] = useState({});
    const [perfilRev, setPerfilRev] = useState("");
    const [perfilEfectivo, setPerfilEfectivo] = useState({});
    const [perfilGlobal, setPerfilGlobal] = useState({});
    const [perfilErro, setPerfilErro] = useState(null);
    const [perfilErros, setPerfilErros] = useState([]);
    const [perfilOk, setPerfilOk] = useState(null);
    const [perfilAGravar, setPerfilAGravar] = useState(false);
    const [perfilACarregar, setPerfilACarregar] = useState(false);

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

    useEffect(function () { carregar(); carregarConfig(); }, [carregar]);

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

    function mudarSuperficie(perfil, superficie, alvo) {
      setAGravar(true);
      pedir("/superficies", {
        method: "POST",
        body: {
          ambito: "perfil",
          perfil: perfil,
          superficie: superficie,
          alvo: alvo === "__nunca" ? "nunca" : String(alvo || ""),
          rev: (estado && estado.rev) || "",
        },
      })
        .then(function () { return carregar(); })
        .catch(function (e) {
          const msg = String(e && e.message ? e.message : e);
          setErro(msg.indexOf("409") !== -1
            ? "As superfícies mudaram noutro sítio — recarreguei o estado. Escolha outra vez."
            : "Erro ao gravar a superfície: " + msg);
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
            // Recarrega do servidor em vez de esvaziar: a seccao de
            // classificador/estrategia continua a ler o texto.
            carregarConfig();
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

    // Fechar grava. `salvarConfig` valida e, so quando o servidor aceita, poe
    // `mostrarEditor` a false — logo um fecho nunca perde trabalho nem fecha
    // sobre uma config invalida (o erro fica a vista e a pagina mantem-se).
    function gravarEFechar() {
      salvarConfig();
    }

    // Sair sem gravar: so aqui o texto e abandonado, e sempre por accao
    // explicita do dono.
    function fecharEditor() {
      setMostrarEditor(false);
      // O texto NAO se apaga: a seccao de classificador/estrategia le-o sempre.
      setErrosValidacao([]);
      setValidacaoOk(null);
      carregarConfig();
    }

    // ---- uma rota do catalogo: abrir, validar, gravar, descartar ----------
    function textoDoErro(e) {
      return String(e && e.message ? e.message : e);
    }

    // Abre a pagina de UMA rota. Le sempre do servidor: a entrada tem campos
    // (esforco, limites, modalidades) que o `/estado` nao traz, e editar o que
    // o cartao mostra em vez do que o ficheiro tem e como se perde um campo
    // sem ninguem dar por isso.
    function abrirModelo(perfil, id) {
      setModeloAberto({ perfil: perfil, id: id || null, novo: !id });
      setModeloForm({});
      setModeloOpcoes({});
      setModeloRev("");
      setModeloErro(null);
      setModeloErros([]);
      setModeloOk(null);
      // O aviso verde do painel fala da gravacao anterior; deixa-lo de pe atras
      // desta pagina faz parecer que o que se esta a editar ja foi gravado.
      setValidacaoOk(null);
      setModeloACarregar(true);
      const consulta = "/modelo?perfil=" + encodeURIComponent(perfil)
        + (id ? "&id=" + encodeURIComponent(id) : "");
      pedir(consulta)
        .then(function (r) {
          setModeloOpcoes((r && r.opcoes) || {});
          setModeloForm(entradaParaForm((r && r.entrada) || {}));
          setModeloRev(r && r.rev ? String(r.rev) : "");
        })
        .catch(function (e) { setModeloErro("Erro ao ler a rota: " + textoDoErro(e)); })
        .then(function () { setModeloACarregar(false); });
    }

    function mudarCampoModelo(campo, valor) {
      const proximo = Object.assign({}, modeloForm);
      proximo[campo] = valor;
      setModeloForm(proximo);
      setModeloOk(null);
      setModeloErros([]);
      setModeloErro(null);
    }

    function corpoDoModelo() {
      const aberto = modeloAberto || {};
      return {
        perfil: aberto.perfil,
        id: aberto.id || null,
        entrada: formParaEntrada(modeloForm, modeloOpcoes),
        rev: modeloRev,
      };
    }

    // Validar: o mesmo `validar` do `gravar`, sem escrever. E o mesmo botao do
    // editor de configuracao — nao ha um caminho de validacao paralelo.
    function validarModelo() {
      setModeloErros([]);
      setModeloOk(null);
      setModeloErro(null);
      setModeloAGravar(true);
      const corpo = Object.assign(corpoDoModelo(), { validar: true });
      pedir("/modelo", { method: "POST", body: corpo })
        .then(function (r) {
          const erros = (r && r.erros) || [];
          if (erros.length) {
            setModeloErros(erros);
            setModeloErro("Configuração inválida: " + erros.join("; "));
            return;
          }
          const e = corpo.entrada;
          setModeloOk("Validado — " + (e.id || "(sem id)")
            + " · " + (e.complexidade || []).length + " nível(is)"
            + " · " + (e.provider || "?") + "/" + (e.modelo || "?") + " · 0 erros");
        })
        .catch(function (e) { setModeloErro("Erro ao validar: " + textoDoErro(e)); })
        .then(function () { setModeloAGravar(false); });
    }

    // Gravar: valida primeiro e so grava se passar. Assim o erro que se ve e o
    // do `gestor`, com o campo e o valor, e nao um 400 generico do transporte.
    function gravarModelo() {
      setModeloErros([]);
      setModeloOk(null);
      setModeloErro(null);
      setModeloAGravar(true);
      const corpo = corpoDoModelo();
      pedir("/modelo", { method: "POST", body: Object.assign({}, corpo, { validar: true }) })
        .then(function (v) {
          const erros = (v && v.erros) || [];
          if (erros.length) {
            setModeloErros(erros);
            setModeloErro("Configuração inválida: " + erros.join("; "));
            return null;
          }
          return pedir("/modelo", { method: "POST", body: corpo });
        })
        .then(function (r) {
          if (r === null) return;
          const gravado = (r && r.id) || (corpo.entrada || {}).id || "";
          setModeloAberto(null);
          setModeloForm({});
          setModeloRev("");
          setValidacaoOk("Modelo " + gravado + " gravado em " + corpo.perfil + " — o gestor já o lê");
          carregar();
        })
        .catch(function (e) {
          const msg = textoDoErro(e);
          setModeloErro(msg.indexOf("409") !== -1
            ? "A configuração foi alterada noutro sítio desde que abriu esta página. "
              + "Descartar e abrir outra vez para ver o estado actual."
            : "Erro ao gravar: " + msg);
        })
        .then(function () { setModeloAGravar(false); });
    }

    // Descartar e o unico caminho que abandona o formulario — sempre por accao
    // explicita do dono, nunca por um erro. Como no editor de configuracao,
    // nao deixa aviso: fechar sem gravar nao e um resultado a anunciar.
    function descartarModelo() {
      setModeloAberto(null);
      setModeloForm({});
      setModeloOpcoes({});
      setModeloRev("");
      setModeloErros([]);
      setModeloErro(null);
      setModeloOk(null);
      setValidacaoOk(null);
    }

    // ---- pagina de um perfil: abrir, editar, validar, gravar, descartar ----
    // Le sempre do servidor. O `/estado` traz o resumo do cartao, nao o bloco
    // que o perfil declara — e o que se edita aqui e o declarado, com a
    // heranca a vista. Editar o resumo seria editar outro objecto.
    function abrirPerfil(perfil) {
      setPerfilAberto(perfil);
      setPerfilForm({});
      setPerfilOpcoes({});
      setPerfilRev("");
      setPerfilEfectivo({});
      setPerfilGlobal({});
      setPerfilErro(null);
      setPerfilErros([]);
      setPerfilOk(null);
      setValidacaoOk(null);
      setPerfilACarregar(true);
      pedir("/perfil?nome=" + encodeURIComponent(perfil))
        .then(function (r) {
          setPerfilOpcoes((r && r.opcoes) || {});
          setPerfilForm(perfilParaForm((r && r.declarado) || {}));
          setPerfilRev(r && r.rev ? String(r.rev) : "");
          setPerfilEfectivo((r && r.efectivo) || {});
          setPerfilGlobal((r && r.global) || {});
        })
        .catch(function (e) { setPerfilErro("Erro ao ler o perfil: " + textoDoErro(e)); })
        .then(function () { setPerfilACarregar(false); });
    }

    function mudarCampoPerfil(campo, valor) {
      const proximo = Object.assign({}, perfilForm);
      proximo[campo] = valor;
      setPerfilForm(proximo);
      setPerfilOk(null);
      setPerfilErros([]);
      setPerfilErro(null);
    }

    function mudarCampoCls(campo, valor) {
      const proximo = Object.assign({}, perfilForm);
      proximo.cls = Object.assign({}, perfilForm.cls || {});
      proximo.cls[campo] = valor;
      setPerfilForm(proximo);
      setPerfilOk(null);
      setPerfilErros([]);
      setPerfilErro(null);
    }

    function mudarTarefa(indice, campo, valor) {
      const proximo = Object.assign({}, perfilForm);
      proximo.tarefas = (perfilForm.tarefas || []).map(function (t, i) {
        return i === indice ? Object.assign({}, t, { [campo]: valor }) : t;
      });
      setPerfilForm(proximo);
      setPerfilOk(null);
      setPerfilErros([]);
      setPerfilErro(null);
    }

    function novaTarefa() {
      const proximo = Object.assign({}, perfilForm);
      proximo.tarefas = (perfilForm.tarefas || []).concat([{ id: "", termos: "", prioridade: 0 }]);
      setPerfilForm(proximo);
      setPerfilOk(null);
    }

    function removerTarefa(indice) {
      const proximo = Object.assign({}, perfilForm);
      proximo.tarefas = (perfilForm.tarefas || []).filter(function (_, i) { return i !== indice; });
      setPerfilForm(proximo);
      setPerfilOk(null);
      setPerfilErros([]);
    }

    // O corpo do POST. Os erros do formulario param aqui, com o campo nomeado:
    // um 400 do gestor por causa de um numero escrito com uma letra nao diz
    // qual dos vinte campos e.
    function corpoDoPerfil() {
      const r = formParaPerfil(perfilForm, perfilAberto, perfilRev);
      return r;
    }

    function validarPerfil() {
      setPerfilErros([]);
      setPerfilOk(null);
      setPerfilErro(null);
      const r = corpoDoPerfil();
      if (r.erro.length) {
        setPerfilErros(r.erro);
        setPerfilErro("Formulário incompleto: " + r.erro.join("; "));
        return;
      }
      setPerfilAGravar(true);
      pedir("/perfil", { method: "POST", body: Object.assign({}, r.corpo, { validar: true }) })
        .then(function (v) {
          const erros = (v && v.erros) || [];
          if (erros.length) {
            setPerfilErros(erros);
            setPerfilErro("Configuração inválida: " + erros.join("; "));
            return;
          }
          setPerfilOk("Validado — perfil " + perfilAberto
            + " · " + Object.keys(r.corpo.tarefas || {}).length + " tarefa(s)"
            + " · classificador " + (r.corpo.classificador === "herdar" ? "herdado" : "próprio")
            + " · estratégia " + (r.corpo.estrategia === "herdar" ? "herdada" : "própria")
            + " · 0 erros");
        })
        .catch(function (e) { setPerfilErro("Erro ao validar: " + textoDoErro(e)); })
        .then(function () { setPerfilAGravar(false); });
    }

    function gravarPerfil() {
      setPerfilErros([]);
      setPerfilOk(null);
      setPerfilErro(null);
      const r = corpoDoPerfil();
      if (r.erro.length) {
        setPerfilErros(r.erro);
        setPerfilErro("Formulário incompleto: " + r.erro.join("; "));
        return;
      }
      setPerfilAGravar(true);
      pedir("/perfil", { method: "POST", body: Object.assign({}, r.corpo, { validar: true }) })
        .then(function (v) {
          const erros = (v && v.erros) || [];
          if (erros.length) {
            setPerfilErros(erros);
            setPerfilErro("Configuração inválida: " + erros.join("; "));
            return null;
          }
          return pedir("/perfil", { method: "POST", body: r.corpo });
        })
        .then(function (res) {
          if (res === null) return;
          setPerfilAberto(null);
          setPerfilForm({});
          setPerfilRev("");
          setValidacaoOk("Perfil " + r.corpo.perfil + " gravado — o gestor já o lê");
          carregar();
        })
        .catch(function (e) {
          const msg = textoDoErro(e);
          setPerfilErro(msg.indexOf("409") !== -1
            ? "A configuração foi alterada noutro sítio desde que abriu esta página. "
              + "Descartar e abrir outra vez para ver o estado actual."
            : "Erro ao gravar: " + msg);
        })
        .then(function () { setPerfilAGravar(false); });
    }

    function descartarPerfil() {
      setPerfilAberto(null);
      setPerfilForm({});
      setPerfilOpcoes({});
      setPerfilRev("");
      setPerfilEfectivo({});
      setPerfilGlobal({});
      setPerfilErros([]);
      setPerfilErro(null);
      setPerfilOk(null);
      setValidacaoOk(null);
    }

    if (!estado && !erro) {
      return h("div", { className: "vmm-pagina" }, h("div", { className: "vmm-mudo" }, "a ler o estado do gestor…"));
    }

    const perfis = (estado && estado.perfis) || {};
    const nomes = Object.keys(perfis);
    // Calculado uma vez: a identidade nao muda a meio de um render.
    const meta = metaPlugin(estado);

    // Pagina exclusiva: com o editor aberto, o painel principal nao chega a
    // ser construido. Um early return e o que garante isso — um `&&` no meio da
    // arvore deixava os cartoes de estado por baixo do formulario.
    // A pagina de uma rota do catalogo tem o seu proprio early return, e vem
    // primeiro: sao dois formularios distintos, nunca os dois ao mesmo tempo.
    // A pagina de um perfil vem primeiro: tem o seu proprio early return, para
    // o painel principal nunca ficar construido por baixo do formulario.
    if (perfilAberto) {
      return h(PaginaPerfil, {
        estado: estado,
        perfil: perfilAberto,
        form: perfilForm,
        opcoes: perfilOpcoes,
        rev: perfilRev,
        efectivo: perfilEfectivo,
        global: perfilGlobal,
        erro: perfilErro,
        erros: perfilErros,
        validacaoOk: perfilOk,
        aGravar: perfilAGravar,
        aValidar: perfilAGravar,
        aCarregar: perfilACarregar,
        onCampo: mudarCampoPerfil,
        onCampoCls: mudarCampoCls,
        onTarefa: mudarTarefa,
        onNovaTarefa: novaTarefa,
        onRemoverTarefa: removerTarefa,
        onValidar: validarPerfil,
        onGravar: gravarPerfil,
        onDescartar: descartarPerfil,
      });
    }

    if (modeloAberto) {
      return h(PaginaModelo, {
        estado: estado,
        perfil: modeloAberto.perfil,
        id: modeloAberto.id,
        novo: modeloAberto.novo,
        form: modeloForm,
        opcoes: modeloOpcoes,
        rev: modeloRev,
        erro: modeloErro,
        erros: modeloErros,
        validacaoOk: modeloOk,
        aGravar: modeloAGravar,
        aCarregar: modeloACarregar,
        onCampo: mudarCampoModelo,
        onCampoComplexidade: function (v) { mudarCampoModelo("complexidade", v); },
        onCampoTarefas: function (v) { mudarCampoModelo("tarefas", v); },
        onCampoModalidades: function (v) { mudarCampoModelo("modalidades", v); },
        onValidar: validarModelo,
        onGravar: gravarModelo,
        onDescartar: descartarModelo,
      });
    }

    if (mostrarEditor) {
      return h(PaginaConfig, {
        estado: estado,
        configJSON: configJSON,
        erro: erro,
        errosValidacao: errosValidacao,
        validacaoOk: validacaoOk,
        aValidar: aValidar,
        aGravar: aGravar,
        onValidar: validarConfig,
        onGravar: gravarEFechar,
        onDescartar: fecharEditor,
        onSuperficie: function (perfil, sup, alvo) { mudarSuperficie(perfil, sup, alvo); },
        onMudarJSON: function (novo) {
          setConfigJSON(novo);
          setValidacaoOk(null);
          setErrosValidacao([]);
        },
      });
    }

    return h(
      "div",
      { className: "vmm-pagina" },
      h(AvisoConfig, { estado: estado }),
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

      // A configuracao — complexidade, estrategia e superficies — vive toda
      // dentro do editor de configuracao. Fora dele ficam so os cartoes de
      // estado por perfil e o registo do que foi servido.

      erro ? h("div", { className: "vmm-erro" }, "erro: " + erro) : null,

      // Aviso de sucesso fora do editor: sobrevive ao fecho depois de gravar.
      (validacaoOk && !mostrarEditor) ? h(
        "div",
        { className: "vmm-ok", role: "status", "aria-live": "polite" },
        "✓ " + validacaoOk
      ) : null,

      h(
        "div",
        { className: "vmm-grelha" },
        nomes.map(function (n) {
          return h(CartaoPerfil, {
            key: n,
            nome: n,
            dados: perfis[n],
            aGravar: aGravar,
            onToggle: function (perfil, activo) { alternar("perfil", perfil, activo); },
            onPerfil: function (perfil) { abrirPerfil(perfil); },
            onModelo: function (perfil, id) { abrirModelo(perfil, id); },
            onNovoModelo: function (perfil) { abrirModelo(perfil, null); },
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
    window.__VMM_TEST__.opcoesAlvo = opcoesAlvo;
    window.__VMM_TEST__.estrategiaDe = estrategiaDe;
    window.__VMM_TEST__.comEstrategia = comEstrategia;
    window.__VMM_TEST__.SecaoClassificador = SecaoClassificador;
    window.__VMM_TEST__.valorDaRegra = valorDaRegra;
    window.__VMM_TEST__.rotuloRegra = rotuloRegra;
    window.__VMM_TEST__.metaPlugin = metaPlugin;
    window.__VMM_TEST__.linhaIdentidade = linhaIdentidade;
    // O aviso de config lida com erros: e a unica superficie que denuncia que o
    // ficheiro no disco nao e o que o painel mostra. Testado sem browser.
    window.__VMM_TEST__.AvisoConfig = AvisoConfig;
    // Componentes expostos para o teste de render: um erro de referencia no
    // corpo do cartao so aparecia no browser do dono (ja aconteceu — o cartao
    // referia `perfil.superficies` sem binding). Chamar o componente em node
    // executa o corpo e apanha-o antes de publicar.
    window.__VMM_TEST__.PaginaConfig = PaginaConfig;
    window.__VMM_TEST__.CartaoPerfil = CartaoPerfil;
    window.__VMM_TEST__.Superficies = Superficies;
    // A conversao forma <-> entrada do catalogo: e o que decide o que fica
    // escrito na rota (e o que fica de fora). Testada sem browser.
    window.__VMM_TEST__.entradaParaForm = entradaParaForm;
    window.__VMM_TEST__.formParaEntrada = formParaEntrada;
    window.__VMM_TEST__.alternarOpcao = alternarOpcao;
    window.__VMM_TEST__.emOrdem = emOrdem;
    window.__VMM_TEST__.BotoesOpcao = BotoesOpcao;
    window.__VMM_TEST__.PaginaModelo = PaginaModelo;
    // A pagina de um perfil: as funcoes puras que decidem o que vai para o
    // ficheiro sao testadas em node, sem browser — sao o sitio onde um erro se
    // paga caro (escrevem no bloco que o middleware le).
    window.__VMM_TEST__.PaginaPerfil = PaginaPerfil;
    window.__VMM_TEST__.perfilParaForm = perfilParaForm;
    window.__VMM_TEST__.formParaPerfil = formParaPerfil;
    window.__VMM_TEST__.CAMPOS_CLASSIFICADOR = CAMPOS_CLASSIFICADOR;
    window.__VMM_TEST__.CAMPOS_JSON_CLASSIFICADOR = CAMPOS_JSON_CLASSIFICADOR;
  }
})();
