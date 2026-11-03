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

// A seccao vive SO dentro do editor de configuracao. Um cartao no topo do
// painel misturava a configuracao com o estado e lia-se como "tudo junto":
// o sitio de configurar e o editor, e so la.
const srcPainel = fs.readFileSync(path.join(__dirname, "dist", "index.js"), "utf-8");
const montagensSecao = (srcPainel.match(/h\(SecaoClassificador/g) || []).length;
check("render: a seccao e montada uma so vez", montagensSecao === 1,
      "obtive " + montagensSecao + " montagens");
check("render: a seccao esta dentro do editor, nao no painel principal",
      (function () {
        const posEditor = srcPainel.indexOf("mostrarEditor && h(");
        const posSecao = srcPainel.indexOf("h(SecaoClassificador");
        return posEditor !== -1 && posSecao !== -1 && posSecao > posEditor;
      })());

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

check("render: o editor tem os tres blocos com titulo proprio",
      (function () {
        const titulos = (srcPainel.match(/tituloBloco\("/g) || []).length;
        return titulos === 3
          && srcPainel.indexOf('tituloBloco("Complexidade"') !== -1
          && srcPainel.indexOf('tituloBloco("Estratégia"') !== -1
          && srcPainel.indexOf('tituloBloco("Superfícies"') !== -1;
      })());

check("render: com JSON invalido a seccao avisa em vez de desaparecer",
      textoDaArvore(T.SecaoClassificador({
        configJSON: "{nao e json", versao: "", opcoes: {}, onMudar: function () {},
      })).indexOf("corrija o JSON") !== -1);

check("render: o cabecalho da seccao mostra a versao do painel",
      textoDaArvore(T.SecaoClassificador({
        configJSON: JSON.stringify(cfgSecao), versao: "9.9.9", opcoes: {}, onMudar: function () {},
      })).indexOf("9.9.9") !== -1);

console.log("");
if (falhas) {
  console.log(falhas + " de " + total + " verificacoes FALHARAM");
  process.exit(1);
}
console.log(total + "/" + total + " verificacoes OK");
