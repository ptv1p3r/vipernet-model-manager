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

  // ---- indicador modelo/heuristica do cartao -----------------------------
  // A pergunta do cartao nao e "esta ligado?", e "esta a decidir?". Um
  // classificador ligado que falha cai na heuristica, e um perfil sem bloco
  // herda o global sem o mostrar em lado nenhum. Por isso o rotulo diz as duas
  // coisas: o que devia decidir e a evidencia do ledger de que decidiu.
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
    const evidencia = a.respostas > 0
      ? "últimos " + a.respostas + " turnos: " + contagens.join(" · ")
      : "";
    const alertaBruto = c.modo === "modelo" ? (a.erro || "") : "";

    if (c.modo === "modelo") {
      const onde = c.origem === "proprio" ? "próprio do perfil" : "herdado do global";
      const extra = [];
      if (c.esforco) extra.push("esforço " + c.esforco);
      extra.push(onde);
      return {
        modo: "modelo",
        titulo: "modelo · " + (c.provider || "?") + "/" + (c.modelo || "?"),
        tom: "default",
        detalhe: extra.join(" · "),
        evidencia: evidencia,
        alerta: alertaBruto ? "o modelo falhou no último turno — caiu na heurística: " + alertaBruto : "",
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
            h("span", { className: "vmm-mudo" }, "estrategia: " + (p.estrategia || "—")),
            p.estrategia === "rodizio"
              ? h("span", { className: "vmm-mudo" }, "rodizio por " + ((p.rodizio || {}).por || "turno"))
              : null
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
        const desligado = {
          activo: false,
          provider: actual.provider || "",
          modelo: actual.modelo || "",
        };
        if (actual.esforco) desligado.esforco = actual.esforco;
        return aplicar(perfil, desligado);
      }
      const primeiro = pares[0] || { provider: "", modelo: "" };
      const ligado = {
        activo: true,
        provider: actual.provider || primeiro.provider,
        modelo: actual.modelo || primeiro.modelo,
      };
      if (actual.esforco) ligado.esforco = actual.esforco;
      aplicar(perfil, ligado);
    }

    function mudarCampo(perfil, campo, valor) {
      const actual = blocoDe(cfg, perfil) || CLASSIF_OFF;
      const novo = {
        activo: !!actual.activo,
        provider: actual.provider || "",
        modelo: actual.modelo || "",
      };
      // O esforco acompanha o bloco: nao se perde ao mudar provider ou modelo,
      // e sai do JSON quando volta a "nao declarado" — um campo vazio deixado
      // la era validado como valor invalido.
      if (actual.esforco) novo.esforco = actual.esforco;
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

    function carregarConfig() {
      pedir("/config")
        .then(function (c) { setConfigJSON(JSON.stringify(c, null, 2)); setErrosValidacao([]); })
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
        pedir("/config", { method: "POST", body: cfg })
          .then(function () {
            setMostrarEditor(false);
            setConfigJSON("");
            setErrosValidacao([]);
            setValidacaoOk("Configuração gravada com sucesso — o gestor já a lê");
            carregar();
          })
          .catch(function (e) { setErro("Erro ao salvar: " + String(e && e.message ? e.message : e)); });
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
          )
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
            onToggle: function (perfil, activo) { alternar("perfil", perfil, activo); },
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
        "Catalogo e associacoes de tarefa vivem em plugin-data/vipernet-model-manager/config.json — este painel permite editar a configuração. Versao do painel: " +
        ((estado && estado.versao_painel) || "—"))
    );
  }

  window.__HERMES_PLUGINS__.register(NOME, Pagina);

  // Exposicao para teste: as funcoes que decidem o que vai para o JSON da
  // configuracao sao puras (sem React) e sao o sitio onde um erro se paga caro
  // — escrevem no bloco que o middleware le. Testadas em node, sem browser.
  if (typeof window !== "undefined" && window.__VMM_TEST__) {
    window.__VMM_TEST__.lerJSON = lerJSON;
    window.__VMM_TEST__.blocoDe = blocoDe;
    window.__VMM_TEST__.comBloco = comBloco;
    window.__VMM_TEST__.paresModelo = paresModelo;
    window.__VMM_TEST__.rotuloAnalise = rotuloAnalise;
  }
})();
