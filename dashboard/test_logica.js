/**
 * Teste da logica pura do painel — o caminho pela qual a UI escreve o bloco
 * `analise.classificador` no JSON da configuracao.
 *
 * O que se prova aqui e o que um teste de strings nao prova: que "herdar"
 * remove a chave (senao o perfil fica fixo ao global), que "desligado" num
 * perfil produz um bloco que vence o global sem perder o modelo declarado, e
 * que a leitura do bloco nao inventa um objecto quando este nao existe.
 *
 * Corre-se com: node dashboard/test_logica.js
 * Nao usa browser nem React: o `index.js` e carregado num `window` falso com um
 * stub de SDK, e o componente nunca e renderizado.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

let falhas = 0;
let total = 0;

function check(nome, condicao, detalhe) {
  total += 1;
  if (condicao) {
    console.log("ok    " + nome);
  } else {
    falhas += 1;
    console.log("FALHA " + nome + (detalhe ? "   [" + detalhe + "]" : ""));
  }
}

function carregar() {
  const ferramentas = {};
  // h falso que constroi a arvore: deixa o componente correr de verdade (e
  // rebentar se rebentar) em vez de so exportar funcoes puras.
  const React = {
    createElement: function (tag, props) {
      return {
        tag: typeof tag === "function" ? (tag.name || "Componente") : tag,
        props: props || {},
        filhos: Array.prototype.slice.call(arguments, 2),
      };
    },
    useState: function (v) { return [v, function () {}]; },
    useEffect: function () {},
    useCallback: function (f) { return f; },
    useMemo: function (f) { return f(); },
    useRef: function (v) { return { current: v }; },
    Fragment: "Fragment",
  };
  const registos = {};
  const janela = {
    __HERMES_PLUGIN_SDK__: { React: React, hooks: {}, components: {} },
    __HERMES_PLUGINS__: { register: function (nome, comp) { registos[nome] = comp; } },
    __VMM_TEST__: ferramentas,
  };
  const contexto = {
    window: janela,
    document: { addEventListener: function () {} },
    console: console,
    JSON: JSON,
    Object: Object,
    Array: Array,
  };
  const codigo = fs.readFileSync(path.join(__dirname, "dist", "index.js"), "utf-8");
  vm.createContext(contexto);
  vm.runInContext(codigo, contexto, { filename: "index.js" });
  ferramentas.__registos = registos;
  return ferramentas;
}

// Texto visivel da arvore, para procurar o que o cabecalho mostra.
function textoDaArvore(no) {
  if (no === null || no === undefined) return "";
  if (typeof no === "string" || typeof no === "number") return String(no);
  if (!no.filhos) return "";
  return no.filhos.map(textoDaArvore).join(" ");
}

function procurarNaArvore(no, predicado) {
  if (!no || typeof no !== "object") return null;
  if (predicado(no)) return no;
  for (const f of no.filhos || []) {
    const achado = procurarNaArvore(f, predicado);
    if (achado) return achado;
  }
  return null;
}

const T = carregar();

check("index.js expoe as funcoes puras para teste",
      typeof T.comBloco === "function" && typeof T.blocoDe === "function"
      && typeof T.paresModelo === "function" && typeof T.lerJSON === "function");

const base = {
  analise: { limiares: { media: 3, alta: 6 } },
  perfis: {
    pessoal: {
      catalogo: [
        { provider: "deepseek", modelo: "deepseek-flash" },
        { provider: "openrouter", modelo: "thinkingmachines/inkling:free" },
      ],
    },
    trabalho: {
      catalogo: [
        { provider: "openrouter", modelo: "openai/gpt-6.1-sol" },
        { provider: "deepseek", modelo: "deepseek-flash" },
      ],
    },
  },
};

// 1. blocoDe nao inventa: perfil sem bloco devolve null (isto e "herda")
check("blocoDe: perfil sem bloco e herança (null)",
      T.blocoDe(base, "pessoal") === null, String(T.blocoDe(base, "pessoal")));

// 2. global ligado
const comGlobal = T.comBloco(base, null, { activo: true, provider: "deepseek", modelo: "deepseek-flash" });
check("comBloco: global cria analise.classificador",
      comGlobal.analise.classificador.activo === true
      && comGlobal.analise.classificador.modelo === "deepseek-flash");
check("comBloco: global nao mexe nos limiares",
      comGlobal.analise.limiares.alta === 6);
check("comBloco: nao muta o objecto de origem",
      base.analise.classificador === undefined);

// 3. perfil desliga o que o global liga
const comOff = T.comBloco(comGlobal, "pessoal", { activo: false, provider: "deepseek", modelo: "deepseek-flash" });
check("comBloco: perfil desligado grava bloco proprio",
      comOff.perfis.pessoal.analise.classificador.activo === false);
check("comBloco: perfil desligado preserva o global",
      comOff.analise.classificador.activo === true);

// 4. herdar = apagar a chave (o defeito que fixava o perfil ao global)
const comHerdar = T.comBloco(comOff, "pessoal", null);
check("comBloco: herdar remove a chave do perfil",
      !(comHerdar.perfis.pessoal.analise || {}).classificador);
check("comBloco: herdar limpa o contentor analise vazio",
      comHerdar.perfis.pessoal.analise === undefined,
      JSON.stringify(comHerdar.perfis.pessoal));

// 5. herdar num perfil que tem outras chaves em analise nao as apaga
const comLimiar = T.comBloco(comGlobal, "trabalho", { activo: true, provider: "x", modelo: "y" });
comLimiar.perfis.trabalho.analise.limiares = { alta: 9 };
const soClassif = T.comBloco(comLimiar, "trabalho", null);
check("comBloco: herdar preserva o resto da analise do perfil",
      soClassif.perfis.trabalho.analise.limiares.alta === 9
      && !soClassif.perfis.trabalho.analise.classificador);

// 6. paresModelo: uniao sem repetidos, pela ordem dos perfis
const pares = T.paresModelo(base);
check("paresModelo: uniao dos catalogos sem duplicados",
      pares.length === 3, JSON.stringify(pares));
check("paresModelo: ignora entradas sem provider/modelo",
      T.paresModelo({ perfis: { a: { catalogo: [{ provider: "x" }, null] } } }).length === 0);

// 7. lerJSON: recusa o que nao e configuracao
check("lerJSON: objecto valido", T.lerJSON('{"a":1}') !== null);
check("lerJSON: array recusado", T.lerJSON("[1,2]") === null);
check("lerJSON: texto invalido recusado", T.lerJSON("{oops") === null);

// 8. rotuloAnalise: o indicador modelo/heuristica de cada cartao
check("rotuloAnalise: exposto para teste", typeof T.rotuloAnalise === "function");

const rModelo = T.rotuloAnalise({
  classificador: { modo: "modelo", provider: "inception", modelo: "mercury-3", esforco: "baixo", origem: "herdado", motivo: "" },
  origens: { modelo: 4, analise: 1 },
  respostas: 5,
  erro: "",
});
check("rotuloAnalise: modelo diz provider/modelo", rModelo.titulo === "modelo · inception/mercury-3", rModelo.titulo);
check("rotuloAnalise: modelo usa o tom de destaque", rModelo.tom === "default", rModelo.tom);
check("rotuloAnalise: modelo declara esforco e heranca", rModelo.detalhe === "esforço baixo · herdado do global", rModelo.detalhe);
check("rotuloAnalise: modelo conta a evidencia do ledger", rModelo.evidencia === "últimos 5 turnos: 4 modelo · 1 heurística", rModelo.evidencia);
check("rotuloAnalise: modelo sem erro nao alerta", rModelo.alerta === "", rModelo.alerta);

// O formato so aparece quando nao e o de fabrica: `auto` e o normal, e um chip
// a dizer "formato auto" em todos os cartoes era ruido.
const rFormatoAuto = T.rotuloAnalise({
  classificador: { modo: "modelo", provider: "inception", modelo: "mercury-3",
                   esforco: "baixo", formato_json: "auto", origem: "herdado", motivo: "" },
  origens: { modelo: 1 }, respostas: 1, erro: "",
});
check("rotuloAnalise: formato auto nao gasta chip",
      rFormatoAuto.detalhe === "esforço baixo · herdado do global", rFormatoAuto.detalhe);
const rFormatoTexto = T.rotuloAnalise({
  classificador: { modo: "modelo", provider: "inception", modelo: "mercury-3",
                   esforco: "baixo", formato_json: "off", origem: "proprio", motivo: "" },
  origens: { modelo: 1 }, respostas: 1, erro: "",
});
check("rotuloAnalise: formato declarado aparece no detalhe",
      rFormatoTexto.detalhe === "esforço baixo · formato off · próprio do perfil",
      rFormatoTexto.detalhe);

const rProprio = T.rotuloAnalise({
  classificador: { modo: "modelo", provider: "deepseek", modelo: "deepseek-flash", esforco: "", origem: "proprio" },
  origens: {},
  respostas: 0,
});
check("rotuloAnalise: proprio do perfil distingue-se de herdado", rProprio.detalhe === "próprio do perfil", rProprio.detalhe);
check("rotuloAnalise: sem turnos nao inventa evidencia", rProprio.evidencia === "", rProprio.evidencia);

const rFalhou = T.rotuloAnalise({
  classificador: { modo: "modelo", provider: "inception", modelo: "mercury-3", origem: "herdado" },
  origens: { analise: 3 },
  respostas: 3,
  erro: "resposta sem nivel reconhecel (length 320tok)",
  rasto: { provider: "inception", modelo: "mercury-3", falhou: "ValueError", lido: null },
});
check("rotuloAnalise: falha da ultima leitura avisa e mostra o erro",
      /o classificador falhou/.test(rFalhou.alerta) && /length 320tok/.test(rFalhou.alerta), rFalhou.alerta);

// Um motivo de config ANTIGO nao pode aparecer como «sem efeito» de hoje: foi
// o que manteve no cartao, duas horas, um `provider_indisponivel` que a
// declaracao do provider as 12:45 ja tinha resolvido. Vai a historico datado.
const rMotivoAntigo = T.rotuloAnalise({
  classificador: { modo: "modelo", provider: "inception", modelo: "mercury-3",
                   esforco: "baixo", origem: "herdado", motivo: "" },
  origens: { modelo: 2 }, respostas: 2, erro: "",
  historico: { lido: null, motivo: "outro classificador",
               motivo_antigo: { motivo: "provider_indisponivel: Unknown provider 'inception'",
                                ts: "2026-11-03T11:29:32+00:00", idade_s: 7691,
                                porque: "anterior à config actual" } },
});
check("rotuloAnalise: motivo antigo nao aparece como estado",
      rMotivoAntigo.detalhe.indexOf("sem efeito —") === -1, rMotivoAntigo.detalhe);
check("rotuloAnalise: motivo antigo aparece datado como historico",
      /histórico: provider_indisponivel há 2 h — anterior à config actual/.test(rMotivoAntigo.detalhe),
      rMotivoAntigo.detalhe);

// Um motivo recente continua a ser estado: a regra nao pode esconder o erro vivo.
const rMotivoActual = T.rotuloAnalise({
  classificador: { modo: "modelo", provider: "inception", modelo: "mercury-3",
                   esforco: "baixo", origem: "herdado" },
  motivo: "provider_indisponivel: Unknown provider 'x'",
  origens: { modelo: 1 }, respostas: 1, erro: "",
});
check("rotuloAnalise: motivo recente continua a ser estado",
      /sem efeito — provider_indisponivel/.test(rMotivoActual.detalhe), rMotivoActual.detalhe);

// Sem `ts` nao ha hora a mostrar; com `ts` o aviso diz quando — um erro de ha
// meia hora nao pode parecer que aconteceu agora.
const rFalhouComHora = T.rotuloAnalise({
  classificador: { modo: "modelo", provider: "inception", modelo: "mercury-3", origem: "herdado" },
  erro: "resposta sem nivel reconhecel (length 320tok)",
  rasto: { provider: "inception", modelo: "mercury-3", falhou: "ValueError",
           ts: new Date(Date.now() - 12 * 60000).toISOString() },
});
check("rotuloAnalise: o alerta diz quando falhou",
      /falhou às \d{2}:\d{2} \(há 12 min\)/.test(rFalhouComHora.alerta), rFalhouComHora.alerta);

// A taxa do classificador e evidencia, nao decoracao: diz se o erro e isolado
// ou se o modelo falha com frequencia.
const rComTaxa = T.rotuloAnalise({
  classificador: { modo: "modelo", provider: "inception", modelo: "mercury-3", origem: "herdado" },
  origens: { modelo: 4 }, respostas: 4,
  taxa: { leituras: 184, falhas: 19, ultima_falha: "2026-11-02T19:38:07+00:00" },
  rasto: { provider: "inception", modelo: "mercury-3", lido: "alta", ms: 1600, falhou: "" },
});
check("rotuloAnalise: mostra a taxa de leituras do classificador",
      /184\/203 leituras ok \(9% falhas\)/.test(rComTaxa.evidencia), rComTaxa.evidencia);

// O mesmo erro, mas a ultima leitura correu bem: nao se avisa. Um erro antigo
// nao pode ficar a assombrar um classificador que voltou a acertar.
const rRecuperado = T.rotuloAnalise({
  classificador: { modo: "modelo", provider: "inception", modelo: "mercury-3", origem: "herdado" },
  origens: { modelo: 4 },
  respostas: 4,
  erro: "resposta sem nivel reconhecel (length 320tok)",
  rasto: { provider: "inception", modelo: "mercury-3", lido: "alta", ms: 1620.4, falhou: "" },
});
check("rotuloAnalise: erro antigo nao alerta se a ultima leitura acertou",
      rRecuperado.alerta === "", rRecuperado.alerta);
check("rotuloAnalise: mostra o que o modelo leu e em quanto tempo",
      /leu alta em 1620 ms/.test(rRecuperado.detalhe), rRecuperado.detalhe);

const rDesligado = T.rotuloAnalise({
  classificador: { modo: "heuristica", origem: "herdado", motivo: "desligado", provider: "inception", modelo: "mercury-3" },
  origens: { analise: 2, tarefa: 1 },
  respostas: 3,
});
check("rotuloAnalise: desligado nao diz heuristica sem modelo", rDesligado.detalhe === "classificador desligado — decide a heurística", rDesligado.detalhe);
check("rotuloAnalise: desligado conta tarefa e heuristica", rDesligado.evidencia === "últimos 3 turnos: 2 heurística · 1 tarefa", rDesligado.evidencia);
check("rotuloAnalise: desligado nao e alerta", rDesligado.alerta === "" && rDesligado.tom === "secondary");

const rInvalido = T.rotuloAnalise({
  classificador: { modo: "heuristica", origem: "proprio", motivo: "analise.classificador: activo exige provider e modelo" },
});
check("rotuloAnalise: bloco invalido explica porque nao decide",
      /sem efeito/.test(rInvalido.detalhe) && /exige provider/.test(rInvalido.detalhe), rInvalido.detalhe);

const rVazio = T.rotuloAnalise(null);
check("rotuloAnalise: sem dados nao rebenta", rVazio.modo === "heuristica" && rVazio.titulo === "heurística");

// --- identidade do plugin no cabecalho -------------------------------------
// A versao vem do plugin.yaml pelo endpoint, nao de uma copia escrita no JS:
// uma copia divergiu e o painel mostrou 3.11.2 com o disco em 3.11.3.

const mOk = T.metaPlugin({
  plugin: {
    versao: "3.13.8",
    autor: "Pedro Tiago de Jesus Estevanez Roldan",
    github: "https://github.com/ptv1p3r",
    versao_manifest: "3.13.8",
  },
  versao_painel: "3.13.8",
});
check("metaPlugin: versao do plugin", mOk.versao === "3.13.8", mOk.versao);
check("metaPlugin: autor completo", mOk.autor === "Pedro Tiago de Jesus Estevanez Roldan", mOk.autor);
check("metaPlugin: github do autor", mOk.github === "https://github.com/ptv1p3r", mOk.github);
check("metaPlugin: sem deriva", mOk.divergente === false);

const mDeriva = T.metaPlugin({
  plugin: { versao: "3.13.8", autor: "X", github: "https://github.com/ptv1p3r", versao_manifest: "3.13.2" },
});
check("metaPlugin: deriva detectada quando manifest difere", mDeriva.divergente === true);
check("metaPlugin: deriva mostra o numero do manifest", mDeriva.manifest === "3.13.2", mDeriva.manifest);

// Endpoint antigo (sem bloco plugin) nao pode deixar o cabecalho em branco.
const mLegado = T.metaPlugin({ versao_painel: "3.12.0" });
check("metaPlugin: sem bloco plugin usa a versao do painel", mLegado.manifest === "3.12.0", mLegado.manifest);
check("metaPlugin: sem bloco plugin nao inventa versao de plugin", mLegado.versao === "—", mLegado.versao);
check("metaPlugin: sem github nao mostra link", mLegado.github === "");
check("metaPlugin: sem deriva quando so ha um numero", mLegado.divergente === false);

const mNulo = T.metaPlugin(null);
check("metaPlugin: sem estado nao rebenta", mNulo.versao === "—" && mNulo.autor === "—" && mNulo.divergente === false);

// --- o cabecalho renderiza a identidade ------------------------------------
// Prova que o que o utilizador ve e o que o endpoint manda: versao, autor e
// link do repositorio, sem React a serio.

const cab = T.linhaIdentidade(mOk);
check("render: cabecalho e o bloco de identidade", cab.tag === "div" && cab.props.className === "vmm-meta");

const textoCab = textoDaArvore(cab);
check("render: cabecalho mostra a versao", textoCab.indexOf("v3.13.8") !== -1, textoCab);
check("render: cabecalho mostra o autor", textoCab.indexOf("Pedro Tiago de Jesus Estevanez Roldan") !== -1, textoCab);

const link = procurarNaArvore(cab, (n) => n.tag === "a");
check("render: cabecalho tem link para o github", !!link && link.props.href === "https://github.com/ptv1p3r",
      link ? link.props.href : "sem link");
check("render: link abre noutro separador", !!link && link.props.target === "_blank");
check("render: link nao passa o opener", !!link && /noopener/.test(link.props.rel || ""));
check("render: etiqueta do link e curta", link && link.filhos[0] === "github.com/ptv1p3r",
      link ? String(link.filhos[0]) : "");

const cabSemGithub = T.linhaIdentidade(mLegado);
check("render: sem github nao desenha link",
      procurarNaArvore(cabSemGithub, (n) => n.tag === "a") === null);

const cabDeriva = T.linhaIdentidade(mDeriva);
const avisoDeriva = procurarNaArvore(cabDeriva, (n) => n.props && n.props.className === "vmm-meta-deriva");
check("render: deriva aparece no cabecalho", !!avisoDeriva, textoDaArvore(cabDeriva));

// O componente inteiro, na primeira pintura (antes do /estado responder).
const Pagina = (T.__registos || {})["vipernet-model-manager"];
check("render: o painel regista-se no SDK", typeof Pagina === "function");
let erroRender = "";
try {
  Pagina();
} catch (e) {
  erroRender = String(e && e.message ? e.message : e);
}
check("render: primeira pintura sem estado nao rebenta", erroRender === "", erroRender);

// --- baseDoBloco: o bloco inteiro sobrevive a uma mudanca de campo ----------
// A lista fixa de chaves perdia tudo o que nao estivesse enumerada. Estes
// checks cobrem as chaves que o formulario nao conhece, para a proxima chave
// nova nao voltar a ser apagada ao mudar o provider no painel.
const blocoRico = {
  activo: true,
  provider: "openrouter",
  modelo: "~typesafe/jev-latest",
  via: "auto",
  preset: "sistema-um",
  estado: "medidas",
  confianca_min: 0,
  temperatura: null,
  max_tokens_campo: "max_completion_tokens",
  caminho: "/api/v9/chat",
  caminho_decisoes: "/api/alpha/decisions",
  timeout_s: 12,
  max_tokens: 500,
  esforco: "",
  formato_json: "auto",
  parametros_extra: { verbosity: "low" },
  headers_extra: {},
};
const chavesAntes = Object.keys(blocoRico).length;
const preservado = T.baseDoBloco(blocoRico);
check("baseDoBloco: identidade e rota ficam",
      preservado.activo === true && preservado.provider === "openrouter"
      && preservado.modelo === "~typesafe/jev-latest", JSON.stringify(preservado));
check("baseDoBloco: via/preset/estado sobrevivem",
      preservado.via === "auto" && preservado.preset === "sistema-um"
      && preservado.estado === "medidas", JSON.stringify(preservado));
check("baseDoBloco: confianca_min 0 sobrevive (nao e campo vazio)",
      preservado.confianca_min === 0, String(preservado.confianca_min));
check("baseDoBloco: temperatura null sobrevive e continua null",
      Object.prototype.hasOwnProperty.call(preservado, "temperatura")
      && preservado.temperatura === null, JSON.stringify(preservado.temperatura));
check("baseDoBloco: max_tokens_campo e caminhos sobrevivem",
      preservado.max_tokens_campo === "max_completion_tokens"
      && preservado.caminho === "/api/v9/chat"
      && preservado.caminho_decisoes === "/api/alpha/decisions", JSON.stringify(preservado));
check("baseDoBloco: parametros_extra e headers_extra sobrevivem",
      preservado.parametros_extra && preservado.parametros_extra.verbosity === "low"
      && preservado.headers_extra && Object.keys(preservado.headers_extra).length === 0,
      JSON.stringify(preservado));
check("baseDoBloco: esforco vazio sai do JSON", !("esforco" in preservado),
      JSON.stringify(preservado));
check("baseDoBloco: nao muta o bloco de origem",
      blocoRico.provider === "openrouter" && blocoRico.esforco === ""
      && Object.keys(blocoRico).length === chavesAntes,
      String(Object.keys(blocoRico).length) + " vs " + chavesAntes);

// ---- estrategia: rotulo e opcoes fechadas -------------------------------
// O rotulo tem de dizer as duas coisas: o que decide e de onde vem. Um cartao
// que mostra "rodizio" sem dizer "herdada do global" esconde a razao pela qual
// mudar o global muda aquele perfil.
check("rotuloEstrategia: rotacao herdada do global",
      T.rotuloEstrategia({ modo: "rodizio", por: "sessao", origem: "herdado" })
      === "rotação por sessão · herdada do global",
      T.rotuloEstrategia({ modo: "rodizio", por: "sessao", origem: "herdado" }));

check("rotuloEstrategia: prioridade propria do perfil",
      T.rotuloEstrategia({ modo: "prioridade", origem: "proprio" })
      === "prioridade (primeira da lista) · própria do perfil",
      T.rotuloEstrategia({ modo: "prioridade", origem: "proprio" }));

check("rotuloEstrategia: rotacao por turno",
      T.rotuloEstrategia({ modo: "rodizio", por: "turno", origem: "global" })
      === "rotação por turno · definida globalmente",
      T.rotuloEstrategia({ modo: "rodizio", por: "turno", origem: "global" }));

check("rotuloEstrategia: sem bloco diz por omissao",
      T.rotuloEstrategia(null) === "rotação por sessão · por omissão",
      T.rotuloEstrategia(null));

const opcoesEstado = {
  opcoes: {
    estrategia: {
      modo: [{ valor: "rodizio", rotulo: "rotação" },
             { valor: "prioridade", rotulo: "prioridade (primeira da lista)" }],
      por: [{ valor: "turno", rotulo: "por turno" },
            { valor: "sessao", rotulo: "por sessão" }],
    },
  },
};
check("opcoesEstrategia: le as listas do /estado",
      T.opcoesEstrategia(opcoesEstado).modo.length === 2
      && T.opcoesEstrategia(opcoesEstado).por[1].valor === "sessao");
check("opcoesEstrategia: payload sem opcoes nao rebenta",
      JSON.stringify(T.opcoesEstrategia(null)) === "{}");

// ---- superficies: regra -> valor do select -----------------------------
const cat = [
  { id: "a", provider: "deepseek", modelo: "deepseek-flash" },
  { id: "b", provider: "nvidia", modelo: "nemotron:free" },
];
check("valorDaRegra: entrada",
      T.valorDaRegra({ entrada: "b" }) === "entrada:b");
check("valorDaRegra: nivel",
      T.valorDaRegra({ nivel: "alta" }) === "nivel:alta");
check("valorDaRegra: provider/modelo",
      T.valorDaRegra({ provider: "x", modelo: "y" }) === "x/y");
check("valorDaRegra: anulada",
      T.valorDaRegra({ activo: false }) === "__nunca");
check("valorDaRegra: sem regra", T.valorDaRegra(null) === "");

const alvos = T.opcoesAlvo(cat, ["leve", "alta"]);
check("opcoesAlvo: herdar vem primeiro e sem regra e o valor vazio",
      alvos[0].valor === "" && alvos[1].valor === "__nunca", JSON.stringify(alvos[0]));
check("opcoesAlvo: uma opcao por entrada do catalogo",
      alvos.filter(function (o) { return o.valor.indexOf("entrada:") === 0; }).length === 2);
check("opcoesAlvo: o rotulo diz provider/modelo",
      alvos.filter(function (o) { return o.valor === "entrada:b"; })[0].rotulo
      === "b · nvidia/nemotron:free");
check("opcoesAlvo: niveis no fim",
      alvos[alvos.length - 1].valor === "nivel:alta", JSON.stringify(alvos[alvos.length - 1]));
check("opcoesAlvo: catalogo vazio nao rebenta",
      T.opcoesAlvo(null, null).length === 2);

check("rotuloRegra: entrada resolvida no catalogo",
      T.rotuloRegra({ entrada: "a" }, cat) === "a (deepseek/deepseek-flash)");
check("rotuloRegra: entrada fora do catalogo e dita",
      T.rotuloRegra({ entrada: "zz" }, cat) === "zz (fora do catálogo)");
check("rotuloRegra: anulada", T.rotuloRegra({ activo: false }, cat) === "nunca");

// ---------------------------------------------------------------- render
// O corpo dos componentes corre em node, com o `h` do SDK. Um nome que nao
// existe no escopo (ja aconteceu com `perfil`) rebenta aqui, nao no browser
// do dono.
function tentar(nome, fn) {
  try {
    const r = fn();
    check(nome, true);
    return r;
  } catch (e) {
    check(nome, false, e && e.message ? e.message : String(e));
    return null;
  }
}

const perfilCompleto = {
  activo: true,
  catalogo: cat,
  tarefas: { relatorio: { termos: ["relatorio"], prioridade: 1 } },
  superficies: {
    cron: { entrada: "a", origem_regra: "perfil" },
    cli: { nivel: "alta", origem_regra: "global" },
  },
  estrategia: { modo: "rodizio", por: "sessao", origem: "herdado" },
  analise: { modelo: "inception/mercury-3", origem: "global" },
};
const propsCartao = {
  nome: "default",
  dados: perfilCompleto,
  mais: { niveis: ["leve", "media", "alta"], superficies: ["cron", "cli"] },
  aGravar: false,
  onEstrategia: function () {},
  onSuperficie: function () {},
  onTarefa: function () {},
  onToggle: function () {},
  onModelo: function () {},
};

const arvore = tentar("render: o cartao de perfil nao rebenta", function () {
  return T.CartaoPerfil(propsCartao);
});
function temTexto(no, texto) {
  if (no === null || no === undefined) return false;
  if (typeof no === "string" || typeof no === "number") return String(no).indexOf(texto) >= 0;
  if (Array.isArray(no)) return no.some(function (n) { return temTexto(n, texto); });
  // O `h` falso guarda os filhos em `filhos`; um `props.children` tambem conta.
  if (no.filhos && no.filhos.some(function (n) { return temTexto(n, texto); })) return true;
  if (no.props) {
    if (temTexto(no.props.children, texto)) return true;
    // Rotulos vivem em props (`title`, `rotulo`, `label`): procurar so nos
    // filhos dava um falso negativo num texto que o browser mostra.
    return Object.keys(no.props).some(function (k) { return temTexto(no.props[k], texto); });
  }
  return false;
}

/** Procura, na arvore, um no cujo `tag` seja o nome de um componente. */
function acharComponente(no, nome) {
  if (!no || typeof no !== "object") return null;
  if (Array.isArray(no)) {
    for (let i = 0; i < no.length; i++) {
      const r = acharComponente(no[i], nome);
      if (r) return r;
    }
    return null;
  }
  if (no.tag === nome) return no;
  const r = acharComponente(no.filhos, nome) || acharComponente(no.props && no.props.children, nome);
  return r || null;
}
check("render: o cartao mostra a estrategia em vigor",
      temTexto(arvore, "rotação por sessão"), "");
const noSuperficies = acharComponente(arvore, "Superficies");
check("render: o cartao de perfil ja nao tem o editor de superficies",
      noSuperficies === null,
      "o cartao nao deve ser painel de controlo — a configuracao vive no editor");
check("render: o cartao nao tem select de estrategia (so leitura)",
      acharComponente(arvore, "Seletor") === null,
      "o cartao mostra o que esta a valer, nao deixa escolher");
check("render: a regra global e dita como tal", temTexto(arvore, "global"), "");

tentar("render: cartao sem catalogo, sem superficies e sem estrategia nao rebenta",
       function () {
         return T.CartaoPerfil({ nome: "vazio", dados: { activo: false }, mais: {},
                                 aGravar: true, onEstrategia: function () {},
                                 onSuperficie: function () {} });
       });

const blocoSup = tentar("render: o bloco de superficies nao rebenta", function () {
  return T.Superficies({ perfil: "default", regras: perfilCompleto.superficies,
                         catalogo: cat, niveis: ["leve", "media", "alta"],
                         conhecidas: ["cron", "cli"], aGravar: false,
                         onMudar: function () {} });
});
check("render: uma linha por regra", (function () {
  const linhas = [];
  (function varrer(no) {
    if (!no || typeof no !== "object") return;
    if (Array.isArray(no)) return no.forEach(varrer);
    if (no.props && no.props.className === "vmm-sup-linha") linhas.push(no);
    if (no.filhos) no.filhos.forEach(varrer);
    if (no.props) varrer(no.props.children);
  })(blocoSup);
  return linhas.length === 2;
})(), "esperava 2 linhas");

// ---- estrategia no editor: a mesma linha do classificador ------------------
check("estrategiaDe: nao inventa bloco global quando nao existe",
      T.estrategiaDe(base, null) === null, String(T.estrategiaDe(base, null)));
check("estrategiaDe: nao inventa bloco de perfil quando nao existe",
      T.estrategiaDe(base, "pessoal") === null, String(T.estrategiaDe(base, "pessoal")));

const comEst = T.comEstrategia(base, null, { modo: "rodizio", por: "turno" }, false);
check("comEstrategia: global cria o bloco com os dois eixos",
      comEst.estrategia && comEst.estrategia.modo === "rodizio"
      && comEst.estrategia.por === "turno", JSON.stringify(comEst.estrategia));
check("comEstrategia: nao muta o objecto de origem",
      base.estrategia === undefined && base.perfis.pessoal.estrategia === undefined);
check("comEstrategia: nao perde o catalogo do perfil",
      comEst.perfis.pessoal.catalogo.length === 2);

const comEstPerfil = T.comEstrategia(comEst, "pessoal", { modo: "prioridade" }, false);
check("comEstrategia: perfil ganha bloco proprio",
      comEstPerfil.perfis.pessoal.estrategia.modo === "prioridade");
check("comEstrategia: perfil nao mexe no global",
      comEstPerfil.estrategia.modo === "rodizio");

const semEstPerfil = T.comEstrategia(comEstPerfil, "pessoal", null, true);
check("comEstrategia: herdar remove a chave do perfil",
      semEstPerfil.perfis.pessoal.estrategia === undefined
      && semEstPerfil.estrategia.modo === "rodizio");
check("comEstrategia: herdar no global remove o bloco",
      T.comEstrategia(comEst, null, null, true).estrategia === undefined);

// Um campo vazio nao fica gravado como escolha propria: sai do bloco.
const comVazio = T.comEstrategia(comEstPerfil, "pessoal", { modo: "" }, false);
check("comEstrategia: campo vazio sai do bloco",
      comVazio.perfis.pessoal.estrategia === undefined, JSON.stringify(comVazio.perfis.pessoal));
const comPorVazio = T.comEstrategia(comEst, null, { por: "" }, false);
check("comEstrategia: `por` vazio mantem o `modo` e perde o eixo",
      comPorVazio.estrategia.modo === "rodizio" && comPorVazio.estrategia.por === undefined,
      JSON.stringify(comPorVazio.estrategia));

// ---- render: a estrategia aparece onde o classificador aparece -------------
const opcoesReais = {
  modo: [{ valor: "rodizio", rotulo: "rotação" }, { valor: "prioridade", rotulo: "prioridade (primeira da lista)" }],
  por: [{ valor: "turno", rotulo: "por turno" }, { valor: "sessao", rotulo: "por sessão" }],
};

function recolher(no, predicado, out) {
  out = out || [];
  if (!no || typeof no !== "object") return out;
  if (Array.isArray(no)) { no.forEach(function (f) { recolher(f, predicado, out); }); return out; }
  if (predicado(no)) out.push(no);
  if (no.filhos) no.filhos.forEach(function (f) { recolher(f, predicado, out); });
  if (no.props) recolher(no.props.children, predicado, out);
  return out;
}

function renderSecao(config, opcoes, estado) {
  return T.SecaoClassificador({
    configJSON: JSON.stringify(config),
    opcoes: opcoes || {},
    estado: estado || {},
    onMudar: function () {},
    onSuperficie: function () {},
  });
}

const cfgSecao = {
  analise: { limiares: { media: 3, alta: 6 }, classificador: { activo: true, provider: "inception", modelo: "mercury-3" } },
  estrategia: { modo: "rodizio", por: "sessao" },
  perfis: { pessoal: { catalogo: [] }, trabalho: { catalogo: [] } },
};
const arvoreSecao = renderSecao(cfgSecao, opcoesReais);

// ---- ensaio A/B na pagina de configuracao ---------------------------------
// A informacao existia em dois ficheiros (interruptor no config, MODELOS no
// pre-registo selado) e nao aparecia em nenhum formulario: quem abria a
// configuracao via um editor de JSON. Estes casos provam que os bracos sao
// editaveis onde se edita o resto, e que o selo e o aviso nao desaparecem.
const estadoSombra = {
  perfis: {
    default: { catalogo: [{ modelo: "modelo-do-catalogo", provider: "p", complexidade: ["leve"] }] },
  },
  sombra: {
    activo: true,
    vida_ttl_h: 24,
    bracos: { a: "deepseek-flash", b: "nvidia/nemotron-3-ultra-550b-a55b:free" },
    // O provider faz parte do desenho: sem ele a sonda adivinhava a credencial
    // pelo nome do modelo. A lista e fechada e sai do catalogo.
    providers: { a: "deepseek", b: "openrouter" },
    providers_declarados: true,
    providers_disponiveis: ["deepseek", "openrouter"],
    selo_valido: true,
    selo: "5275bbf6eba15dbb" + "0".repeat(48),
    ensaio: "taxa de falha por chamada de ferramenta",
    p0: 0.0463, margem: 0.01, n_por_braco: 18536, dias_estimados: 2.3,
    pre_registo: "/tmp/pre-registo-20261105b.json",
    pre_registo_recente: "/tmp/pre-registo-20261105b.json",
    pre_registo_recente_selado: true,
    vida: {
      medida_ts: "2026-11-05T10:29:59+00:00",
      bracos: {
        a: { modelo: "deepseek-flash", ok: true, http: 200, motivo: "" },
        b: { modelo: "nvidia/nemotron-3-ultra-550b-a55b:free", ok: false, http: 404,
             motivo: "unavailable for free" },
      },
      todos_vivos: false,
    },
  },
};
// O harness nao executa componentes-funcao (um `h(SecaoSombra)` fica um no com
// o tag do nome), por isso encontra-se o no dentro da seccao e chama-se a
// funcao a mao — a mesma convencao que ja se usa para a SecaoClassificador.
function renderSombra(estado, extra) {
  const arvore = renderSecao({ perfis: { default: {} } }, {}, estado);
  const no = recolher(arvore, function (n) { return n.tag === "SecaoSombra"; });
  if (!no.length) return null;
  const props = Object.assign({}, no[0].props, extra || {});
  return T.SecaoSombra(props);
}

const arvoreSombraCompleta = renderSombra(estadoSombra);
check("render: a seccao monta o bloco do ensaio", arvoreSombraCompleta !== null);

const textosSombra = arvoreSombraCompleta ? textoDaArvore(arvoreSombraCompleta) : "";
const inputsSombra = arvoreSombraCompleta ? recolher(arvoreSombraCompleta,
  function (no) { return no.tag === "input"; }) : [];
const valoresSombra = inputsSombra.map(function (n) { return n.props.value; });

check("render: o ensaio tem bloco proprio no editor",
      textosSombra.indexOf("Ensaio A/B em sombra") !== -1, textosSombra.slice(0, 200));
check("render: os dois modelos dos bracos estao nos campos",
      valoresSombra.indexOf("deepseek-flash") !== -1
      && valoresSombra.indexOf("nvidia/nemotron-3-ultra-550b-a55b:free") !== -1,
      JSON.stringify(valoresSombra));
check("render: mostra os numeros do ensaio (p0, margem, n, dias)",
      textosSombra.indexOf("0.0463") !== -1 && textosSombra.indexOf("0.01") !== -1
      && textosSombra.indexOf("18536") !== -1 && textosSombra.indexOf("2.3") !== -1,
      textosSombra);
check("render: mostra o selo do pre-registo",
      textosSombra.indexOf("5275bbf6eba15dbb") !== -1);
check("render: mostra a sonda de vida de cada braco, com o motivo da falha",
      textosSombra.indexOf("vivo") !== -1 && textosSombra.indexOf("morto") !== -1
      && textosSombra.indexOf("unavailable for free") !== -1, textosSombra);
check("render: as sugestoes de modelo vem do catalogo real",
      (function () {
        const dl = recolher(arvoreSombraCompleta, function (n) { return n.tag === "datalist"; });
        if (!dl.length) return false;
        // `filhos` no harness, `props.children` no React real.
        const dentro = recolher(dl[0], function (n) { return n.tag === "option"; });
        const opcoes = dentro.length ? dentro : recolher(
          { tag: "x", props: { children: (dl[0].props || {}).children }, filhos: dl[0].filhos },
          function (n) { return n.tag === "option"; });
        return opcoes.length === 1 && opcoes[0].props.value === "modelo-do-catalogo";
      })());

// O provider nao se adivinha. Estes casos provam que o formulario o pede numa
// lista fechada (tirada do catalogo), que o mostra preenchido com o que esta
// selado, e que diz em voz alta quando o selo nao o declara.
const selectsSombra = arvoreSombraCompleta ? recolher(arvoreSombraCompleta,
  function (no) { return no.tag === "select"; }) : [];
const valoresSelects = selectsSombra.map(function (n) { return n.props.value; });
check("render: ha um select de provider por braco", selectsSombra.length === 2,
      "obtive " + selectsSombra.length);
check("render: o provider selado vem preenchido nos dois selects",
      valoresSelects.indexOf("deepseek") !== -1
      && valoresSelects.indexOf("openrouter") !== -1,
      JSON.stringify(valoresSelects));
check("render: a lista de providers e fechada e tirada do catalogo",
      (function () {
        if (!selectsSombra.length) return false;
        const opcoes = recolher(
          { tag: "x", props: { children: (selectsSombra[0].props || {}).children },
            filhos: selectsSombra[0].filhos },
          function (n) { return n.tag === "option"; });
        const vals = opcoes.map(function (o) { return o.props.value; }).sort();
        return JSON.stringify(vals) === JSON.stringify(["", "deepseek", "openrouter"]);
      })(),
      JSON.stringify((selectsSombra[0] || { props: {} }).props));
check("render: com provider declarado, diz qual e o do selo",
      textosSombra.indexOf("provider declarado no selo") !== -1, textosSombra);

// Caso negativo: selo antigo, sem provider. O painel nao pode mostrar um
// provider que nao existe — tem de dizer que o selo nao o declara.
const estadoSemProvider = JSON.parse(JSON.stringify(estadoSombra));
delete estadoSemProvider.sombra.providers;
estadoSemProvider.sombra.providers_declarados = false;
const arvoreSemProvider = renderSombra(estadoSemProvider);
check("render: selo sem provider e dito, nao inventado",
      arvoreSemProvider !== null
      && textoDaArvore(arvoreSemProvider).indexOf("NÃO declara o provider") !== -1,
      arvoreSemProvider ? textoDaArvore(arvoreSemProvider).slice(0, 400) : "sem arvore");


const botoesSombra = arvoreSombraCompleta ? recolher(arvoreSombraCompleta,
  function (no) {
    const filhos = (no.filhos || []).concat((no.props || {}).children || []);
    return no.tag === "button" && filhos.indexOf("Re-selar braços") !== -1;
  }) : [];
check("render: o botao de re-selar existe", botoesSombra.length === 1,
      "obtive " + botoesSombra.length);
check("render: re-selar comeca desligado ate confirmar",
      botoesSombra.length === 1 && botoesSombra[0].props.disabled === true);

// A classe do aviso tem de aparecer quando o ensaio grava: sem isso, gravar
// com sucesso e nao ver nada e indistinguivel de um botao morto.
const temasAviso = renderSombra(estadoSombra, { aviso: "braços re-selados" });
check("render: o aviso de sucesso aparece no sitio do gesto",
      temasAviso !== null && textoDaArvore(temasAviso).indexOf("braços re-selados") !== -1);

// Caso negativo do selo: com selo invalido o painel avisa em vez de mostrar os
// bracos como aprovados.
const estadoSombraMau = JSON.parse(JSON.stringify(estadoSombra));
estadoSombraMau.sombra.selo_valido = false;
estadoSombraMau.sombra.pre_registo_recente_selado = false;
estadoSombraMau.sombra.bracos = { a: null, b: null };
const arvoreSombraMau = renderSombra(estadoSombraMau);
const textosSombraMau = arvoreSombraMau ? textoDaArvore(arvoreSombraMau) : "";
check("render: selo invalido e dito por extenso, nao escondido",
      textosSombraMau.indexOf("NÃO bate") !== -1, textosSombraMau.slice(0, 260));

// Terceiro caso, distinto do anterior: NAO existe pre-registo nenhum. O painel
// nao pode confundir "nao ha" com "ha um e o selo nao bate" — sao acoes
// diferentes (criar vs. investigar quem editou o ficheiro).
const estadoSemRegisto = JSON.parse(JSON.stringify(estadoSombra));
estadoSemRegisto.sombra.selo_valido = false;
estadoSemRegisto.sombra.selo = null;
estadoSemRegisto.sombra.pre_registo = "";
estadoSemRegisto.sombra.pre_registo_recente = "";
estadoSemRegisto.sombra.pre_registo_recente_selado = null;
estadoSemRegisto.sombra.bracos = { a: null, b: null };
const arvoreSemRegisto = renderSombra(estadoSemRegisto);
const textosSemRegisto = arvoreSemRegisto ? textoDaArvore(arvoreSemRegisto) : "";
check("render: distingue 'nao ha pre-registo' de 'selo nao bate'",
      textosSemRegisto.indexOf("sem pré-registo") !== -1
      && textosSemRegisto.indexOf("NÃO bate") === -1,
      textosSemRegisto.slice(0, 260));

check("render: os blocos distintos — Complexidade, Estrategia e Superficies",
      textoDaArvore(arvoreSecao).indexOf("Complexidade") !== -1
      && textoDaArvore(arvoreSecao).indexOf("Estratégia") !== -1
      && textoDaArvore(arvoreSecao).indexOf("Superfícies") !== -1);

// O bloco de superficies vive no editor e mostra um por perfil, com o catalogo
// desse perfil — a mesma informacao que o cartao mostrava, mas onde se edita.
const estadoComSuperficies = {
  perfis: {
    default: { catalogo: [{ modelo: "m1", provider: "p1", complexidade: "leve" }],
               superficies: { cron: "m1" } },
    pessoal: { catalogo: [], superficies: {} },
  },
  opcoes: { niveis: ["leve", "media", "alta"], superficies: ["cli", "cron"] },
};
const blocosSup = recolher(
  renderSecao({ perfis: { default: {}, pessoal: {} } }, {}, estadoComSuperficies),
  function (no) { return no.tag === "Superficies"; });
check("render: o editor tem um bloco de superficies por perfil",
      blocosSup.length === 2, "obtive " + blocosSup.length);
check("render: o bloco de superficies recebe as regras do perfil",
      blocosSup.length > 0 && Object.keys(blocosSup[0].props.regras || {}).join(",") === "cron",
      JSON.stringify(blocosSup.length && blocosSup[0].props.regras));
check("render: o bloco de superficies recebe o catalogo do perfil",
      blocosSup.length > 0 && (blocosSup[0].props.catalogo || []).length === 1, "");

const seletoresModo = recolher(arvoreSecao, function (no) {
  return no.tag === "Seletor" && String(no.props && no.props.rotulo || "").indexOf("estratégia de") === 0;
});
check("render: um select de estrategia por linha (global + 2 perfis)",
      seletoresModo.length === 3, "obtive " + seletoresModo.length);

const globalSel = seletoresModo.filter(function (no) {
  return no.props.rotulo === "estratégia de todos os perfis";
})[0];
check("render: o global mostra o modo declarado",
      globalSel && globalSel.props.valor === "rodizio", globalSel && globalSel.props.valor);

const perfilSel = seletoresModo.filter(function (no) {
  return no.props.rotulo === "estratégia de pessoal";
})[0];
check("render: o perfil sem bloco mostra herdar do global",
      perfilSel && perfilSel.props.valor === ""
      && perfilSel.props.opcoes[0].rotulo.indexOf("herdar do global") !== -1
      && perfilSel.props.opcoes[0].rotulo.indexOf("rotação") !== -1,
      perfilSel && JSON.stringify(perfilSel.props.opcoes[0]));

check("render: as opcoes de modo vem do backend (rotação e prioridade)",
      globalSel && globalSel.props.opcoes.map(function (o) { return o.valor; }).join(",")
        === ",rodizio,prioridade");
check("render: sem opcoes do backend nao se inventam valores",
      (function () {
        const semOpcoes = recolher(renderSecao(cfgSecao, {}), function (no) {
          return no.tag === "Seletor" && no.props.rotulo === "estratégia de todos os perfis";
        })[0];
        return semOpcoes && semOpcoes.props.opcoes.length === 1 && semOpcoes.props.opcoes[0].valor === "";
      })());

const rotacaoSel = recolher(arvoreSecao, function (no) {
  return no.tag === "Seletor" && String(no.props && no.props.rotulo || "").indexOf("rotação de") === 0;
});
check("render: o eixo da rotacao esta desactivado em prioridade", (function () {
  const cfgPrioridade = JSON.parse(JSON.stringify(cfgSecao));
  cfgPrioridade.perfis.pessoal.estrategia = { modo: "prioridade" };
  const sel = recolher(renderSecao(cfgPrioridade, opcoesReais), function (no) {
    return no.tag === "Seletor" && no.props.rotulo === "rotação de pessoal";
  })[0];
  const outro = recolher(renderSecao(cfgPrioridade, opcoesReais), function (no) {
    return no.tag === "Seletor" && no.props.rotulo === "rotação de trabalho";
  })[0];
  return sel && sel.props.disabled === true && outro && !outro.props.disabled;
})(), "esperava pessoal desactivado e trabalho activo");
check("render: ha um select de rotacao por linha", rotacaoSel.length === 3, "obtive " + rotacaoSel.length);

// A configuracao vive numa pagina EXCLUSIVA: com o editor aberto, `Pagina`
// devolve PaginaConfig por early return e nao constroi a arvore principal. Um
// `&&` no meio da arvore deixava os cartoes de estado por baixo do formulario
// — foi o que o dono viu tres vezes.
const srcPainel = fs.readFileSync(path.join(__dirname, "dist", "index.js"), "utf-8");
const montagensSecao = (srcPainel.match(/h\(SecaoClassificador/g) || []).length;
check("render: a seccao e montada uma so vez", montagensSecao === 1,
      "obtive " + montagensSecao + " montagens");

check("render: a pagina de configuracao e uma pagina propria, montada por early return",
      (function () {
        const iConf = srcPainel.indexOf("function PaginaConfig(");
        const iPag = srcPainel.indexOf("function Pagina(");
        const iGuard = srcPainel.indexOf("if (mostrarEditor) {");
        const iMonta = srcPainel.indexOf("return h(PaginaConfig, {");
        const iRetorno = srcPainel.indexOf('    return h(\n      "div",\n      { className: "vmm-pagina" },');
        if (iConf === -1 || iPag === -1 || iGuard === -1 || iMonta === -1 || iRetorno === -1) return false;
        // a guarda esta dentro de Pagina, monta PaginaConfig e vem antes da arvore principal
        return iConf < iPag && iGuard > iPag && iMonta > iGuard && iMonta < iRetorno;
      })(), "esperava PaginaConfig montada por early return dentro de Pagina");

check("render: a secao de configuracao vive dentro de PaginaConfig",
      (function () {
        const iConf = srcPainel.indexOf("function PaginaConfig(");
        const iSecao = srcPainel.indexOf("h(SecaoClassificador");
        const iPag = srcPainel.indexOf("function Pagina(");
        return iConf !== -1 && iSecao !== -1 && iPag !== -1 && iConf < iSecao && iSecao < iPag;
      })());

// Fechar grava. O botao de fechar/gravar passa por gravarEFechar, que chama
// salvarConfig: e salvarConfig que poe mostrarEditor a false e so depois de o
// servidor aceitar. Um fecho que descartasse em silencio perdia o trabalho.
check("fechar grava e volta: gravarEFechar chama salvarConfig",
      /function gravarEFechar\(\) \{\s*\n\s*salvarConfig\(\);/.test(srcPainel));
check("fechar grava e volta: a pagina de configuracao usa onGravar=gravarEFechar",
      /onGravar: gravarEFechar/.test(srcPainel));
check("descartar sem gravar continua a existir como accao explicita",
      /onDescartar: fecharEditor/.test(srcPainel)
      && /function fecharEditor\(\)/.test(srcPainel));


// O defeito que o dono repetiu tres vezes: configuracao no painel principal.
// A prova faz-se sobre o corpo de Pagina — nenhum select, nenhum bloco de
// configuracao. Se voltar a haver um, este check fica vermelho.
check("render: o painel principal nao tem um unico controlo de configuracao",
      (function () {
        const iPagina = srcPainel.indexOf("function Pagina()");
        const iFim = srcPainel.lastIndexOf("__HERMES_PLUGINS__.register");
        if (iPagina === -1 || iFim === -1 || iFim <= iPagina) return false;
        const corpo = srcPainel.slice(iPagina, iFim);
        return corpo.indexOf("h(Seletor") === -1
          && corpo.indexOf("h(Superficies") === -1
          && corpo.indexOf("<select") === -1;
      })());

check("render: o editor tem os cinco blocos com titulo proprio",
      (function () {
        const titulos = (srcPainel.match(/tituloBloco\("/g) || []).length;
        return titulos === 5
          && srcPainel.indexOf('tituloBloco("Complexidade"') !== -1
          && srcPainel.indexOf('tituloBloco("Tarefa persistente"') !== -1
          && srcPainel.indexOf('tituloBloco("Estratégia"') !== -1
          && srcPainel.indexOf('tituloBloco("Superfícies"') !== -1
          // O ensaio A/B e configuracao como as outras: os modelos dos bracos
          // estavam so no pre-registo e nao apareciam em nenhum formulario.
          && srcPainel.indexOf('tituloBloco("Ensaio A/B em sombra"') !== -1;
      })());

check("render: com JSON invalido a seccao avisa em vez de desaparecer",
      textoDaArvore(T.SecaoClassificador({
        configJSON: "{nao e json", versao: "", opcoes: {}, onMudar: function () {},
      })).indexOf("corrija o JSON") !== -1);

check("render: o cabecalho da seccao mostra a versao do painel",
      textoDaArvore(T.SecaoClassificador({
        configJSON: JSON.stringify(cfgSecao), versao: "9.9.9", opcoes: {}, onMudar: function () {},
      })).indexOf("9.9.9") !== -1);

// ---- a pagina de configuracao, renderizada como pagina inteira -------------
// Chama-se o componente a serio (nao o texto-fonte): um erro de referencia so
// aparecia no browser do dono. Foi assim que o `perfil.superficies` sem
// binding passou despercebido uma vez.
const estadoPagina = {
  versao_painel: "9.9.9",
  plugin: { versao: "9.9.9", autor: "Pedro Roldan", github: "github.com/ptv1p3r" },
  perfis: {
    default: { activo: true, catalogo: [{ modelo: "m1", provider: "p1", complexidade: "leve" }], superficies: {} },
    pessoal: { activo: true, catalogo: [], superficies: {} },
  },
  opcoes: {
    estrategia: opcoesReais,
    niveis: ["leve", "media", "alta"],
    superficies: ["cli", "cron", "api_server"],
  },
};

function renderPagina(extra) {
  const props = {
    estado: estadoPagina,
    configJSON: JSON.stringify(cfgSecao),
    erro: null,
    errosValidacao: [],
    validacaoOk: null,
    aValidar: false,
    aGravar: false,
    onValidar: function () {},
    onGravar: function () {},
    onDescartar: function () {},
    onSuperficie: function () {},
    onMudarJSON: function () {},
  };
  if (extra) Object.keys(extra).forEach(function (k) { props[k] = extra[k]; });
  return T.PaginaConfig(props);
}

const arvorePagina = renderPagina();
// O harness nao executa componentes-funcao: um `h((SecaoClassificador))` fica
// como no com tag. Texto plano achata arrays (o `.map` de erros produz um).
function textoPlano(no) {
  if (no === null || no === undefined || no === false) return "";
  if (typeof no === "string" || typeof no === "number") return String(no);
  if (Array.isArray(no)) return no.map(textoPlano).join(" ");
  if (!no.filhos) return "";
  return no.filhos.map(textoPlano).join(" ");
}
const textoPagina = textoPlano(arvorePagina);

check("pagina de configuracao: titulo proprio e nao o do painel",
      textoPagina.indexOf("Configuração") !== -1 && textoPagina.indexOf("Model Manager") === -1);

// A seccao que a pagina monta e exactamente a SecaoClassificador — e o que ela
// desenha prova-se chamando-a com as props que a pagina lhe passa. Procurar os
// titulos no texto da pagina nao provaria nada: o harness nao a expande.
const noSecao = recolher(arvorePagina, function (no) {
  return no.tag === "SecaoClassificador";
})[0];
check("pagina de configuracao: monta a seccao de configuracao",
      !!noSecao && noSecao.props.configJSON === JSON.stringify(cfgSecao));
const textoDaSecaoNaPagina = noSecao ? textoPlano(T.SecaoClassificador(noSecao.props)) : "";
check("pagina de configuracao: tem os tres blocos de configuracao",
      textoDaSecaoNaPagina.indexOf("Complexidade") !== -1
      && textoDaSecaoNaPagina.indexOf("Estratégia") !== -1
      && textoDaSecaoNaPagina.indexOf("Superfícies") !== -1);

check("pagina de configuracao: o JSON da configuracao vive aqui",
      textoPagina.indexOf("JSON da configuração") !== -1);
check("pagina de configuracao: fechar grava — o botao diz gravar e voltar",
      (textoPagina.indexOf("Gravar e voltar") !== -1)
      && (textoPagina.indexOf("Descartar") !== -1));
check("pagina de configuracao: nao mostra os cartoes de estado do painel",
      textoPagina.indexOf("Registo de utilizacao") === -1);

// Os botoes da pagina: os filhos vao no 3.o argumento do `h`. O stub do SDK
// nao traz `components`, logo `Button` cai no fallback "button" (minusculo).
const botoesPagina = recolher(arvorePagina, function (no) {
  return no.tag === "Button" || no.tag === "button";
});
const rotulosBotoes = botoesPagina.map(function (b) { return textoPlano(b).trim(); });
check("pagina de configuracao: tem validar, gravar e descartar",
      rotulosBotoes.indexOf("Validar") !== -1
      && rotulosBotoes.some(function (r) { return r.indexOf("Gravar e voltar") !== -1; })
      && rotulosBotoes.indexOf("Descartar") !== -1, rotulosBotoes.join(" | "));
check("pagina de configuracao: os erros de validacao aparecem no ecra",
      textoPlano(renderPagina({ errosValidacao: ["perfis.default: tarefa desconhecida 'cron'"] }))
        .indexOf("tarefa desconhecida") !== -1);
check("pagina de configuracao: o erro de topo aparece no ecra",
      textoPlano(renderPagina({ erro: "Configuração inválida" })).indexOf("Configuração inválida") !== -1);

// ---------------------------------------------------------------------------
// Uma rota do catalogo: a conversao forma <-> entrada e a pagina propria.
// E o sitio onde um erro se paga caro — o que sai daqui vai para o JSON que o
// middleware le no turno seguinte.
// ---------------------------------------------------------------------------

check("index.js expoe a conversao da rota para teste",
      typeof T.entradaParaForm === "function" && typeof T.formParaEntrada === "function"
      && typeof T.alternarOpcao === "function" && typeof T.emOrdem === "function"
      && typeof T.PaginaModelo === "function");

const rotaCompleta = {
  id: "gpt-sol", provider: "openrouter", modelo: "openai/gpt-6.1-sol",
  complexidade: ["alta"], acesso: "subscricao", esforco: "alto",
  tarefas: ["arquitetura"], desligado: true, contexto_max: 200000,
  latencia_p95_ms: 900, modalidades: ["texto", "imagem"], json_nativo: false,
  custo: 0.28, ordem: 3,
  nota: "rota de arquitectura",
};

const forma = T.entradaParaForm(rotaCompleta);
check("entradaParaForm le todos os campos",
      forma.id === "gpt-sol" && forma.provider === "openrouter"
      && forma.modelo === "openai/gpt-6.1-sol"
      && forma.complexidade.join(",") === "alta" && forma.acesso === "subscricao"
      && forma.esforco === "alto" && forma.tarefas.join(",") === "arquitetura"
      && forma.desligado === true && forma.contexto_max === "200000"
      && forma.latencia_p95_ms === "900"
      && forma.modalidades.join(",") === "texto,imagem"
      && forma.json_nativo === "nao" && forma.nota === "rota de arquitectura"
      && forma.custo === "0.28" && forma.ordem === "3",
      JSON.stringify(forma));

check("entradaParaForm sem campos nao inventa valores",
      (function () {
        const f = T.entradaParaForm({ id: "x", provider: "p", modelo: "m" });
        return f.complexidade.length === 0 && f.esforco === "" && f.json_nativo === ""
          && f.desligado === false && f.nota === "" && f.modalidades.length === 0
          && f.custo === "" && f.ordem === "";
      })(), JSON.stringify(T.entradaParaForm({ id: "x" })));

check("entradaParaForm aceita `niveis` legado",
      T.entradaParaForm({ niveis: "alta" }).complexidade.join(",") === "alta");

const OPCOES = {
  niveis: ["leve", "media", "alta"],
  acessos: ["api", "subscricao"],
  esforcos: ["baixo", "medio", "alto"],
  modalidades: ["texto", "imagem", "audio", "ferramentas"],
  tarefas: [{ valor: "arquitetura", rotulo: "arquitetura · prioridade 10" }],
  providers: ["deepseek", "openrouter"],
  superficies: ["cron", "api_server", "cli", "whatsapp"],
};

const volta = T.formParaEntrada(forma, OPCOES);
function canonico(obj) {
  // Ordem de chaves nao e semantica: conta o que ficou escrito, nao por que ordem.
  if (Array.isArray(obj)) return obj.map(canonico);
  if (!obj || typeof obj !== "object") return obj;
  const out = {};
  Object.keys(obj).sort().forEach(function (k) { out[k] = canonico(obj[k]); });
  return out;
}
// Custo e ordem entram na chave de escolha do plugin: custo, depois ordem,
// depois id. Duas coisas que se estragam em silencio: o custo 0 (gratis) nao
// pode virar "nao declarado", e o vazio nao pode virar 0 — sao opostos.
check("formParaEntrada: custo 0 e gratis, nao 'nao declarado'",
      T.formParaEntrada({ id: "x", provider: "p", modelo: "m", complexidade: ["alta"],
                          custo: "0" }, OPCOES).custo === 0);
check("formParaEntrada: ordem 0 e declarada",
      T.formParaEntrada({ id: "x", provider: "p", modelo: "m", complexidade: ["alta"],
                          ordem: "0" }, OPCOES).ordem === 0);
check("formParaEntrada: custo e ordem em branco nao sao escritos",
      !("custo" in T.formParaEntrada({ id: "x", provider: "p", modelo: "m",
                                       complexidade: ["alta"], custo: "", ordem: " " }, OPCOES))
      && !("ordem" in T.formParaEntrada({ id: "x", provider: "p", modelo: "m",
                                          complexidade: ["alta"], custo: "", ordem: " " }, OPCOES)));
// Superficies por modelo: o mapa e superficie -> booleano. A armadilha e a
// heranca: a ausencia da chave e o que herda, por isso um mapa vazio NAO pode
// ser escrito, e a copia nao pode partilhar a referencia do estado.
check("entradaParaForm: superficies vem copiadas, nao por referencia",
      (function () {
        const origem = { cron: false };
        const f = T.entradaParaForm({ id: "x", superficies: origem });
        f.superficies.cron = true;
        return origem.cron === false;
      })());
check("entradaParaForm: sem superficies devolve mapa vazio",
      Object.keys(T.entradaParaForm({ id: "x" }).superficies).length === 0);
check("formParaEntrada: superficies booleanas sobrevivem",
      (function () {
        const e = T.formParaEntrada({ id: "x", provider: "p", modelo: "m",
                                     complexidade: ["alta"],
                                     superficies: { cron: false, cli: true } }, OPCOES);
        return e.superficies.cron === false && e.superficies.cli === true;
      })());
check("formParaEntrada: mapa vazio nao escreve a chave (heranca)",
      !("superficies" in T.formParaEntrada({ id: "x", provider: "p", modelo: "m",
                                            complexidade: ["alta"], superficies: {} }, OPCOES)));
check("formParaEntrada: valores nao booleanos sao descartados",
      (function () {
        const e = T.formParaEntrada({ id: "x", provider: "p", modelo: "m",
                                     complexidade: ["alta"],
                                     superficies: { cron: "nao", cli: true } }, OPCOES);
        return !("cron" in e.superficies) && e.superficies.cli === true;
      })());
check("superficies: ida e volta preserva o mapa",
      JSON.stringify(canonico(T.formParaEntrada(
        T.entradaParaForm({ id: "x", provider: "p", modelo: "m",
                            complexidade: ["alta"], superficies: { "*": false, cron: true } }),
        OPCOES).superficies)) === JSON.stringify({ "*": false, cron: true }));

check("formParaEntrada: custo decimal sobrevive",
      T.formParaEntrada({ id: "x", provider: "p", modelo: "m", complexidade: ["alta"],
                          custo: "0.28" }, OPCOES).custo === 0.28);

check("formParaEntrada devolve a rota igual ao que leu",
      JSON.stringify(canonico(volta)) === JSON.stringify(canonico(rotaCompleta)),
      JSON.stringify(volta) + " != " + JSON.stringify(rotaCompleta));

// O que NAO se declara nao vai para o ficheiro: um `esforco: ""` seria um valor
// invalido disfarcado de omissao.
const vazia = T.formParaEntrada({ id: "x", provider: "p", modelo: "m",
                                  complexidade: ["leve"], acesso: "api", tarefas: [] }, OPCOES);
check("formParaEntrada nao escreve chaves vazias",
      !("esforco" in vazia) && !("modalidades" in vazia) && !("json_nativo" in vazia)
      && !("contexto_max" in vazia) && !("latencia_p95_ms" in vazia)
      && !("nota" in vazia) && !("desligado" in vazia),
      JSON.stringify(vazia));

const comNumeroMau = T.formParaEntrada(
  { id: "x", provider: "p", modelo: "m", complexidade: ["alta"], acesso: "api",
    tarefas: [], contexto_max: "abc" }, OPCOES);
check("formParaEntrada deixa o texto invalido para o gestor julgar",
      comNumeroMau.contexto_max === "abc", JSON.stringify(comNumeroMau));

check("formParaEntrada converte numeros",
      T.formParaEntrada({ id: "x", provider: "p", modelo: "m", complexidade: ["alta"],
                          acesso: "api", tarefas: [], contexto_max: "128000" }, OPCOES)
        .contexto_max === 128000);

// Ordem canonica: o JSON nao pode mudar de forma so porque se clicou por outra
// ordem. E um valor fora da lista actual (config antiga) nao se perde.
check("emOrdem ordena pela lista fechada",
      T.emOrdem(["alta", "leve"], ["leve", "media", "alta"]).join(",") === "leve,alta");
check("emOrdem nao perde valores fora da lista",
      T.emOrdem(["alta", "turbo"], ["leve", "media", "alta"]).join(",") === "alta,turbo");
check("formParaEntrada escreve a complexidade ordenada",
      T.formParaEntrada({ id: "x", provider: "p", modelo: "m",
                          complexidade: ["alta", "leve"], acesso: "api", tarefas: [] }, OPCOES)
        .complexidade.join(",") === "leve,alta");

check("alternarOpcao liga e desliga sem mutar a lista",
      (function () {
        const original = ["leve"];
        const ligada = T.alternarOpcao(original, "alta", true);
        const desligada = T.alternarOpcao(ligada, "leve", false);
        return original.join(",") === "leve" && ligada.join(",") === "leve,alta"
          && desligada.join(",") === "alta";
      })());
check("alternarOpcao nao duplica",
      T.alternarOpcao(["leve"], "leve", true).join(",") === "leve");

// --- a pagina: render sem browser -----------------------------------------
function andar(no, visita) {
  if (!no || typeof no !== "object") return;
  if (Array.isArray(no)) { no.forEach(function (f) { andar(f, visita); }); return; }
  visita(no);
  (no.filhos || []).forEach(function (f) { andar(f, visita); });
}

function comTag(arvore, tag) {
  const out = [];
  andar(arvore, function (n) { if (n.tag === tag) out.push(n); });
  return out;
}

function renderModelo(extra) {
  const props = {
    estado: { plugin: { versao: "3.20.0", autor: "Pedro Roldan", github: "https://x" } },
    perfil: "trabalho",
    id: "gpt-sol",
    novo: false,
    form: T.entradaParaForm(rotaCompleta),
    opcoes: OPCOES,
    rev: "abc123",
    erro: null,
    erros: [],
    validacaoOk: null,
    aGravar: false,
    aCarregar: false,
    onCampo: function () {},
    onCampoComplexidade: function () {},
    onCampoTarefas: function () {},
    onCampoModalidades: function () {},
    onValidar: function () {},
    onGravar: function () {},
    onDescartar: function () {},
  };
  if (extra) Object.keys(extra).forEach(function (k) { props[k] = extra[k]; });
  return T.PaginaModelo(props);
}

const arvoreModelo = renderModelo();
const rotulosModelo = comTag(arvoreModelo, "button").map(function (b) {
  return textoPlano(b.filhos).trim();
});
// O harness nao executa componentes-funcao: o rotulo de um `Campo` vive nas
// props, nao no texto achatado. Le-se pela prop, que e o que o browser mostra.
const rotulosCampo = comTag(arvoreModelo, "Campo").map(function (n) {
  return String((n.props && n.props.rotulo) || "").toLowerCase();
});
check("pagina do modelo: mostra o custo",
      rotulosCampo.indexOf("custo") !== -1, rotulosCampo.join(" | "));
check("pagina do modelo: mostra a ordem",
      rotulosCampo.indexOf("ordem") !== -1, rotulosCampo.join(" | "));
check("pagina do modelo: custo e ordem mostram o valor lido",
      comTag(arvoreModelo, "Campo").some(function (n) {
        if (!n.props || n.props.rotulo !== "custo") return false;
        return (n.filhos || []).some(function (f) {
          return f && f.props && String(f.props.valor) === "0.28";
        });
      }));
check("pagina do modelo: tem validar, gravar e descartar",
      rotulosModelo.indexOf("Validar") !== -1
      && rotulosModelo.some(function (r) { return r.indexOf("Gravar e voltar") !== -1; })
      && rotulosModelo.indexOf("Descartar") !== -1, rotulosModelo.join(" | "));

check("pagina do modelo: identifica a rota e o perfil",
      textoPlano(arvoreModelo).indexOf("Modelo · gpt-sol") !== -1
      && textoPlano(arvoreModelo).indexOf("trabalho") !== -1);

const titulosBlocos = comTag(arvoreModelo, "strong").map(function (s) { return textoPlano(s.filhos); });
check("pagina do modelo: tres blocos (identidade, roteamento, limites)",
      titulosBlocos.indexOf("Identidade") !== -1
      && titulosBlocos.indexOf("Roteamento") !== -1
      && titulosBlocos.indexOf("Limites declarados") !== -1, titulosBlocos.join(" | "));

// Todas as opcoes de escolha vem fechadas. O harness nao executa componentes-
// funcao, logo `BotoesOpcao` aparece como no com tag: aqui prova-se que a
// pagina lhe passa as listas fechadas, e em seguida renderiza-se o componente
// sozinho para provar que so mostra valores dessas listas.
const gruposOpcao = comTag(arvoreModelo, "BotoesOpcao");
const listasNaPagina = gruposOpcao.map(function (g) { return (g.props || {}).opcoes; });
check("pagina do modelo: tres grupos de escolha multipla",
      gruposOpcao.length === 3, String(gruposOpcao.length));
check("pagina do modelo: o grupo complexidade leva os niveis fechados",
      JSON.stringify(listasNaPagina[0]) === JSON.stringify(OPCOES.niveis),
      JSON.stringify(listasNaPagina[0]));
check("pagina do modelo: o grupo tarefas leva as tarefas declaradas no perfil",
      JSON.stringify(listasNaPagina[1]) === JSON.stringify(["arquitetura"]),
      JSON.stringify(listasNaPagina[1]));
check("pagina do modelo: o grupo modalidades leva as modalidades fechadas",
      JSON.stringify(listasNaPagina[2]) === JSON.stringify(OPCOES.modalidades),
      JSON.stringify(listasNaPagina[2]));

const grupoRender = T.BotoesOpcao({
  rotulo: "complexidade", opcoes: OPCOES.niveis, valor: ["alta"], onChange: function () {},
});
const botoesGrupo = comTag(grupoRender, "button");
check("grupo de opcao: um botao por opcao da lista",
      botoesGrupo.length === OPCOES.niveis.length, String(botoesGrupo.length));
check("grupo de opcao: so mostra valores da lista fechada",
      botoesGrupo.every(function (b) {
        return OPCOES.niveis.indexOf(textoPlano(b.filhos).trim()) !== -1;
      }), botoesGrupo.map(function (b) { return textoPlano(b.filhos).trim(); }).join(" | "));
check("grupo de opcao: o escolhido anuncia-se com aria-pressed",
      botoesGrupo.filter(function (b) {
        return String(b.props["aria-pressed"]) === "true";
      }).map(function (b) { return textoPlano(b.filhos).trim(); }).join(",") === "alta");

const seletores = comTag(arvoreModelo, "Seletor");
// Cinco selectores fixos (provider, acesso, esforço, estado, JSON) e, mais
// recente, um por superficie declarada + o das nao nomeadas. Conta-se pela
// lista, nao por um numero magico: acrescentar uma superficie nao parte isto.
const ESPERADOS_SELETORES = 5 + OPCOES.superficies.length + 1;
check("pagina do modelo: cinco listas fechadas + uma por superficie",
      seletores.length === ESPERADOS_SELETORES,
      String(seletores.length) + " != " + ESPERADOS_SELETORES);
check("pagina do modelo: cada superficie tem o seu selector tri-estado",
      (function () {
        const tri = seletores.filter(function (s) {
          const vals = ((s.props && s.props.opcoes) || []).map(function (o) { return o.valor; });
          return vals.length === 3 && vals[0] === "herdar";
        });
        return tri.length === OPCOES.superficies.length + 1;
      })(), String(seletores.length));
check("pagina do modelo: o provider e lista fechada vinda de opcoes.providers",
      (function () {
        const doProv = seletores.filter(function (s) {
          const vals = ((s.props && s.props.opcoes) || []).map(function (o) { return o.valor; });
          return vals.length && vals.every(function (v) { return OPCOES.providers.indexOf(v) !== -1; });
        });
        return doProv.length === 1;
      })(), seletores.map(function (s) { return JSON.stringify(((s.props || {}).opcoes || []).map(function (o) { return o.valor; })); }).join(" ; "));
check("pagina do modelo: os Seletor so recebem valores das listas",
      seletores.every(function (s) {
        const vals = ((s.props && s.props.opcoes) || []).map(function (o) { return o.valor; });
        return vals.every(function (v) {
          return v === "" || v === "sim" || v === "nao" || v === "herdar"
            || OPCOES.acessos.indexOf(v) !== -1 || OPCOES.esforcos.indexOf(v) !== -1
            || OPCOES.providers.indexOf(v) !== -1;
        });
      }), seletores.map(function (s) { return JSON.stringify((s.props || {}).opcoes); }).join(" ; "));

check("pagina do modelo: o botao de gravar esta desligado sem id",
      (function () {
        const semId = comTag(renderModelo({ form: T.entradaParaForm({ provider: "p", modelo: "m" }) }), "button")
          .filter(function (b) { return textoPlano(b.filhos).indexOf("Gravar e voltar") !== -1; })[0];
        return !!(semId && semId.props && semId.props.disabled === true);
      })());

check("pagina do modelo novo diz que e novo e nao grava id antigo",
      (function () {
        const arvore = renderModelo({ novo: true, id: null,
                                      form: T.entradaParaForm({ provider: "openrouter", modelo: "v/f:free" }) });
        const txt = textoPlano(arvore);
        return txt.indexOf("Novo modelo · trabalho") !== -1
          && txt.indexOf("Só entra no catálogo depois de gravar") !== -1;
      })());

check("pagina do modelo: os erros do gestor aparecem no ecra",
      textoPlano(renderModelo({ erros: ["perfis.trabalho.catalogo[1]: complexidade vazia",
                                        "acesso subscricao exige provider openai-codex"] }))
        .indexOf("acesso subscricao exige provider openai-codex") !== -1);
check("pagina do modelo: a validacao com sucesso aparece no ecra",
      textoPlano(renderModelo({ validacaoOk: "Validado — gpt-sol · 1 nível(is)" }))
        .indexOf("Validado — gpt-sol") !== -1);
check("pagina do modelo: o erro de topo aparece no ecra",
      textoPlano(renderModelo({ erro: "Configuração inválida: x" })).indexOf("Configuração inválida") !== -1);
check("pagina do modelo: enquanto carrega nao mostra campos a fingir",
      textoPlano(renderModelo({ aCarregar: true })).indexOf("a ler a rota do catálogo") !== -1);

// --- os icones: um por rota, um por perfil -------------------------------
const arvoreCartao = T.CartaoPerfil({
  nome: "trabalho",
  dados: {
    activo: true,
    estrategia: { modo: "rodizio", por: "sessao", origem: "padrao" },
    tarefas: { arquitetura: { termos: ["análise"], prioridade: 10 } },
    catalogo: [
      { id: "deepseek-flash", provider: "deepseek", modelo: "deepseek-flash",
        complexidade: ["leve"], acesso: "api", tarefas: [] },
      { id: "gpt-sol", provider: "openrouter", modelo: "openai/gpt-6.1-sol",
        complexidade: ["alta"], acesso: "api", esforco: "alto", tarefas: ["arquitetura"] },
    ],
  },
  aGravar: false,
  onToggle: function () {},
  onModelo: function () {},
  onNovoModelo: function () {},
});
const botoesCartao = comTag(arvoreCartao, "button");
const editar = botoesCartao.filter(function (b) {
  return textoPlano(b.filhos).trim() === "✎";
});
const novoBtn = botoesCartao.filter(function (b) {
  return textoPlano(b.filhos).indexOf("modelo") !== -1 && b.props.className.indexOf("vmm-icone-novo") !== -1;
});
check("cartao do perfil: um icone de edicao por modelo", editar.length === 2, String(editar.length));
check("cartao do perfil: o icone de edicao nomeia a rota",
      editar.every(function (b) { return String(b.props.title || "").indexOf("Editar as opções de ") === 0; }),
      editar.map(function (b) { return b.props.title; }).join(" | "));
check("cartao do perfil: um icone para criar modelo novo", novoBtn.length === 1, String(novoBtn.length));
check("cartao do perfil: o icone novo nomeia o perfil",
      novoBtn.length === 1 && String(novoBtn[0].props.title).indexOf("trabalho") !== -1,
      novoBtn.length ? String(novoBtn[0].props.title) : "sem botao");
check("cartao do perfil: o icone de edicao vive na celula do modelo",
      (function () {
        // O icone tem de estar na primeira coluna. Numa coluna a direita fica
        // fora do cartao (517px) porque a tabela mede 671px, e o overflow do
        // cartao cortava-o sem aviso — foi o defeito reportado.
        const celulas = comTag(arvoreCartao, "td").filter(function (td) {
          return String(td.props.className || "").indexOf("vmm-celula-modelo") !== -1;
        });
        if (!celulas.length) return false;
        return celulas.every(function (td) {
          return comTag({ filhos: td.filhos, tag: "raiz", props: {} }, "button").some(function (b) {
            return textoPlano(b.filhos).trim() === "✎";
          });
        });
      })(),
      "celulas com vmm-celula-modelo: " + comTag(arvoreCartao, "td").filter(function (td) {
        return String(td.props.className || "").indexOf("vmm-celula-modelo") !== -1;
      }).length);
check("cartao do perfil: nenhuma coluna de accoes a direita do cartao",
      !comTag(arvoreCartao, "th").some(function (th) {
        return String(th.props.className || "").indexOf("vmm-th-accoes") !== -1;
      }) && !comTag(arvoreCartao, "td").some(function (td) {
        return String(td.props.className || "").indexOf("vmm-td-accoes") !== -1;
      }));



// ---- pagina de um perfil --------------------------------------------------
// O que se prova aqui e o que um teste de strings nao prova: que "herdar"
// produz a palavra `herdar` (e nao um bloco copiado do global, que mataria a
// heranca em silencio), que um campo numerico vazio fica AUSENTE do bloco em
// vez de virar 0, e que um erro de formulario diz qual dos dezoito campos e.
(function () {
  const t = carregar();

  check("pagina de perfil: exportada para teste", typeof t.PaginaPerfil === "function");
  check("pagina de perfil: campos do classificador descritos",
        Array.isArray(t.CAMPOS_CLASSIFICADOR) && t.CAMPOS_CLASSIFICADOR.length === 18,
        "n=" + (t.CAMPOS_CLASSIFICADOR || []).length);
  check("pagina de perfil: dois objectos JSON editaveis",
        Array.isArray(t.CAMPOS_JSON_CLASSIFICADOR) && t.CAMPOS_JSON_CLASSIFICADOR.length === 2);
  check("pagina de perfil: nenhuma chave repetida",
        new Set(t.CAMPOS_CLASSIFICADOR.map(function (c) { return c.chave; })).size
          === t.CAMPOS_CLASSIFICADOR.length);
  check("pagina de perfil: todas as chaves tem tipo conhecido",
        t.CAMPOS_CLASSIFICADOR.concat(t.CAMPOS_JSON_CLASSIFICADOR).every(function (c) {
          return ["texto", "numero", "seletor", "json"].indexOf(c.tipo) >= 0 || c.tipo === undefined;
        }));

  // --- leitura: perfil que DECLARA um bloco proprio ---
  const declarado = {
    activo: true,
    latencia_max_ms: 45000,
    tarefas: { arquitetura: { termos: ["SA Analysis", "parecer"], prioridade: 10 } },
    classificador: {
      activo: true, provider: "openrouter", modelo: "~typesafe/jev-latest",
      via: "decisoes", preset: "sistema-um", estado: "medidas",
      confianca_min: 0, timeout_s: 12.0, max_tokens: 320,
      parametros_extra: { reasoning: { effort: "low" } },
      headers_extra: {},
    },
    estrategia: { modo: "rodizio", por: "sessao" },
  };
  const f1 = t.perfilParaForm(declarado);
  check("leitura: declara -> clsHerdar falso", f1.clsHerdar === false);
  check("leitura: declara -> estHerdar falso", f1.estHerdar === false);
  check("leitura: modelo preservado", f1.cls.modelo === "~typesafe/jev-latest");
  check("leitura: confianca 0 nao vira vazio", f1.cls.confianca_min === "0",
        "valor=" + JSON.stringify(f1.cls.confianca_min));
  check("leitura: parametros_extra vira JSON legivel",
        JSON.parse(f1.cls.parametros_extra).reasoning.effort === "low");
  check("leitura: headers_extra vazio fica string vazia", f1.cls.headers_extra === "");
  check("leitura: termos juntos por virgula", f1.tarefas[0].termos === "SA Analysis, parecer");
  check("leitura: latencia como texto", f1.latencia_max_ms === "45000");
  check("leitura: estrategia preenchida",
        f1.estModo === "rodizio" && f1.estPor === "sessao");

  // --- leitura: perfil que HERDA (o estado medido da config viva) ---
  const herdado = t.perfilParaForm({ activo: true, tarefas: {} });
  check("leitura: sem bloco -> clsHerdar verdadeiro", herdado.clsHerdar === true);
  check("leitura: sem bloco -> estHerdar verdadeiro", herdado.estHerdar === true);
  check("leitura: sem bloco -> nao inventa modelo", herdado.cls.modelo === "");
  check("leitura: sem bloco -> nenhuma tarefa", herdado.tarefas.length === 0);

  // --- escrita: herdar produz a palavra, nao uma copia ---
  const r1 = t.formParaPerfil(
    { activo: true, tarefas: [], clsHerdar: true, estHerdar: true }, "default", "abc123");
  check("escrita: herdar -> corpo.classificador === 'herdar'",
        r1.corpo.classificador === "herdar", JSON.stringify(r1.corpo.classificador));
  check("escrita: herdar -> corpo.estrategia === 'herdar'",
        r1.corpo.estrategia === "herdar");
  check("escrita: rev do servidor viaja no corpo", r1.corpo.rev === "abc123");

  // --- escrita: proprio, com tipos certos ---
  const r2 = t.formParaPerfil(f1, "default", "");
  check("escrita: sem erros de formulario", r2.erro.length === 0, r2.erro.join("; "));
  check("escrita: confianca_min volta a ser numero",
        r2.corpo.classificador.confianca_min === 0 && typeof r2.corpo.classificador.confianca_min === "number");
  check("escrita: timeout_s numero", r2.corpo.classificador.timeout_s === 12);
  check("escrita: max_tokens numero", r2.corpo.classificador.max_tokens === 320);
  check("escrita: parametros_extra volta a objecto",
        r2.corpo.classificador.parametros_extra.reasoning.effort === "low");
  check("escrita: headers_extra vazio NAO entra no bloco",
        !("headers_extra" in r2.corpo.classificador));
  check("escrita: termos partidos por virgula",
        JSON.stringify(r2.corpo.tarefas.arquitetura.termos) === JSON.stringify(["SA Analysis", "parecer"]));
  check("escrita: prioridade numero", r2.corpo.tarefas.arquitetura.prioridade === 10);
  check("escrita: latencia numero", r2.corpo.latencia_max_ms === 45000);
  check("escrita: estrategia propria objecto",
        r2.corpo.estrategia.modo === "rodizio" && r2.corpo.estrategia.por === "sessao");

  // --- escrita: campo numerico vazio fica AUSENTE (o padrao decide) ---
  const r3 = t.formParaPerfil({
    activo: false, tarefas: [], clsHerdar: false,
    cls: { provider: "deepseek", modelo: "deepseek-flash", timeout_s: "", max_tokens: "" },
    estHerdar: true,
  }, "pessoal", "");
  check("escrita: numero vazio nao vira 0 nem entra",
        !("timeout_s" in r3.corpo.classificador) && !("max_tokens" in r3.corpo.classificador),
        JSON.stringify(r3.corpo.classificador));
  check("escrita: activo false preservado", r3.corpo.activo === false);
  check("escrita: latencia vazia vira 0", r3.corpo.latencia_max_ms === 0);

  // --- escrita: erros nomeados ---
  const r4 = t.formParaPerfil({
    activo: true, tarefas: [], clsHerdar: false,
    cls: { timeout_s: "doze" }, estHerdar: true,
  }, "default", "");
  check("escrita: numero invalido e recusado", r4.erro.length === 1, JSON.stringify(r4.erro));
  check("escrita: o erro nomeia o campo", /timeout/.test(r4.erro[0]), r4.erro[0]);

  const r5 = t.formParaPerfil({
    activo: true, tarefas: [], clsHerdar: false,
    cls: { parametros_extra: "{nao e json" }, estHerdar: true,
  }, "default", "");
  check("escrita: JSON invalido e recusado com a causa",
        r5.erro.length === 1 && /parâmetros extra/.test(r5.erro[0]), JSON.stringify(r5.erro));

  const r6 = t.formParaPerfil({
    activo: true, clsHerdar: true, estHerdar: true,
    tarefas: [{ id: "a", termos: "x", prioridade: 1 }, { id: "a", termos: "y", prioridade: 2 }],
  }, "default", "");
  check("escrita: id de tarefa repetido e recusado", r6.erro.length === 1, JSON.stringify(r6.erro));

  const r7 = t.formParaPerfil({
    activo: true, clsHerdar: true, estHerdar: true,
    tarefas: [{ id: "", termos: "x", prioridade: 0 }],
  }, "default", "");
  check("escrita: tarefa sem id e recusada", r7.erro.length === 1, JSON.stringify(r7.erro));

  // --- tarefa persistente: perfil (leitura, escrita, limites) -------------
  // O bloco tem dois sitios: o global na pagina de configuracao, o delta no
  // perfil. Se a conversao deixar de o transportar, o POST leva "herdar" e a
  // caixa do painel mente — e nada no servidor se queixa.
  const comTp = t.perfilParaForm({
    activo: true, tarefas: {},
    tarefa_persistente: { activo: false, expira_turnos: 3 },
  });
  check("tp: declarado -> tpHerdar falso", comTp.tpHerdar === false);
  check("tp: activo false preservado", comTp.tpActivo === false);
  check("tp: turnos como texto", comTp.tpTurnos === "3");
  check("tp: chave ausente fica vazia, para herdar so essa",
        comTp.tpMin === "", JSON.stringify(comTp.tpMin));

  const semTp = t.perfilParaForm({ activo: true, tarefas: {} });
  check("tp: sem bloco -> herda o global", semTp.tpHerdar === true);

  const e1 = t.formParaPerfil(
    { activo: true, tarefas: [], clsHerdar: true, estHerdar: true, tpHerdar: true },
    "default", "");
  check("tp: herdar -> a palavra, nao uma copia do valor",
        e1.corpo.tarefa_persistente === "herdar", JSON.stringify(e1.corpo.tarefa_persistente));

  const e2 = t.formParaPerfil({
    activo: true, tarefas: [], clsHerdar: true, estHerdar: true,
    tpHerdar: false, tpActivo: true, tpTurnos: "8", tpMin: "",
  }, "default", "");
  check("tp: declarar -> inteiro e chave vazia fora",
        e2.corpo.tarefa_persistente.activo === true
          && e2.corpo.tarefa_persistente.expira_turnos === 8
          && !("expira_min" in e2.corpo.tarefa_persistente),
        JSON.stringify(e2.corpo.tarefa_persistente));
  check("tp: declarar sem erro", e2.erro.length === 0, e2.erro.join("; "));

  const e3 = t.formParaPerfil({
    activo: true, tarefas: [], clsHerdar: true, estHerdar: true,
    tpHerdar: false, tpActivo: true, tpTurnos: "0", tpMin: "2000",
  }, "default", "");
  check("tp: 0 turnos e 2000 min recusados", e3.erro.length === 2, JSON.stringify(e3.erro));
  check("tp: cada erro nomeia o seu prazo",
        /turnos/.test(e3.erro[0]) && /minutos/.test(e3.erro[1]), JSON.stringify(e3.erro));

  // --- tarefa persistente: o JSON da configuracao (painel de config) ------
  const cfgTp = {
    analise: { limiares: { alta: 4 } },
    perfis: { pessoal: { analise: { modo: "modelo",
                                    tarefa_persistente: { expira_min: 30 } } } },
  };
  check("tp config: sem bloco global devolve null", t.tarefaPersistenteDe(cfgTp, null) === null);
  check("tp config: le o delta do perfil",
        t.tarefaPersistenteDe(cfgTp, "pessoal").expira_min === 30);
  const cfgNovo = t.comTarefaPersistente(cfgTp, null, { activo: false }, false);
  check("tp config: escreve o global dentro de analise",
        cfgNovo.analise.tarefa_persistente.activo === false, JSON.stringify(cfgNovo.analise));
  check("tp config: nao toca no que la estava",
        cfgNovo.analise.limiares.alta === 4
          && cfgNovo.perfis.pessoal.analise.tarefa_persistente.expira_min === 30);
  check("tp config: o config original fica intacto",
        cfgTp.analise.tarefa_persistente === undefined);
  const cfgCampoVazio = t.comTarefaPersistente(
    { analise: { tarefa_persistente: { expira_min: 30 } } }, null, { expira_min: "" }, false);
  check("tp config: campo vazio sai (herda em vez de fixar)",
        (cfgCampoVazio.analise || {}).tarefa_persistente === undefined,
        JSON.stringify(cfgCampoVazio));
  const cfgPerfilHerdar = t.comTarefaPersistente(cfgTp, "pessoal", {}, true);
  check("tp config: herdar no perfil apaga so o bloco",
        cfgPerfilHerdar.perfis.pessoal.analise.tarefa_persistente === undefined
          && cfgPerfilHerdar.perfis.pessoal.analise.modo === "modelo");
  check("tp config: o padrao do painel e o do motor",
        t.TAREFA_PERSISTENTE_PADRAO.expira_turnos === 6
          && t.TAREFA_PERSISTENTE_PADRAO.expira_min === 90
          && t.TAREFA_PERSISTENTE_PADRAO.activo === true);

  // --- a pagina desenha-se de verdade (sem rebentar) ---
  const arvore = t.PaginaPerfil({
    estado: { versao: "3.21.0", painel: { versao: "3.21.0" } },
    perfil: "default",
    form: f1,
    opcoes: {
      providers: [{ valor: "deepseek", rotulo: "deepseek" }],
      vias: [{ valor: "auto", rotulo: "auto" }, { valor: "decisoes", rotulo: "decisoes" }],
      presets: [{ valor: "sistema-um", rotulo: "sistema-um" }],
      estados: [{ valor: "medidas", rotulo: "medidas" }],
      esforcos: [{ valor: "baixo", rotulo: "baixo" }],
      formatos_json: [{ valor: "auto", rotulo: "auto" }],
      estrategia: { modo: [{ valor: "rodizio", rotulo: "rotação" }], por: [{ valor: "sessao", rotulo: "por sessão" }] },
      tarefas: [], niveis: [], acessos: [], modalidades: [], superficies: [],
    },
    rev: "abc",
    efectivo: { classificador: { provider: "x", modelo: "y", origem: "proprio" }, estrategia: { modo: "rodizio", por: "sessao", origem: "proprio" }, n_modelos: 4 },
    global: { classificador: {}, estrategia: {} },
    onCampo: function () {}, onCampoCls: function () {}, onTarefa: function () {},
    onNovaTarefa: function () {}, onRemoverTarefa: function () {},
    onValidar: function () {}, onGravar: function () {}, onDescartar: function () {},
  });
  // O harness nao executa componentes-funcao: um `h(Seletor, ...)` fica como no
  // com tag "Seletor" e as suas props. Por isso se contam os nos, nao os
  // elementos finais do DOM.
  const titulos = comTag(arvore, "div").filter(function (x) {
    return String(x.props && x.props.className || "").indexOf("vmm-bloco-titulo") !== -1;
  });
  check("render: a pagina tem os cinco blocos", titulos.length === 5, "blocos=" + titulos.length);
  const textosBotoes = comTag(arvore, "button").map(function (b) { return textoPlano(b.filhos).trim(); });
  check("render: os tres botoes do editor estao la",
        textosBotoes.some(function (t) { return t === "Validar"; })
          && textosBotoes.some(function (t) { return /Gravar e voltar/.test(t); })
          && textosBotoes.some(function (t) { return t === "Descartar"; }),
        JSON.stringify(textosBotoes));
  const campos = comTag(arvore, "Seletor").length
    + comTag(arvore, "EntradaTexto").length
    + comTag(arvore, "textarea").length;
  check("render: os 18 campos do classificador desenhados quando e proprio",
        campos >= 20, "campos=" + campos
          + " Seletor=" + comTag(arvore, "Seletor").length
          + " EntradaTexto=" + comTag(arvore, "EntradaTexto").length
          + " textarea=" + comTag(arvore, "textarea").length);

  // Herdar esconde os dezoito: o formulario nao mostra campos que nao vao ser
  // gravados — mostra o que se herda.
  const arvoreHerdada = t.PaginaPerfil({
    estado: {}, perfil: "pessoal", form: herdado,
    opcoes: {}, rev: "abc",
    efectivo: { classificador: { origem: "herdado" }, estrategia: { origem: "herdado" } },
    global: { classificador: { provider: "openrouter", modelo: "~typesafe/jev-latest" },
              estrategia: { modo: "rodizio", por: "sessao" } },
    onCampo: function () {}, onCampoCls: function () {}, onTarefa: function () {},
    onNovaTarefa: function () {}, onRemoverTarefa: function () {},
    onValidar: function () {}, onGravar: function () {}, onDescartar: function () {},
  });
  check("render: a herdar nao mostra os campos do classificador",
        comTag(arvoreHerdada, "Seletor").length + comTag(arvoreHerdada, "EntradaTexto").length <= 5,
        "campos=" + (comTag(arvoreHerdada, "Seletor").length + comTag(arvoreHerdada, "EntradaTexto").length));
  // (o limite subiu de 4 para 5 com o seletor "herdar/declarar" da tarefa
  // persistente: o bloco dela mostra sempre a definicao, nunca os tres campos)
  check("render: a herdar diz de quem herda",
        /~typesafe\/jev-latest/.test(textoPlano(arvoreHerdada)));
  // --- o icone do cartao ABRE a pagina: a ligacao, nao o desenho -----------
  // O render prova que o ⚙ esta la e cabe; isto prova que carregar nele chama
  // o handler do perfil certo. Sem esta prova, um icone bonito e inerte.
  const cartao = t.CartaoPerfil({
    estado: { versao: "3.21.0" },
    perfil: "default",
    nome: "trabalho",
    dados: {
      activo: true, catalogo: [], tarefas: {},
      estrategia: { modo: "rodizio", por: "sessao", origem: "herdado" },
      analise: { modo: "modelo" },
    },
    aGravar: false,
    onToggle: function () {}, onPerfil: function () {}, onModelo: function () {},
    onNovoModelo: function () {}, onNovo: function () {},
  });
  const chamadas = [];
  (function procurar(no) {
    if (!no || typeof no !== "object") return;
    if (Array.isArray(no)) return no.forEach(procurar);
    if (no.props && typeof no.props.onClick === "function") {
      const rotulo = textoPlano(no.filhos).trim();
      if (rotulo === "⚙") {
        chamadas.push({ rotulo: rotulo, classe: no.props.className,
                        titulo: no.props.title, clicar: no.props.onClick });
      }
    }
    (no.filhos || []).forEach(procurar);
  })(cartao);
  check("cartao: o ⚙ existe e esta ligado a um onClick", chamadas.length === 1,
        "encontrados=" + chamadas.length);
  if (chamadas.length === 1) {
    check("cartao: o ⚙ tem a classe do icone de perfil",
          /vmm-icone-perfil/.test(chamadas[0].classe || ""), chamadas[0].classe);
    check("cartao: o titulo do ⚙ diz o que faz",
          /configurações do perfil trabalho/.test(chamadas[0].titulo || ""),
          chamadas[0].titulo);

    // E o handler: o cartao recebe `onPerfil`, e o painel passa-lhe abrirPerfil.
    let recebido = null;
    const cartaoComEspiao = t.CartaoPerfil({
      estado: {}, perfil: "trabalho", nome: "trabalho",
      dados: { activo: true, catalogo: [], tarefas: {}, estrategia: {}, analise: {} },
      aGravar: false,
      onToggle: function () {}, onPerfil: function (n) { recebido = n; },
      onModelo: function () {}, onNovoModelo: function () {}, onNovo: function () {},
    });
    (function procurar2(no) {
      if (!no || typeof no !== "object") return;
      if (Array.isArray(no)) return no.forEach(procurar2);
      if (no.props && typeof no.props.onClick === "function"
          && textoPlano(no.filhos).trim() === "⚙") {
        no.props.onClick();
      }
      (no.filhos || []).forEach(procurar2);
    })(cartaoComEspiao);
    check("cartao: carregar no ⚙ entrega o nome do perfil ao painel", recebido === "trabalho",
          JSON.stringify(recebido));
  }

  // O painel liga `onPerfil` a `abrirPerfil`: le-se no proprio bundle, porque
  // abrirPerfil e fechada dentro do modulo. Um `onPerfil` que apontasse a outra
  // coisa (ou nada) passaria nos testes acima e falharia aqui.
  const fonte = require("fs").readFileSync(
    "/opt/data/plugins/vipernet-model-manager/dashboard/dist/index.js", "utf-8");
  check("painel: onPerfil chama abrirPerfil",
        /onPerfil:\s*function \(perfil\) \{ abrirPerfil\(perfil\); \}/.test(fonte));
  check("painel: a pagina de perfil tem early return antes do painel",
        /if \(perfilAberto\) \{\s*\n\s*return h\(PaginaPerfil,/.test(fonte));
  check("painel: abrirPerfil le /perfil do servidor (nao do resumo do cartao)",
        /pedir\("\/perfil\?nome=" \+ encodeURIComponent\(perfil\)\)/.test(fonte));
})();

// ---------------------------------------------------------- aviso de config
(function () {
  // O painel lia o config normalizado e mostrava-o como se fosse o do disco.
  // Um ficheiro com uma chave desconhecida (editado a mao, ou escrito por uma
  // versao futura) nao produzia uma unica palavra de aviso: gravava-se por
  // cima sem saber o que se perdia.
  const vazio = T.AvisoConfig({ estado: {} });
  check("aviso de config: sem problemas nao desenha nada", vazio === null);
  check("aviso de config: estado sem a chave tambem nao desenha",
        T.AvisoConfig({ estado: { erros_config: [] } }) === null);

  const comErro = T.AvisoConfig({
    estado: { erros_config: ["analise.coisa desconhecida (quis dizer ...)"] },
  });
  const texto = textoPlano(comErro);
  check("aviso de config: diz que o painel mostra a versao validada",
        texto.indexOf("versão validada") !== -1);
  check("aviso de config: avisa que gravar vai ser recusado",
        texto.indexOf("recusado") !== -1);
  check("aviso de config: lista o erro por extenso",
        texto.indexOf("analise.coisa desconhecida") !== -1);
  check("aviso de config: e um alerta acessivel", comErro.props.role === "alert");

  // A lista completa pode ser longa: mostra no maximo seis, para o cabecalho
  // nao empurrar o painel todo para fora do ecra.
  const muitos = T.AvisoConfig({
    estado: { erros_config: ["a", "b", "c", "d", "e", "f", "g", "h"] },
  });
  const itens = muitos.filhos[1];
  check("aviso de config: mostra no maximo seis erros", itens.length === 6,
        String(itens.length));

  // E tem de estar montado nas DUAS paginas: o aviso existe para ser visto.
  check("aviso de config: montado no painel principal",
        /className: "vmm-pagina" \},\s*\n\s*h\(AvisoConfig/.test(srcPainel));
  check("aviso de config: montado na pagina de configuracao",
        /"vmm-pagina vmm-pagina-config" \},\s*\n\s*h\(AvisoConfig/.test(srcPainel));
})();

console.log("");
if (falhas) {
  console.log(falhas + " de " + total + " verificacoes FALHARAM");
  process.exit(1);
}
console.log(total + "/" + total + " verificacoes OK");
