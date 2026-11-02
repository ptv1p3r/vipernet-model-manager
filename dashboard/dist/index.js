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
    if (SDK.fetchJSON) return SDK.fetchJSON(BASE + caminho, opcoes);
    // Fallback minimo: o host injeta o token de sessao no header em modo loopback.
    const tok = window.__HERMES_SESSION_TOKEN__;
    const headers = { "Content-Type": "application/json" };
    if (tok) headers["X-Hermes-Session-Token"] = tok;
    return fetch(BASE + caminho, Object.assign({ credentials: "include", headers: headers }, opcoes || {}))
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
              h("th", null, "acesso")
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
                h("td", null, h(Chip, { tone: m.acesso === "subscricao" ? "outline" : "secondary" }, m.acesso || "api"))
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
      pedir("/toggle", { method: "POST", body: JSON.stringify(corpo) })
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
      try {
        const cfg = JSON.parse(configJSON);
        pedir("/validar", { method: "POST", body: JSON.stringify(cfg) })
          .then(function (r) {
            if (r && r.erros && r.erros.length) {
              setErrosValidacao(r.erros);
              setErro("Configuração inválida: " + r.erros.join("; "));
            } else {
              setErro(null);
            }
          })
          .catch(function (e) { setErro("Erro ao validar: " + String(e && e.message ? e.message : e)); });
      } catch (e) {
        setErrosValidacao(["JSON inválido: " + String(e && e.message ? e.message : e)]);
        setErro("JSON inválido: " + String(e && e.message ? e.message : e));
      }
    }

    function salvarConfig() {
      try {
        const cfg = JSON.parse(configJSON);
        pedir("/config", { method: "POST", body: JSON.stringify(cfg) })
          .then(function () {
            setMostrarEditor(false);
            setConfigJSON("");
            setErrosValidacao([]);
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
            h("textarea", {
              value: configJSON,
              onChange: function (e) { setConfigJSON(e.target.value); },
              className: "vmm-editor-textarea",
              style: { width: "100%", height: "350px", fontFamily: "monospace", fontSize: "12px", lineHeight: "1.5", padding: "8px", border: "1px solid var(--border)", borderRadius: "4px", resize: "vertical" }
            }),
            errosValidacao.length > 0 && h(
              "div",
              { className: "vmm-editor-erros" },
              errosValidacao.map(function (e, i) { return h("div", { key: i, className: "vmm-erro-item" }, "• " + e); })
            )
          ),
          h(
            "div",
            { className: "vmm-editor-accoes" },
            h(Button, { size: "sm", variant: "outline", onClick: validarConfig }, "Validar"),
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
})();
