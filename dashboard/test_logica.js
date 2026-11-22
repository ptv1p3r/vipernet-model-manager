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
  // Estes testes verificam o texto em portugues, que e a lingua de origem do
  // painel (o dicionario tem PT como chave). O padrao da instalacao e ingles,
  // por isso a lingua e fixada aqui; o ingles testa-se a parte, no bloco de
  // i18n abaixo, com o dicionario a ser virado de proposito.
  ferramentas.idioma.setIdioma("pt");
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
const opcoesSelectProvider = (function () {
  if (!selectsSombra.length) return [];
  // `h` guarda os filhos em `filhos`; nao se junta `props.children` para nao
  // contar a mesma lista duas vezes.
  const crus = selectsSombra[0].filhos || [];
  const filhos = [];
  crus.forEach(function (f) {
    if (Array.isArray(f)) f.forEach(function (g) { filhos.push(g); });
    else filhos.push(f);
  });
  const vistos = [];
  recolher({ tag: "x", filhos: filhos }, function (n) {
    if (n.tag !== "option") return false;
    const v = n.props.value;
    if (vistos.indexOf(v) === -1) vistos.push(v);
    return false;
  });
  return vistos.sort();
})();
check("render: a lista de providers e fechada e tirada do catalogo",
      // "" = (auto); `deepseek`/`openrouter` vem do selo; `p` vem do catalogo
      // do perfil — prova que a lista nao e escrita a mao no painel.
      JSON.stringify(opcoesSelectProvider)
      === JSON.stringify(["", "deepseek", "openrouter", "p"]),
      JSON.stringify(opcoesSelectProvider));
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

check("render: o editor tem os seis blocos com titulo proprio",
      (function () {
        // O titulo pode chegar ao `tituloBloco` em portugues (lingua de origem)
        // ou ja passado por `T(...)`, que e a forma que o painel usa para
        // traduzir. Aceitam-se as duas: o que se prova e que o bloco existe e
        // que o titulo e o certo, nao a forma sintactica da chamada.
        const comTitulo = function (texto) {
          return srcPainel.indexOf('tituloBloco("' + texto + '"') !== -1
            || srcPainel.indexOf('tituloBloco(T("' + texto + '")') !== -1;
        };
        const titulos = (srcPainel.match(/tituloBloco\("/g) || []).length
          + (srcPainel.match(/tituloBloco\(T\("/g) || []).length;
        return titulos === 6
          && comTitulo("Complexidade")
          && comTitulo("Tarefa persistente")
          && comTitulo("Estratégia")
          // O circuit breaker e configuracao global e por perfil desde que a
          // regra "X falhas em Y minutos -> cooldown +Z%" entrou: o bloco tem de
          // estar no formulario, nao so no JSON.
          && comTitulo("Circuit Breaker")
          && comTitulo("Superfícies")
          // O ensaio A/B e configuracao como as outras: os modelos dos bracos
          // estavam so no pre-registo e nao apareciam em nenhum formulario.
          && comTitulo("Ensaio A/B em sombra");
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
  // Sugestoes do campo do provider: os da configuracao e os que o nucleo serve.
  providers_conhecidos: ["deepseek", "openrouter", "inception"],
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
// Quatro selectores fixos (acesso, esforço, estado, JSON), um por superficie
// declarada e o das nao nomeadas. O do `provider` saiu daqui a 2026-11-08: e
// texto livre com sugestoes, porque um provider novo nao pode ser bloqueado por
// uma lista fechada. Conta-se pela lista, nao por um numero magico.
const ESPERADOS_SELETORES = 4 + OPCOES.superficies.length + 1;
check("pagina do modelo: quatro listas fechadas + uma por superficie",
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

// --- Sonda de ligação (3.47.12) -----------------------------------------
// O botão da página de um modelo chama o provider com o par escrito e diz se
// responde. Prova-se, sem browser e sem rede: que existe com o icon, que está
// desligado sem o par (não se gasta um pedido a descobrir que falta um campo) e
// que o resultado é lido nas três formas que o backend devolve.
const botaoSonda = comTag(arvoreModelo, "button").filter(function (b) {
  return textoPlano(b.filhos).trim().indexOf("Testar ligação") !== -1;
});
check("pagina do modelo: o botao de sonda existe com icon",
      botaoSonda.length === 1
      && textoPlano(botaoSonda[0].filhos).trim().charAt(0) === "⚡",
      rotulosModelo.join(" | "));
check("pagina do modelo: a sonda esta ligada com o par preenchido",
      botaoSonda.length === 1 && botaoSonda[0].props.disabled === false,
      String(botaoSonda[0] && botaoSonda[0].props.disabled));

const semPar = renderModelo({
  form: T.entradaParaForm({ id: "x", provider: "", modelo: "", complexidade: ["leve"] }),
});
const sondaSemPar = comTag(semPar, "button").filter(function (b) {
  return textoPlano(b.filhos).trim().indexOf("Testar ligação") !== -1;
});
check("pagina do modelo: sem provider/modelo a sonda esta desligada",
      sondaSemPar.length === 1 && sondaSemPar[0].props.disabled === true,
      String(sondaSemPar[0] && sondaSemPar[0].props.disabled));

const emCurso = renderModelo({ aSondar: true });
const botaoEmCurso = comTag(emCurso, "button").filter(function (b) {
  return textoPlano(b.filhos).trim().indexOf("a testar a ligação") === 0;
});
check("pagina do modelo: a meio da sonda o botao diz que esta a testar",
      botaoEmCurso.length === 1,
      comTag(emCurso, "button").map(function (b) { return textoPlano(b.filhos).trim(); }).join(" | "));
check("pagina do modelo: a meio da sonda o botao fica desligado",
      botaoEmCurso.length === 1 && botaoEmCurso[0].props.disabled === true);

check("sonda: sucesso diz ms, http e o par",
      T.textoSonda({ ok: true, ms: 412, http: 200, provider: "deepseek", modelo: "deepseek-flash" })
      === "✓ respondeu em 412 ms · HTTP 200 · deepseek/deepseek-flash",
      T.textoSonda({ ok: true, ms: 412, http: 200, provider: "deepseek", modelo: "deepseek-flash" }));
check("sonda: falha com http mostra o motivo",
      T.textoSonda({ ok: false, http: 401, ms: 42, motivo: "Incorrect API key" })
      === "✗ falhou · HTTP 401 · Incorrect API key",
      T.textoSonda({ ok: false, http: 401, ms: 42, motivo: "Incorrect API key" }));
check("sonda: falha antes do pedido nao inventa um 0",
      T.textoSonda({ ok: false, http: 0, ms: 0, motivo: "URLError: sem rede" })
      === "✗ falhou · URLError: sem rede");
check("sonda: motivo vazio leva marcador em vez de linha muda",
      T.textoSonda({ ok: false, motivo: "   " }) === "✗ falhou · ?",
      T.textoSonda({ ok: false, motivo: "   " }));
check("sonda: o texto vai para o ecra e nao para o ficheiro",
      T.textoSonda({ ok: true, ms: 5, http: 200, provider: "p", modelo: "m" })
      === "✓ respondeu em 5 ms · HTTP 200 · p/m");
// O provider deixou de ser lista fechada a 2026-11-08, por um defeito medido: o
// dono tentou criar uma rota para um provider que o Hermes ja serve e o painel
// so deixava escolher entre os que a configuracao ja usava. Passou a texto livre
// com `datalist` de sugestoes; quem recusa um valor e o backend, nao o campo.
const campoProvider = comTag(arvoreModelo, "Campo").filter(function (c) {
  return String((c.props && c.props.rotulo) || "").toLowerCase() === "provider";
})[0];
const filhosProvider = (campoProvider && campoProvider.filhos) || [];
const noProvider = filhosProvider.filter(function (f) {
  return f && f.tag === "EntradaComSugestoes";
})[0];
check("pagina do modelo: o provider e texto livre, nao um select",
      !!campoProvider && !!noProvider
        && !filhosProvider.some(function (f) { return f && f.tag === "Seletor"; }),
      filhosProvider.map(function (f) { return f && f.tag; }).join(","));
check("pagina do modelo: as sugestoes do provider juntam configuracao e nucleo, sem repetir",
      !!noProvider && (noProvider.props.sugestoes || []).join(",")
        === "deepseek,openrouter,inception",
      JSON.stringify((noProvider && noProvider.props && noProvider.props.sugestoes) || []));
check("pagina do modelo: o provider em vigor aparece escrito no campo",
      !!noProvider && noProvider.props.valor === String(rotaCompleta.provider),
      (noProvider && noProvider.props.valor) + " != " + rotaCompleta.provider);

// Prova directa do componente: um nome que nao esta em nenhuma lista chega ao
// `onChange` tal e qual — e o que o backend grava na entrada do catalogo.
const escritosProvider = [];
const arvoreProvider = T.EntradaComSugestoes({
  valor: "", rotulo: "provider", idLista: "vmm-provider-sugestoes",
  placeholder: "ex.: inception", sugestoes: ["deepseek", "openrouter"],
  onChange: function (v) { escritosProvider.push(v); },
});
const inputProvider = comTag(arvoreProvider, "input")[0];
const dlProvider = comTag(arvoreProvider, "datalist")[0];
check("provider livre: e um input com datalist, sem select ao lado",
      !!inputProvider && !!dlProvider
        && !comTag(arvoreProvider, "select").length
        && inputProvider.props.list === "vmm-provider-sugestoes",
      inputProvider ? JSON.stringify(inputProvider.props) : "sem input");
check("provider livre: o datalist leva as sugestoes como opcoes",
      !!dlProvider && comTag(dlProvider, "option")
        .map(function (o) { return o.props.value; }).join(",") === "deepseek,openrouter");
if (inputProvider) inputProvider.props.onChange({ target: { value: "inception" } });
check("provider livre: um nome fora das sugestoes chega ao onChange",
      escritosProvider.join(",") === "inception", JSON.stringify(escritosProvider));
check("provider livre: o nome escrito sobrevive ao formParaEntrada",
      T.formParaEntrada({ id: "x", provider: "inception", modelo: "m",
                          complexidade: ["alta"], acesso: "api" }, OPCOES).provider === "inception");
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
  check("render: a pagina tem os seis blocos", titulos.length === 6, "blocos=" + titulos.length);
  // (eram cinco ate a 3.47.21: o sexto e o interruptor do ensaio em sombra)
  check("render: o sexto bloco e o do ensaio em sombra",
        titulos.length === 6 && /Ensaio em sombra/.test(textoPlano(titulos[5])),
        titulos.length ? textoPlano(titulos[titulos.length - 1]) : "sem blocos");
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

// ── i18n: ingles (padrao) e portugues de Portugal ──────────────────────────
// O que se prova aqui e o que um teste de ecra nao prova de forma estavel:
// que o dicionario nao tem chaves vazias, que um texto desconhecido cai no PT
// em vez de desaparecer, e que o wrapper `h` traduz texto e titulos mas NAO
// toca em `className` nem em `value` — uma traducao que mexesse numa classe
// partia o CSS e uma que mexesse num `value` partia um select.
(function () {
  const I = T.idioma;

  check("i18n: as duas linguas sao en e pt", I.IDIOMAS.join(",") === "en,pt");
  check("i18n: normaliza formas regionais",
        I.normalizarIdioma("pt-PT") === "pt"
        && I.normalizarIdioma("PT_br") === "pt"
        && I.normalizarIdioma("en-US") === "en");
  check("i18n: codigo desconhecido cai no padrao (ingles)",
        I.normalizarIdioma("fr") === "en"
        && I.normalizarIdioma("") === "en"
        && I.normalizarIdioma(null) === "en");

  const chaves = Object.keys(I.PT_EN);
  // A contagem e um alarme de deriva: subir a mao quando se acrescentam chaves e
  // o que obriga a olhar para o dicionario. +9 na 3.47.12 (sonda de ligacao),
  // +9 na 3.47.16 (remover uma rota do catalogo), +34 na 3.47.17 (painel do
  // ensaio em sombra: etiquetas de estado, barras e previsao), +18 na 3.47.21
  // (interruptor do ensaio por perfil e o resumo por perfil ao lado do global),
  // +1 na 3.48.1 (estado `em pausa (quota)` — pausa de quota nao e avaria).
  check("i18n: o dicionario tem as 576 chaves medidas",
        chaves.length === 576, "tem " + chaves.length);
  check("i18n: nenhuma traducao vazia",
        chaves.every(function (k) { return String(I.PT_EN[k]).trim() !== ""; }));
  // Nomes proprios e termos tecnicos ficam iguais nas duas linguas — o que nao
  // pode haver e prosa portuguesa a passar por traducao.
  const INTENCIONAIS = ["Model Manager", "Circuit Breaker"];
  check("i18n: nenhuma traducao igual a chave com maiuscula inicial",
        chaves.filter(function (k) {
          const v = I.PT_EN[k];
          return v === k && /^[A-ZÀ-Ú]/.test(k) && k.length > 6
            && INTENCIONAIS.indexOf(k) === -1;
        }).length === 0);
  check("i18n: as chaves de interface principais estao traduzidas",
        I.PT_EN["Gravar"] === "Save"
        && I.PT_EN["Cancelar"] === "Cancel"
        && I.PT_EN["Classificador"] === "Classifier"
        && I.PT_EN["Estratégia"] === "Strategy"
        && I.PT_EN["Circuit Breaker"] === "Circuit Breaker"
        && I.PT_EN["Idioma"] === "Language");

  // Em portugues a funcao devolve o texto intacto: PT e a lingua de origem.
  I.setIdioma("pt");
  check("i18n: em pt o texto nao e tocado",
        I.t("Gravar") === "Gravar" && I.t("Cancelar") === "Cancelar");
  check("i18n: em en o texto e traduzido",
        I.setIdioma("en") === true && I.t("Gravar") === "Save");
  check("i18n: texto fora do dicionario cai no original",
        I.t("texto que nao existe no dicionario") === "texto que nao existe no dicionario");

  // Tempo decorrido: a ordem das palavras muda com a lingua.
  check("i18n: tempo decorrido com a ordem de cada lingua",
        I.tQuanto(5, "min") === " 5 min ago"
        && I.tHa(12) === "12 min ago"
        && I.tInstantes() === "moments ago");
  I.setIdioma("pt");
  check("i18n: tempo decorrido em pt volta a forma original",
        I.tQuanto(5, "min") === " há 5 min"
        && I.tHa(12) === "há 12 min"
        && I.tInstantes() === "instantes");
  check("i18n: datas em pt usam pt-PT e em en usam en-GB",
        I.localeHora() === "pt-PT");
  I.setIdioma("en");
  check("i18n: datas em en mantem dia/mes (en-GB, nao en-US)",
        I.localeHora() === "en-GB");

  // O wrapper: traduz o que e texto, nao toca no que e logica.
  const arvore = I.h("div", { className: "vmm-opcao-on", title: "Gravar" }, "Gravar");
  check("i18n: o wrapper traduz o filho de texto", arvore.filhos[0] === "Save");
  check("i18n: o wrapper traduz o titulo", arvore.props.title === "Save");
  check("i18n: o wrapper NAO toca no className",
        arvore.props.className === "vmm-opcao-on");
  const select = I.h("select", { value: "en", className: "vmm-select" },
    I.h("option", { value: "en" }, "English"));
  check("i18n: o wrapper NAO toca no value de um select",
        select.props.value === "en");

  // Uma string que e chave do dicionario mas nao e texto visivel (por exemplo
  // um identificador igual a uma palavra traduzida) so muda quando e filho de
  // texto — e ai e mesmo texto.
  check("i18n: um filho que nao e string passa intacto",
        I.h("div", null, 42, null).filhos[0] === 42);

  // Rotulos que vem do BACKEND (valores de listas fechadas: niveis, esforcos,
  // acessos, origem e efeito do registo). O `h()` traduz por lookup exacto,
  // logo o valor tem de ser chave; e quando o painel o compoe com prefixo
  // ("nivel " + valor), a composicao tem de traduzir a parte tambem. Nasceu de
  // um defeito real: `"nivel " + n` dava «level leve» no painel ingles.
  check("i18n: o wrapper traduz o rotulo que vem do backend",
        I.h("option", null, "desligado").filhos[0] === "off"
        && I.h("span", null, "sombra").filhos[0] === "shadow"
        && I.h("span", null, "media").filhos[0] === "medium"
        && I.h("span", null, "subscricao").filhos[0] === "subscription");
  const alvoEn = T.opcoesAlvo([], ["leve", "media"]);
  const alvoLeve = alvoEn.filter(function (o) { return o.valor === "nivel:leve"; })[0];
  check("i18n: o rotulo composto traduz as duas partes",
        !!alvoLeve && !/\bleve\b/.test(alvoLeve.rotulo)
        && /light/.test(alvoLeve.rotulo), alvoLeve && alvoLeve.rotulo);
  check("i18n: o valor do select continua o identificador da config",
        !!alvoLeve && alvoLeve.valor === "nivel:leve");
  const regraEn = T.rotuloRegra({ nivel: "alta" }, []);
  check("i18n: o rotulo da regra nao deixa o nivel em portugues",
        regraEn.indexOf("alta") === -1, regraEn);

  // Tempo decorrido: a ordem das palavras muda entre linguas. Uma chave com
  // {n}/{u} resolve as duas sem ramos por idioma e sem traducao vazia.
  check("i18n: tempo decorrido em ingles poe o 'ago' no fim",
        I.tHa(5) === "5 min ago" && I.tQuanto(5, "min") === " 5 min ago",
        I.tHa(5) + " | " + I.tQuanto(5, "min"));
  check("i18n: instantes em ingles", I.tInstantes() === "moments ago", I.tInstantes());
  I.setIdioma("pt");
  check("i18n: tempo decorrido em portugues mantem a ordem",
        I.tHa(5) === "há 5 min" && I.tQuanto(5, "min") === " há 5 min"
        && I.tInstantes() === "instantes",
        I.tHa(5) + " | " + I.tQuanto(5, "min"));
  I.setIdioma("en");

  // O seletor: duas opcoes, valor corrente, rotulo traduzido.
  const sel = I.SeletorIdioma({ idioma: "pt", onMudar: function () {} });
  const campo = sel.filhos[1];
  // O stub de `h` recebe o array de opcoes como um so filho: `filhos[0]` e a
  // lista, nao a primeira opcao.
  const opcoes = campo.filhos[0];
  check("i18n: o seletor mostra as duas linguas",
        Array.isArray(opcoes) && opcoes.length === 2
        && opcoes[0].props.value === "en"
        && opcoes[1].props.value === "pt");
  check("i18n: o seletor mostra a lingua em vigor",
        campo.props.value === "pt");
  check("i18n: o seletor tem rotulo traduzido",
        sel.filhos[0].filhos[0] === "Language"
        && sel.props.title === "Change the panel language");
  check("i18n: o seletor desliga enquanto grava",
        I.SeletorIdioma({ idioma: "en", ocupado: true }).filhos[1].props.disabled === true);

  // Volta ao portugues: o resto do teste (e o resto dos ficheiros de teste)
  // verifica texto em portugues.
  I.setIdioma("pt");
})();

// Paridade do painel com o formato.py. Os literais abaixo sao os MESMOS que o
// test_formato.py fixa do lado do Python: se um dos dois lados mudar sozinho
// (separador, ou a regra pt-PT de nao agrupar quatro digitos), um falha.
(function () {
  const I = T.idioma;
  I.setIdioma("pt");
  check("paridade pt: 1234.5 nao agrupa (minimumGroupingDigits=2)",
        I.fmtNum(1234.5, 1) === "1234,5", I.fmtNum(1234.5, 1));
  check("paridade pt: 12345 agrupa com U+00A0",
        I.fmtNum(12345, 0) === "12\u00a0345", I.fmtNum(12345, 0));
  check("paridade pt: milhoes com dois separadores",
        I.fmtNum(1234567, 0) === "1\u00a0234\u00a0567", I.fmtNum(1234567, 0));
  check("paridade pt: negativo com virgula decimal",
        I.fmtNum(-12345.5, 2) === "-12\u00a0345,50", I.fmtNum(-12345.5, 2));
  I.setIdioma("en");
  check("paridade en: quatro digitos agrupam",
        I.fmtNum(1234.5, 1) === "1,234.5", I.fmtNum(1234.5, 1));
  check("paridade en: milhoes com virgula",
        I.fmtNum(1234567, 0) === "1,234,567", I.fmtNum(1234567, 0));
  check("paridade: valor nao numerico devolve vazio",
        I.fmtNum(null, 0) === "" && I.fmtNum(undefined, 0) === "");
  I.setIdioma("pt");
})();

// Deteccao do 409: o `fetchJSON` do dashboard entrega um ApiError com `.status`
// e uma frase humana sem "409" — detectar pelo texto dava sempre falso e o
// painel mostrava a frase generica em ingles, sem recarregar a versao.
(function () {
  const C = T.ehConflito;
  check("conflito: ApiError 409 e reconhecido pelo codigo",
        C({ status: 409, message: "That change conflicts with the current state on the server." }) === true);
  check("conflito: ApiError 400 nao e conflito",
        C({ status: 400, message: "Some of the entered values are not valid." }) === false);
  check("conflito: caminho de recurso (texto com 409) continua reconhecido",
        C(new Error("409: Conflict")) === true);
  check("conflito: erro sem status nem 409 nao e conflito",
        C(new Error("Failed to fetch")) === false);
  check("conflito: nada e nao conflito", C(null) === false && C(undefined) === false);
})();

// O registo de utilizacao dizia `origem: tarefa` e nada mais: nem qual tarefa,
// nem se vinha deste turno, nem que termo a fixou. O dono leu isso como "o
// plugin acha que estou no Home Assistant" num turno de trabalho no model
// manager. A celula passa a dizer tudo, e o `title` leva o digest do texto.
(function () {
  const R = T.rotuloOrigem, D = T.detalheOrigem;
  check("origem: analise fica como esta", R({ origem: "analise" }) === "analise");
  check("origem: sem origem da travessao", R({}) === "—" && R(null) === "—");
  check("origem: tarefa detectada mostra tarefa e termo",
        R({ origem: "tarefa", tarefa: "home-assistant", tarefa_termo: "sensor" })
        === "tarefa · home-assistant · «sensor»");
  check("origem: tarefa herdada mostra a idade em turnos",
        R({ origem: "tarefa", tarefa: "home-assistant",
            tarefa_origem: "herdada", tarefa_idade_turnos: 3 })
        === "tarefa · home-assistant · herdada 3t");
  check("origem: tarefa sem termo continua legivel",
        R({ origem: "tarefa", tarefa: "arquitetura" }) === "tarefa · arquitetura");
  check("origem: detalhe traz o nivel e o digest do texto",
        D({ nivel_origem: "analise", tarefa_texto_sha: "abc123",
            tarefa_texto_chars: 412 })
        === "nivel: analise · texto: abc123 (412 car.)");
  check("origem: sem prova nao inventa detalhe", D({ origem: "modelo" }) === null);
})();

// A lingua vive no config: mudar o seletor grava no servidor e o texto que o
// editor tem em maos tem de acompanhar. Sem isto, o "Gravar" seguinte repunha a
// lingua antiga ou era recusado por `rev` velho — o erro que o dono viu a
// 2026-11-08 08:28:43 ao mudar a lingua e gravar.
(function () {
  const A = T.idioma.aplicarIdiomaNoTexto;
  const cfg = JSON.stringify({ versao: 3, idioma: "pt", perfis: { default: { activo: true } } }, null, 2);
  const r = A(cfg, "en");
  const obj = JSON.parse(r.texto);
  check("idioma no texto: aplica o codigo novo", r.aplicado === true && obj.idioma === "en");
  check("idioma no texto: preserva o resto do documento",
        obj.versao === 3 && obj.perfis.default.activo === true && Object.keys(obj).length === 3);
  check("idioma no texto: normaliza a forma recebida",
        JSON.parse(A(cfg, "PT-BR").texto).idioma === "pt");
  check("idioma no texto: JSON invalido fica como esta",
        A("{nao e json", "en").aplicado === false
        && A("{nao e json", "en").texto === "{nao e json");
  check("idioma no texto: array nao e documento de config", A("[1,2]", "en").aplicado === false);
  check("idioma no texto: vazio e null nao rebentam",
        A(null, "en").aplicado === false && A("", "en").aplicado === false);
})();

// Remover uma rota do catalogo: o botao tem de chegar ao ecra e nao pode
// aparecer onde nao ha nada para remover. Renderiza-se a pagina com o `h`
// falso — o que aqui se prova e a arvore que o React receberia, nao uma string.
(function () {
  const P = T.PaginaModelo;
  check("remover: a pagina do modelo esta exportada para o teste", typeof P === "function");
  const base = {
    perfil: "default",
    estado: {},
    opcoes: { tarefas: [], providers: [] },
    form: { id: "deepseek-flash", provider: "deepseek", modelo: "deepseek-chat" },
    onCampo: function () {},
    onValidar: function () {},
    onGravar: function () {},
    onDescartar: function () {},
    onPedirRemocao: function () {},
    onCancelarRemocao: function () {},
    onRemover: function () {},
  };
  const guardada = P(Object.assign({}, base));
  const textoGuardada = textoDaArvore(guardada);
  check("remover: rota gravada mostra o botao", textoGuardada.indexOf("Remover") !== -1);
  const botao = procurarNaArvore(guardada, function (no) {
    return no.props && no.props.className === "vmm-btn-perigo";
  });
  check("remover: o botao leva a classe destrutiva", !!botao);
  check("remover: o primeiro clique pede confirmacao, nao remove",
        !!botao && typeof botao.props.onClick === "function"
        && botao.props.onClick === base.onPedirRemocao);
  check("remover: ainda nao ha confirmacao na arvore",
        textoGuardada.indexOf("Confirmar remoção") === -1);

  const nova = P(Object.assign({}, base, { novo: true, form: { id: "" } }));
  check("remover: rota nova nao tem botao de remover",
        textoDaArvore(nova).indexOf("Remover") === -1
        && !procurarNaArvore(nova, function (no) {
          return no.props && no.props.className === "vmm-btn-perigo";
        }));

  const aConfirmar = P(Object.assign({}, base, { confirmarRemocao: true }));
  const textoConfirma = textoDaArvore(aConfirmar);
  check("remover: a segunda etapa diz o que vai acontecer",
        textoConfirma.indexOf("Confirmar remoção") !== -1);
  check("remover: a segunda etapa deixa cancelar", textoConfirma.indexOf("Cancelar") !== -1);
  const confirma = procurarNaArvore(aConfirmar, function (no) {
    return no.props && no.props.className === "vmm-btn-perigo";
  });
  check("remover: a segunda etapa chama a remocao",
        !!confirma && confirma.props.onClick === base.onRemover);
  const cancelar = procurarNaArvore(aConfirmar, function (no) {
    return no.props && no.props.onClick === base.onCancelarRemocao;
  });
  check("remover: cancelar volta atras sem chamar a API", !!cancelar);

  const aGravar = P(Object.assign({}, base, { aGravar: true }));
  const desactivado = procurarNaArvore(aGravar, function (no) {
    return no.props && no.props.className === "vmm-btn-perigo";
  });
  check("remover: fica desactivado durante uma gravacao",
        !!desactivado && desactivado.props.disabled === true);
})();

// --- painel do ensaio em sombra (ao lado do registo de utilizacao) ----------
// O dono pediu: "percentagens actualizadas, previsao de quando estara concluido
// e quais os modelos em uso", num painel que so aparece com o modo sombra
// activo. Tres propriedades, cada uma com o seu caso negativo:
//  * a visibilidade depende do interruptor e de mais nada;
//  * um ensaio a correr mostra percentagem, data e modelos;
//  * um ensaio travado mostra o motivo e NENHUMA data (braco morto nao e braco
//    lento), e sem payload nao se inventam zeros.
(function () {
  check("sombra: mostrarPainelSombra so com o ensaio ligado",
        T.mostrarPainelSombra({ sombra: { activo: true } }) === true
        && T.mostrarPainelSombra({ sombra: { activo: false } }) === false);
  check("sombra: sem estado (ou sem bloco) o painel nao aparece",
        T.mostrarPainelSombra(null) === false
        && T.mostrarPainelSombra(undefined) === false
        && T.mostrarPainelSombra({}) === false
        && T.mostrarPainelSombra({ sombra: {} }) === false);

  const payload = {
    sombra: {
      activo: true,
      ensaio: "nvidia/nemotron-3-ultra-550b-a55b:free vs deepseek-flash",
      vida: {
        medida_ts: "2026-11-10T06:54:38+00:00",
        todos_vivos: false,
        bracos: {
          a: { modelo: "deepseek-flash", ok: true, http: 200 },
          b: { modelo: "nvidia/nemotron-3-ultra-550b-a55b:free", ok: false, http: 404 },
        },
      },
    },
    progresso: {
      unidade: "chamada de ferramenta medida por exit_code",
      alvo_por_braco: 18536,
      alvo_total: 37072,
      selo: "5275bbf6eba15dbbe09d80f7f68c5b34422e8541b4c02dcb1b85d5f323e898f2",
      ensaio: "nvidia/nemotron-3-ultra-550b-a55b:free vs deepseek-flash",
      progresso: 0.0733,
      estado: "travado",
      motivo: "braço b sem vida (nvidia/nemotron-3-ultra-550b-a55b:free, HTTP 404)",
      fim_estimado: "",
      fim_estimado_ultimo_dia: "",
      braco_a: { modelo: "deepseek-flash", provider: "", chamadas: 9505, taxa: 0.0638,
                 progresso: 0.5128, restante: 9031, ritmo_dia: 1765.9,
                 dias_restantes: 5.1, primeira_ts: "2026-11-05T00:59:06+00:00",
                 ultima_ts: "2026-11-10T10:09:38+00:00" },
      braco_b: { modelo: "nvidia/nemotron-3-ultra-550b-a55b:free", provider: "",
                 chamadas: 1359, taxa: 0.2053, progresso: 0.0733, restante: 17177,
                 ritmo_dia: 271.5, dias_restantes: 63.3,
                 primeira_ts: "2026-11-05T10:02:18+00:00",
                 ultima_ts: "2026-11-09T20:29:21+00:00" },
      vida: { todos_vivos: false, medida_ts: "2026-11-10T06:54:38+00:00" },
    },
  };

  const arvore = T.PainelSombra({ dados: payload, aCarregar: false });
  const texto = textoDaArvore(arvore);
  check("sombra: o painel tem titulo proprio", texto.indexOf("Ensaio em sombra — progresso") !== -1,
        texto.slice(0, 160));
  check("sombra: mostra a percentagem dos dois bracos",
        texto.indexOf("51,28") !== -1 && texto.indexOf("7,33") !== -1, texto);
  check("sombra: mostra a percentagem do ensaio pelo braco que atrasa",
        texto.indexOf("7,33%") !== -1, texto);
  check("sombra: nomeia os modelos em uso", texto.indexOf("modelos em uso") !== -1
        && texto.indexOf("deepseek-flash") !== -1
        && texto.indexOf("nvidia/nemotron-3-ultra-550b-a55b:free") !== -1, texto);
  check("sombra: travado nao mostra data e diz o motivo",
        texto.indexOf("sem previsão de fim") !== -1
        && texto.indexOf("HTTP 404") !== -1, texto);
  const antesModelos = texto.split("modelos em uso")[0];
  check("sombra: travado nao mostra data nenhuma",
        antesModelos.indexOf("sem previsão de fim") !== -1
        && antesModelos.indexOf("/2026") === -1, texto);
  check("sombra: declara a unidade da medida",
        texto.indexOf("chamada de ferramenta medida por exit_code") !== -1, texto);
  const barras = recolher(arvore, function (no) {
    return no.props && no.props.className === "vmm-barra-fill";
  });
  check("sombra: uma barra por braco", barras.length === 2, "obtive " + barras.length);

  // Ensaio a correr: aqui a data existe (o caso positivo do mesmo componente).
  const aCorrer = JSON.parse(JSON.stringify(payload));
  aCorrer.progresso.estado = "a_correr";
  aCorrer.progresso.fim_estimado = "2026-12-15T00:00:00+00:00";
  aCorrer.progresso.motivo = "";
  aCorrer.sombra.vida.todos_vivos = true;
  const textoCorrer = textoDaArvore(T.PainelSombra({ dados: aCorrer }));
  check("sombra: a correr mostra a data de fim",
        textoCorrer.indexOf("previsão de fim: ") !== -1
        && textoCorrer.indexOf("15/12/2026") !== -1, textoCorrer);
  check("sombra: a data vem com a ancora do ritmo",
        textoCorrer.indexOf("ritmo médio desde") !== -1, textoCorrer);

  // Sem payload: diz que falta o reinicio em vez de desenhar zeros medidos.
  const semPayload = textoDaArvore(T.PainelSombra({ dados: null, aCarregar: false }));
  check("sombra: sem payload nao inventa numeros",
        semPayload.indexOf("progresso indisponível") !== -1
        && semPayload.indexOf("51,28") === -1, semPayload);

  // Pausa de quota: um 429 da quota diaria nao e braco morto. O painel tem de
  // dizer "em pausa (quota)" e continuar sem data de fim — a pausa ainda nao
  // tem ritmo medido. Antes da 3.48.1 isto saia "travado", indistinguivel de
  // um slug retirado, e o ensaio so voltava na sonda seguinte.
  const emPausa = JSON.parse(JSON.stringify(payload));
  emPausa.progresso.estado = "em_pausa";
  emPausa.progresso.motivo = "braço b sem quota (nvidia/nemotron-3-ultra-550b-a55b:free, HTTP 429)";
  emPausa.progresso.pausa_ate = "2026-11-20T00:00:00+00:00";
  const textoPausa = textoDaArvore(T.PainelSombra({ dados: emPausa }));
  check("sombra: quota esgotada diz 'em pausa (quota)'",
        textoPausa.indexOf("em pausa (quota)") !== -1, textoPausa);
  check("sombra: em pausa nao mostra data de fim",
        textoPausa.split("modelos em uso")[0].indexOf("sem previsão de fim") !== -1
        && textoPausa.split("modelos em uso")[0].indexOf("/2026") === -1, textoPausa);
  const aCarregarTxt = textoDaArvore(T.PainelSombra({ dados: null, aCarregar: true }));
  check("sombra: enquanto le, diz que esta a ler",
        aCarregarTxt.indexOf("a ler o ledger do ensaio") !== -1, aCarregarTxt);
})();

// ------------------------------------- interruptor do ensaio em sombra POR PERFIL
// O dono pediu um botao na pagina de cada perfil para ligar/desligar o ensaio so
// nesse perfil, mantendo o global na pagina de configuracao. O que se prova:
//   * o estado mostrado vem do payload (`sombra.perfis.<nome>`), nunca inventado;
//   * o botao pede exactamente o que o servidor le (`activo` ou `herdar`);
//   * «Seguir o global» so existe quando ha escolha propria para largar;
//   * a pagina do perfil monta o bloco e entrega o gesto ao painel;
//   * o resumo por perfil ao lado do global nao esconde quem nao obedece.
// O fixture tem a forma do `/estado` vivo medido a 2026-11-19 (quatro perfis,
// um de cada origem que la existia).
const SOMBRA_PERFIS = {
  activo: true,
  global: { activo: true, vida_ttl_h: 24, global_control: { activo: true, por: "cli:sombra" } },
  perfis: {
    "default": { activo: true, vida_ttl_h: 24, origem: "global", declarado: {} },
    pessoal: { activo: true, vida_ttl_h: 24, origem: "herdado", declarado: { herda: true } },
    trabalho: { activo: false, vida_ttl_h: 24, origem: "proprio", declarado: { activo: false } },
    financas: { activo: true, vida_ttl_h: 24, origem: "global", declarado: {} },
  },
};

function clonar(x) { return JSON.parse(JSON.stringify(x)); }

(function () {
  const t = carregar();
  const estado = { sombra: clonar(SOMBRA_PERFIS) };

  // --- leitura pura -------------------------------------------------------
  const fin = t.sombraDoPerfil(estado, "financas");
  check("sombra/perfil: le o efectivo e a origem do payload",
        fin.conhecido === true && fin.activo === true && fin.origem === "global"
          && fin.globalConhecido === true && fin.globalActivo === true, JSON.stringify(fin));
  const trab = t.sombraDoPerfil(estado, "trabalho");
  check("sombra/perfil: perfil desligado por escolha propria",
        trab.conhecido === true && trab.activo === false && trab.origem === "proprio",
        JSON.stringify(trab));
  check("sombra/perfil: perfil que nao vem no payload nao e conhecido",
        t.sombraDoPerfil(estado, "nao-existe").conhecido === false);
  check("sombra/perfil: sem bloco sombra nao e conhecido (payload antigo)",
        t.sombraDoPerfil({}, "financas").conhecido === false
          && t.sombraDoPerfil(null, "financas").conhecido === false);
  check("sombra/perfil: `activo` que nao e booleano nao e conhecido",
        t.sombraDoPerfil({ sombra: { perfis: { x: { origem: "global" } } } }, "x").conhecido === false
          && t.sombraDoPerfil({ sombra: { perfis: { x: { activo: "true" } } } }, "x").conhecido === false);
  check("sombra/perfil: nome de propriedade herdada nao passa por perfil",
        t.sombraDoPerfil(estado, "constructor").conhecido === false
          && t.sombraDoPerfil(estado, "toString").conhecido === false);
  check("sombra/perfil: os quatro rotulos de origem",
        t.rotuloOrigemSombra("proprio") === "próprio deste perfil"
          && t.rotuloOrigemSombra("global") === "segue o global"
          && t.rotuloOrigemSombra("herdado") === "segue o global (herança declarada)"
          && t.rotuloOrigemSombra("padrao") === "padrão (nada declarado)");
  check("sombra/perfil: origem desconhecida aparece tal e qual, nao some",
        t.rotuloOrigemSombra("outra") === "outra" && t.rotuloOrigemSombra("") === "?");

  // --- o bloco: botoes e o que pedem ---------------------------------------
  function bloco(perfil, extra, est) {
    const pedidos = [];
    const props = Object.assign({
      estado: est || estado, perfil: perfil, aGravar: false, aviso: "",
      onSombraPerfil: function () { pedidos.push(Array.prototype.slice.call(arguments)); },
    }, extra || {});
    return { arvore: t.blocoSombraPerfil(props), pedidos: pedidos };
  }
  function interruptores(arvore) { return comTag(arvore, "Interruptor"); }
  function botaoSeguir(arvore) {
    return comTag(arvore, "button").concat(comTag(arvore, "Button")).filter(function (b) {
      return textoPlano(b.filhos).trim() === "Seguir o global";
    });
  }

  const bFin = bloco("financas");
  check("sombra/perfil: perfil ligado mostra UM interruptor, ligado",
        interruptores(bFin.arvore).length === 1
          && interruptores(bFin.arvore)[0].props.activo === true,
        "n=" + interruptores(bFin.arvore).length);
  check("sombra/perfil: o titulo diz o estado em vigor e de onde vem",
        /em vigor: ligado · segue o global/.test(textoPlano(bFin.arvore)),
        textoPlano(bFin.arvore).slice(0, 120));
  check("sombra/perfil: diz o estado do interruptor global",
        /interruptor global: ligado/.test(textoPlano(bFin.arvore)));
  interruptores(bFin.arvore)[0].props.onClick();
  check("sombra/perfil: carregar no ligado pede desligar SO este perfil",
        bFin.pedidos.length === 1 && bFin.pedidos[0].length === 1
          && JSON.stringify(bFin.pedidos[0][0]) === JSON.stringify({ activo: false }),
        JSON.stringify(bFin.pedidos));
  check("sombra/perfil: quem segue o global nao tem «Seguir o global»",
        botaoSeguir(bFin.arvore).length === 0);
  check("sombra/perfil: o titulo do botao diz que e so neste perfil",
        /só neste perfil/.test(interruptores(bFin.arvore)[0].props.title || ""),
        interruptores(bFin.arvore)[0].props.title);

  const bTrab = bloco("trabalho");
  check("sombra/perfil: perfil desligado mostra o interruptor desligado",
        interruptores(bTrab.arvore).length === 1
          && interruptores(bTrab.arvore)[0].props.activo === false);
  interruptores(bTrab.arvore)[0].props.onClick();
  check("sombra/perfil: carregar no desligado pede ligar este perfil",
        JSON.stringify(bTrab.pedidos[0]) === JSON.stringify([{ activo: true }]),
        JSON.stringify(bTrab.pedidos));
  const seguir = botaoSeguir(bTrab.arvore);
  check("sombra/perfil: com escolha propria aparece «Seguir o global»", seguir.length === 1,
        "n=" + seguir.length);
  if (seguir.length === 1) {
    seguir[0].props.onClick();
    check("sombra/perfil: «Seguir o global» pede herdar, e so isso",
          JSON.stringify(bTrab.pedidos[1]) === JSON.stringify([{ herdar: true }]),
          JSON.stringify(bTrab.pedidos));
  }
  check("sombra/perfil: o estado do global continua a vista com o perfil desligado",
        /em vigor: desligado · próprio deste perfil/.test(textoPlano(bTrab.arvore))
          && /interruptor global: ligado/.test(textoPlano(bTrab.arvore)),
        textoPlano(bTrab.arvore).slice(0, 160));

  const bPes = bloco("pessoal");
  check("sombra/perfil: heranca declarada le-se como tal e nao tem «Seguir o global»",
        /segue o global \(herança declarada\)/.test(textoPlano(bPes.arvore))
          && botaoSeguir(bPes.arvore).length === 0);

  // Global desligado, perfil ligado por escolha propria: o caso que o texto de
  // ajuda descreve tem de se ver no ecra, nao so no ficheiro.
  const estGlobalOff = { sombra: clonar(SOMBRA_PERFIS) };
  estGlobalOff.sombra.global.activo = false;
  estGlobalOff.sombra.perfis.financas = { activo: true, origem: "proprio", declarado: { activo: true } };
  const bOff = bloco("financas", null, estGlobalOff);
  check("sombra/perfil: ligado no perfil com o global desligado mostra os dois",
        /em vigor: ligado · próprio deste perfil/.test(textoPlano(bOff.arvore))
          && /interruptor global: desligado/.test(textoPlano(bOff.arvore)),
        textoPlano(bOff.arvore).slice(0, 160));

  // Sem estado para o perfil: nenhum botao, e o bloco diz porque.
  const bSem = bloco("financas", null, { sombra: { activo: true } });
  check("sombra/perfil: sem estado do perfil nao ha interruptor nem botoes",
        interruptores(bSem.arvore).length === 0 && botaoSeguir(bSem.arvore).length === 0
          && comTag(bSem.arvore, "button").length === 0);
  check("sombra/perfil: sem estado diz que nao veio, em vez de mostrar desligado",
        /não veio no payload/.test(textoPlano(bSem.arvore))
          && !/desligado ·/.test(textoPlano(bSem.arvore)),
        textoPlano(bSem.arvore));

  // A gravar: nada clicavel fica activo.
  const bGravar = bloco("trabalho", { aGravar: true });
  check("sombra/perfil: a gravar, o interruptor e «Seguir o global» ficam presos",
        interruptores(bGravar.arvore)[0].props.aGravar === true
          && botaoSeguir(bGravar.arvore)[0].props.disabled === true);
  check("sombra/perfil: o aviso do gesto aparece no bloco",
        /ensaio neste perfil: desligado/.test(
          textoPlano(bloco("trabalho", { aviso: "ensaio neste perfil: desligado" }).arvore)));
  check("sombra/perfil: o bloco diz que grava de imediato e que «Descartar» nao desfaz",
        /Grava de imediato/.test(textoPlano(bFin.arvore))
          && /«Descartar» não o desfaz/.test(textoPlano(bFin.arvore)));

  // --- a pagina do perfil monta o bloco e entrega o gesto -------------------
  function pagina(perfil, extra) {
    return t.PaginaPerfil(Object.assign({
      estado: estado, perfil: perfil, form: { activo: true, tarefas: [], cls: {}, clsHerdar: true,
                                              tpHerdar: true, estHerdar: true },
      opcoes: {}, rev: "abc", efectivo: {}, global: {},
      onCampo: function () {}, onCampoCls: function () {}, onTarefa: function () {},
      onNovaTarefa: function () {}, onRemoverTarefa: function () {},
      onValidar: function () {}, onGravar: function () {}, onDescartar: function () {},
    }, extra || {}));
  }
  const recebidos = [];
  const pag = pagina("financas", { onSombraPerfil: function (c) { recebidos.push(c); } });
  const blocosSombra = comTag(pag, "div").filter(function (x) {
    return /vmm-bloco-sombra-perfil/.test(String(x.props && x.props.className || ""));
  });
  check("sombra/perfil: a pagina do perfil monta o bloco UMA vez", blocosSombra.length === 1,
        "n=" + blocosSombra.length);
  check("sombra/perfil: a pagina mostra o interruptor do perfil aberto",
        interruptores(pag).length === 1 && interruptores(pag)[0].props.activo === true);
  interruptores(pag)[0].props.onClick();
  check("sombra/perfil: o gesto na pagina chega ao painel com o corpo certo",
        JSON.stringify(recebidos) === JSON.stringify([{ activo: false }]),
        JSON.stringify(recebidos));
  check("sombra/perfil: a pagina de outro perfil le o estado DESSE perfil",
        interruptores(pagina("trabalho")).length === 1
          && interruptores(pagina("trabalho"))[0].props.activo === false);
  let rebentou = "";
  try { interruptores(pagina("financas"))[0].props.onClick(); } catch (e) { rebentou = String(e); }
  check("sombra/perfil: sem handler do painel o clique nao rebenta", rebentou === "", rebentou);
  check("sombra/perfil: enquanto o gesto grava, a pagina prende o interruptor",
        interruptores(pagina("financas", { sombraAGravar: true }))[0].props.aGravar === true
          && interruptores(pagina("financas", { aGravar: true }))[0].props.aGravar === true
          && interruptores(pagina("financas"))[0].props.aGravar === false);
  check("sombra/perfil: a ler o perfil, o bloco nao e desenhado",
        interruptores(pagina("financas", { aCarregar: true })).length === 0);
  // Os botoes do formulario continuam os mesmos tres: o interruptor nao e um
  // quarto «gravar» disfarcado.
  const rotulos = comTag(pag, "button").concat(comTag(pag, "Button"))
    .map(function (b) { return textoPlano(b.filhos).trim(); });
  check("sombra/perfil: o formulario mantem Validar, Gravar e voltar e Descartar",
        rotulos.filter(function (r) { return r === "Validar" || /Gravar e voltar/.test(r) || r === "Descartar"; }).length === 3,
        JSON.stringify(rotulos));

  // --- o resumo por perfil ao lado do interruptor GLOBAL --------------------
  const linha = t.linhaPerfisSombra(estado.sombra);
  const txtLinha = textoPlano(linha);
  check("sombra/global: o resumo lista os quatro perfis com estado e origem",
        /default: ligado \(segue o global\)/.test(txtLinha)
          && /pessoal: ligado \(segue o global \(herança declarada\)\)/.test(txtLinha)
          && /trabalho: desligado \(próprio deste perfil\)/.test(txtLinha)
          && /financas: ligado \(segue o global\)/.test(txtLinha), txtLinha);
  check("sombra/global: sem perfis no payload nao ha linha",
        t.linhaPerfisSombra({ activo: true }) === null && t.linhaPerfisSombra(null) === null);
  check("sombra/global: perfil sem estado fica de fora em vez de aparecer desligado",
        !/fantasma/.test(textoPlano(t.linhaPerfisSombra({
          perfis: { fantasma: { origem: "global" }, a: { activo: true, origem: "global" } } }))));
  const secao = t.SecaoSombra({ sombra: estado.sombra, catalogo: [], aGravar: false,
                                onSombra: function () {}, onBracos: function () {} });
  check("sombra/global: a seccao global mostra o resumo por perfil",
        /trabalho: desligado \(próprio deste perfil\)/.test(textoPlano(secao)));
  const seletoresGlobais = comTag(secao, "Seletor").filter(function (s) {
    return s.props.rotulo === "ensaio";
  });
  check("sombra/global: o interruptor global continua la, um so",
        seletoresGlobais.length === 1 && seletoresGlobais[0].props.valor === "ligado");
  const pedidosGlobais = [];
  t.SecaoSombra({ sombra: estado.sombra, catalogo: [], aGravar: false,
                  onSombra: function (c) { pedidosGlobais.push(c); }, onBracos: function () {} });
  comTag(t.SecaoSombra({ sombra: estado.sombra, catalogo: [], aGravar: false,
                         onSombra: function (c) { pedidosGlobais.push(c); },
                         onBracos: function () {} }), "Seletor")
    .filter(function (s) { return s.props.rotulo === "ensaio"; })[0].props.onChange("desligado");
  check("sombra/global: o interruptor global continua a pedir so `activo`, sem perfil",
        JSON.stringify(pedidosGlobais) === JSON.stringify([{ activo: false }]),
        JSON.stringify(pedidosGlobais));

  // --- ingles: o bloco novo nao fica em portugues --------------------------
  const en = carregar();
  en.idioma.setIdioma("en");
  const txtEn = textoPlano(en.blocoSombraPerfil({
    estado: estado, perfil: "trabalho", aGravar: false, aviso: "", onSombraPerfil: function () {},
  }));
  check("sombra/perfil: em ingles o bloco sai traduzido",
        /Shadow trial/.test(txtEn) && /in force: off · set on this profile/.test(txtEn)
          && /Follow the global/.test(txtEn) && /global switch: on/.test(txtEn)
          && !/Ensaio em sombra|próprio deste perfil|Seguir o global|Grava de imediato/.test(txtEn),
        txtEn.slice(0, 260));
  check("sombra/global: em ingles o resumo por perfil sai traduzido",
        /per profile/.test(textoPlano(en.linhaPerfisSombra(estado.sombra)))
          && /trabalho: off \(set on this profile\)/.test(textoPlano(en.linhaPerfisSombra(estado.sombra))),
        textoPlano(en.linhaPerfisSombra(estado.sombra)));
})();

// ------------------------- o gesto de ponta a ponta, com a `Pagina` a correr
// Os testes de cima provam o desenho; este prova a cablagem que um teste de
// desenho nao ve — a `Pagina` a serio, com estado que persiste entre renders e
// um servidor falso que regista cada pedido. E aqui que se apanha um `rev`
// errado, um perfil trocado, ou um «gravado» que nao releu o servidor.
function carregarVivo(responder) {
  const estados = [];
  const pedidos = [];
  let cursor = 0;
  let primeiro = true;
  const efeitos = [];
  const React = {
    createElement: function (tag, props) {
      return {
        tag: typeof tag === "function" ? (tag.name || "Componente") : tag,
        props: props || {},
        filhos: Array.prototype.slice.call(arguments, 2),
      };
    },
    useState: function (inicial) {
      const i = cursor; cursor += 1;
      if (!(i in estados)) estados[i] = inicial;
      return [estados[i], function (v) {
        estados[i] = typeof v === "function" ? v(estados[i]) : v;
      }];
    },
    // So o primeiro render dispara efeitos: e o carregamento inicial da pagina.
    useEffect: function (fn) { if (primeiro) efeitos.push(fn); },
    useCallback: function (f) { return f; },
    useMemo: function (f) { return f(); },
    useRef: function (v) { const i = cursor++; return estados[i] || (estados[i] = { current: v }); },
    Fragment: "Fragment",
  };
  const registos = {};
  const ferramentas = {};
  const janela = {
    __HERMES_PLUGIN_SDK__: {
      React: React, hooks: {}, components: {},
      fetchJSON: function (url, opts) {
        const pedido = {
          url: String(url).replace("/api/plugins/vipernet-model-manager", ""),
          metodo: (opts && opts.method) || "GET",
          corpo: opts && typeof opts.body === "string" ? JSON.parse(opts.body) : null,
        };
        pedidos.push(pedido);
        try { return Promise.resolve(responder(pedido)); }
        catch (e) { return Promise.reject(e); }
      },
    },
    __HERMES_PLUGINS__: { register: function (nome, comp) { registos[nome] = comp; } },
    __VMM_TEST__: ferramentas,
  };
  const contexto = { window: janela, document: { addEventListener: function () {} },
                     console: console, JSON: JSON, Object: Object, Array: Array };
  vm.createContext(contexto);
  vm.runInContext(fs.readFileSync(path.join(__dirname, "dist", "index.js"), "utf-8"),
                  contexto, { filename: "index.js" });
  ferramentas.idioma.setIdioma("pt");
  const Pagina = registos["vipernet-model-manager"];
  return {
    pedidos: pedidos,
    render: function () {
      cursor = 0;
      const arvore = Pagina();
      if (primeiro) { primeiro = false; efeitos.splice(0).forEach(function (f) { f(); }); }
      return arvore;
    },
    // Corre um componente filho A SERIO, com estado que persiste entre renders.
    // As posicoes de estado do filho vem a seguir as da `Pagina`, por isso a
    // regra e uma so: `render()` primeiro e os filhos sempre pela mesma ordem.
    filho: function (nome, props) { return ferramentas[nome](props); },
    assentar: function () {
      // As cadeias do painel tem varios `.then`: dez voltas ao ciclo de eventos
      // chegam para a mais comprida (pedido -> estado -> progresso -> fim).
      let p = Promise.resolve();
      for (let i = 0; i < 10; i += 1) {
        p = p.then(function () { return new Promise(function (r) { setImmediate(r); }); });
      }
      return p;
    },
  };
}

async function provaDoGestoPorPerfil() {
  // Servidor falso com estado: o POST muda o que o GET seguinte devolve, como o
  // verdadeiro. Um painel que mostrasse o que PEDIU em vez do que o servidor
  // respondeu passava num mock sem estado e falha aqui.
  const servidor = { rev: "rev-1", sombra: clonar(SOMBRA_PERFIS), recusar: null };
  function responder(p) {
    if (p.url === "/estado") {
      return { rev: servidor.rev, idioma: "pt", activo: true, sombra: clonar(servidor.sombra),
               perfis: { financas: { activo: true, catalogo: [], tarefas: {}, estrategia: {}, analise: {} },
                         trabalho: { activo: true, catalogo: [], tarefas: {}, estrategia: {}, analise: {} } },
               opcoes: {}, erros_config: [] };
    }
    if (p.url.indexOf("/log") === 0) return { linhas: [] };
    if (p.url === "/config") return { config: { perfis: {} }, rev: servidor.rev };
    if (p.url === "/sombra/progresso") return { sombra: clonar(servidor.sombra), progresso: {} };
    if (p.url.indexOf("/perfil?nome=") === 0) {
      return { perfil: decodeURIComponent(p.url.split("=")[1]), rev: servidor.rev,
               declarado: { activo: true, tarefas: {} }, efectivo: {}, global: {}, opcoes: {} };
    }
    if (p.url === "/sombra" && p.metodo === "POST") {
      if (servidor.recusar) {
        const erro = new Error(servidor.recusar.mensagem);
        erro.status = servidor.recusar.status;
        throw erro;
      }
      if (p.corpo.rev !== servidor.rev) {
        const conflito = new Error("a configuração mudou desde que este painel leu o estado");
        conflito.status = 409;
        throw conflito;
      }
      const alvo = servidor.sombra.perfis[p.corpo.perfil];
      if (p.corpo.herdar) {
        alvo.activo = servidor.sombra.global.activo; alvo.origem = "herdado";
      } else {
        alvo.activo = !!p.corpo.activo; alvo.origem = "proprio";
      }
      servidor.rev = "rev-" + (Number(servidor.rev.split("-")[1]) + 1);
      return { ok: true, rev: servidor.rev, sombra: clonar(servidor.sombra), controlo: [] };
    }
    throw new Error("pedido inesperado: " + p.metodo + " " + p.url);
  }

  const painel = carregarVivo(responder);
  painel.render();
  await painel.assentar();
  let arvore = painel.render();
  const cartoes = comTag(arvore, "CartaoPerfil");
  check("gesto: o painel principal desenha os cartoes dos perfis", cartoes.length === 2,
        "n=" + cartoes.length);

  // Abrir a pagina do perfil financas, como o ⚙ do cartao faz.
  cartoes.filter(function (c) { return c.props.nome === "financas"; })[0].props.onPerfil("financas");
  await painel.assentar();
  arvore = painel.render();
  check("gesto: abrir o perfil mostra a pagina do perfil",
        arvore.tag === "PaginaPerfil" && arvore.props.perfil === "financas", arvore.tag);
  check("gesto: a pagina recebe o handler do interruptor e o estado do painel",
        typeof arvore.props.onSombraPerfil === "function"
          && arvore.props.estado.sombra.perfis.financas.activo === true
          && arvore.props.rev === "rev-1", JSON.stringify(arvore.props.rev));

  // Desligar o ensaio so neste perfil.
  const antes = painel.pedidos.length;
  arvore.props.onSombraPerfil({ activo: false });
  const aMeio = painel.render();
  check("gesto: enquanto o pedido esta fora, a pagina diz que esta a gravar",
        aMeio.props.sombraAGravar === true);
  await painel.assentar();
  arvore = painel.render();
  const novos = painel.pedidos.slice(antes);
  const posts = novos.filter(function (p) { return p.metodo === "POST"; });
  check("gesto: sai UM POST, para /sombra", posts.length === 1 && posts[0].url === "/sombra",
        JSON.stringify(posts.map(function (p) { return p.metodo + " " + p.url; })));
  check("gesto: o corpo leva o perfil aberto, o `activo` pedido e o rev lido pela pagina",
        JSON.stringify(posts[0].corpo) === JSON.stringify({ rev: "rev-1", perfil: "financas", activo: false }),
        JSON.stringify(posts[0].corpo));
  check("gesto: depois de gravar, o painel RELE o estado do servidor",
        novos.some(function (p) { return p.metodo === "GET" && p.url === "/estado"; }),
        JSON.stringify(novos.map(function (p) { return p.metodo + " " + p.url; })));
  check("gesto: a pagina passa a mostrar o perfil desligado, lido do servidor",
        arvore.tag === "PaginaPerfil"
          && arvore.props.estado.sombra.perfis.financas.activo === false
          && arvore.props.estado.sombra.perfis.financas.origem === "proprio");
  check("gesto: o outro perfil e o global ficaram como estavam",
        arvore.props.estado.sombra.perfis.trabalho.activo === false
          && arvore.props.estado.sombra.perfis.trabalho.origem === "proprio"
          && arvore.props.estado.sombra.perfis.pessoal.activo === true
          && arvore.props.estado.sombra.global.activo === true);
  check("gesto: a pagina adopta o rev novo (o «Gravar e voltar» a seguir nao da conflito)",
        arvore.props.rev === "rev-2", String(arvore.props.rev));
  check("gesto: o aviso diz o que ficou, com a origem",
        arvore.props.avisoSombra === "ensaio neste perfil: desligado · próprio deste perfil",
        String(arvore.props.avisoSombra));
  check("gesto: acabado o pedido, o interruptor volta a estar livre e sem erro",
        arvore.props.sombraAGravar === false && !arvore.props.erro, String(arvore.props.erro));
  check("gesto: a pagina do perfil continua aberta (o interruptor nao fecha o formulario)",
        arvore.tag === "PaginaPerfil" && arvore.props.perfil === "financas");

  // «Seguir o global».
  arvore.props.onSombraPerfil({ herdar: true });
  await painel.assentar();
  arvore = painel.render();
  const ultimoPost = painel.pedidos.filter(function (p) { return p.metodo === "POST"; }).pop();
  check("gesto: «Seguir o global» manda `herdar` com o rev novo e sem `activo`",
        JSON.stringify(ultimoPost.corpo) === JSON.stringify({ rev: "rev-2", perfil: "financas", herdar: true }),
        JSON.stringify(ultimoPost.corpo));
  check("gesto: o perfil volta a seguir o global",
        arvore.props.estado.sombra.perfis.financas.activo === true
          && arvore.props.estado.sombra.perfis.financas.origem === "herdado"
          && arvore.props.rev === "rev-3");

  // Conflito: outra sessao escreveu entretanto.
  servidor.rev = "rev-99";
  const postsAntes = painel.pedidos.filter(function (p) { return p.metodo === "POST"; }).length;
  arvore.props.onSombraPerfil({ activo: false });
  await painel.assentar();
  arvore = painel.render();
  check("gesto: com rev velho o servidor recusa e o perfil NAO muda",
        painel.pedidos.filter(function (p) { return p.metodo === "POST"; }).length === postsAntes + 1
          && servidor.sombra.perfis.financas.activo === true
          && arvore.props.estado.sombra.perfis.financas.activo === true);
  check("gesto: o conflito diz que nao gravou e o que fazer",
        /O interruptor do ensaio não foi gravado/.test(String(arvore.props.erro))
          && /Descartar e abrir outra vez/.test(String(arvore.props.erro)),
        String(arvore.props.erro));
  check("gesto: num conflito nao fica aviso de sucesso nem «a gravar» preso",
        arvore.props.avisoSombra === "" && arvore.props.sombraAGravar === false,
        JSON.stringify([arvore.props.avisoSombra, arvore.props.sombraAGravar]));
  check("gesto: num conflito a pagina NAO adopta um rev que nao leu",
        arvore.props.rev === "rev-3", String(arvore.props.rev));
  // O conflito fez o painel reler o `/estado`, que ja traz o rev novo do
  // servidor. Um segundo clique NAO pode ir buscar esse rev: a pagina continua
  // com o formulario que leu antes da escrita alheia, e adoptar o rev fresco
  // deixava o «Gravar e voltar» repor por cima o que a outra sessao escreveu.
  check("gesto: o painel releu o estado e conhece o rev novo do servidor",
        arvore.props.estado.rev === "rev-99", String(arvore.props.estado.rev));
  arvore.props.onSombraPerfil({ activo: false });
  await painel.assentar();
  arvore = painel.render();
  const repetido = painel.pedidos.filter(function (p) { return p.metodo === "POST"; }).pop();
  check("gesto: insistir depois do conflito continua a mandar o rev que a PAGINA leu",
        repetido.corpo.rev === "rev-3" && servidor.sombra.perfis.financas.activo === true
          && arvore.props.rev === "rev-3"
          && /O interruptor do ensaio não foi gravado/.test(String(arvore.props.erro)),
        JSON.stringify(repetido.corpo) + " rev da pagina=" + arvore.props.rev);

  // Recusa do servidor que nao e conflito: o motivo chega ao ecra.
  servidor.rev = "rev-3";
  servidor.recusar = { status: 400, mensagem: "nao gravou: perfil desconhecido: financas" };
  arvore.props.onSombraPerfil({ activo: false });
  await painel.assentar();
  arvore = painel.render();
  check("gesto: uma recusa mostra o motivo do servidor",
        /Erro ao gravar o ensaio: nao gravou: perfil desconhecido: financas/.test(String(arvore.props.erro)),
        String(arvore.props.erro));
  servidor.recusar = null;

  // Descartar a pagina do perfil nao manda nada para o servidor.
  const totalAntes = painel.pedidos.filter(function (p) { return p.metodo === "POST"; }).length;
  arvore.props.onDescartar();
  await painel.assentar();
  arvore = painel.render();
  check("gesto: «Descartar» fecha a pagina sem escrever",
        arvore.tag !== "PaginaPerfil"
          && painel.pedidos.filter(function (p) { return p.metodo === "POST"; }).length === totalAntes);
}

// ------------------- re-selar os bracos: a recusa do servidor chega ao ecra
// O gesto atravessa quatro componentes (`Pagina` -> `PaginaConfig` ->
// `SecaoClassificador` -> `SecaoSombra`) e o handler vive no primeiro. Um teste
// que chame so a seccao com um `onBracos` falso nunca executa o `catch` do
// handler verdadeiro: aqui correm os quatro a serio, com o clique no botao.
async function provaDoReSelarRecusado() {
  const servidor = {
    rev: "rev-1", selo: "selo-antigo",
    bracos: { a: "modelo-a", b: "modelo-b" },
    recusar: { status: 400, mensagem: "modelo-fantasma não está no catálogo de nenhum perfil" },
  };
  function sombraDoServidor() {
    return Object.assign(clonar(SOMBRA_PERFIS), {
      selo: servidor.selo, bracos: clonar(servidor.bracos),
      providers: { a: "prov-a", b: "prov-b" }, providers_declarados: true,
    });
  }
  function responder(p) {
    if (p.url === "/estado") {
      return { rev: servidor.rev, idioma: "pt", activo: true, sombra: sombraDoServidor(),
               perfis: { financas: { activo: true, catalogo: [], tarefas: {}, estrategia: {}, analise: {} } },
               opcoes: {}, erros_config: [] };
    }
    if (p.url.indexOf("/log") === 0) return { linhas: [] };
    if (p.url === "/config") return { config: { perfis: {} }, rev: servidor.rev };
    if (p.url === "/sombra/progresso") return { sombra: sombraDoServidor(), progresso: {} };
    if (p.url === "/sombra/bracos" && p.metodo === "POST") {
      if (servidor.recusar) {
        const erro = new Error(servidor.recusar.mensagem);
        erro.status = servidor.recusar.status;
        throw erro;
      }
      servidor.bracos = { a: p.corpo.braco_a, b: p.corpo.braco_b };
      servidor.selo = "selo-novo";
      return { ok: true, pre_registo: { selo_valido: true, sha256_ficheiro: "abcdef0123456789abcdef" } };
    }
    throw new Error("pedido inesperado: " + p.metodo + " " + p.url);
  }

  const painel = carregarVivo(responder);
  // Um render completo: a pagina e, dentro dela, os tres filhos ate a seccao.
  function montar() {
    const raiz = painel.render();
    if (raiz.tag !== "PaginaConfig") return { raiz: raiz, sec: null };
    const cfg = painel.filho("PaginaConfig", raiz.props);
    const noClassif = comTag(cfg, "SecaoClassificador")[0];
    const classif = noClassif ? painel.filho("SecaoClassificador", noClassif.props) : null;
    const noSombra = classif ? comTag(classif, "SecaoSombra")[0] : null;
    return { raiz: raiz, sec: noSombra ? painel.filho("SecaoSombra", noSombra.props) : null };
  }
  function botaoReSelar(sec) {
    return comTag(sec, "button").concat(comTag(sec, "Button")).filter(function (b) {
      return textoPlano(b.filhos).trim() === "Re-selar braços";
    })[0];
  }
  function posts() {
    return painel.pedidos.filter(function (p) { return p.metodo === "POST"; });
  }

  painel.render();
  await painel.assentar();
  // «Editar configuração», como no painel principal.
  comTag(painel.render(), "button").concat(comTag(painel.render(), "Button")).filter(function (b) {
    return textoPlano(b.filhos).trim() === "Editar configuração";
  })[0].props.onClick();
  await painel.assentar();
  let m = montar();
  check("re-selar: o editor monta a seccao do ensaio com o botao «Re-selar braços»",
        m.raiz.tag === "PaginaConfig" && !!m.sec && !!botaoReSelar(m.sec), m.raiz.tag);
  check("re-selar: sem a confirmacao marcada o botao esta preso",
        botaoReSelar(m.sec).props.disabled === true);

  // Escrever um modelo que o catalogo nao tem no braco B e marcar a confirmacao.
  procurarNaArvore(m.sec, function (n) {
    return n.tag === "input" && n.props.placeholder === "braço b (gratuito)";
  }).props.onChange({ target: { value: "modelo-fantasma" } });
  procurarNaArvore(m.sec, function (n) {
    return n.tag === "input" && n.props.type === "checkbox";
  }).props.onChange({ target: { checked: true } });
  m = montar();
  check("re-selar: com dois modelos diferentes e a confirmacao, o botao fica livre",
        botaoReSelar(m.sec).props.disabled === false);

  // O clique. O servidor recusa: o modelo nao esta no catalogo.
  let antes = painel.pedidos.length;
  botaoReSelar(m.sec).props.onClick();
  await painel.assentar();
  m = montar();
  let novos = painel.pedidos.slice(antes);
  check("re-selar: sai UM POST para /sombra/bracos com bracos, providers, confirmacao e rev",
        novos.filter(function (p) { return p.metodo === "POST"; }).length === 1
          && JSON.stringify(posts().pop()) === JSON.stringify({
            url: "/sombra/bracos", metodo: "POST",
            corpo: { braco_a: "modelo-a", braco_b: "modelo-fantasma",
                     braco_a_provider: "prov-a", braco_b_provider: "prov-b",
                     confirmar_reabertura: true, rev: "rev-1" } }),
        JSON.stringify(posts().pop()));
  let texto = textoPlano(m.sec);
  check("re-selar recusado: o bloco dos bracos mostra o motivo que o servidor deu",
        texto.indexOf("modelo-fantasma não está no catálogo de nenhum perfil") !== -1,
        texto.slice(-160));
  check("re-selar recusado: o ecra nao mostra um erro interno do painel",
        !/is not defined/.test(texto + " " + String(m.raiz.props.erro || "")),
        texto.slice(-160));
  check("re-selar recusado: o painel rele o estado do servidor",
        novos.some(function (p) { return p.metodo === "GET" && p.url === "/estado"; }),
        JSON.stringify(novos.map(function (p) { return p.metodo + " " + p.url; })));
  check("re-selar recusado: o «a gravar» e libertado e o botao volta a estar livre",
        m.raiz.props.aGravar === false && botaoReSelar(m.sec).props.disabled === false,
        JSON.stringify([m.raiz.props.aGravar, botaoReSelar(m.sec).props.disabled]));
  check("re-selar recusado: nao fica aviso de sucesso e os bracos do servidor nao mudaram",
        !m.raiz.props.avisoSombra && servidor.bracos.b === "modelo-b" && servidor.selo === "selo-antigo",
        JSON.stringify([m.raiz.props.avisoSombra, servidor.bracos]));

  // Conflito de versao: o SDK entrega uma frase generica, o painel diz a sua.
  servidor.recusar = { status: 409, mensagem: "That change conflicts with the current state on the server." };
  botaoReSelar(m.sec).props.onClick();
  await painel.assentar();
  m = montar();
  texto = textoPlano(m.sec);
  check("re-selar em conflito: o bloco diz que o ensaio mudou e manda repetir",
        texto.indexOf("O ensaio mudou noutro sítio — recarreguei o estado. Repita a re-selagem.") !== -1,
        texto.slice(-160));
  check("re-selar em conflito: o «a gravar» tambem e libertado",
        m.raiz.props.aGravar === false, String(m.raiz.props.aGravar));

  // O servidor passa a aceitar: o gesto seguinte grava e limpa o erro anterior.
  servidor.recusar = null;
  antes = posts().length;
  botaoReSelar(m.sec).props.onClick();
  await painel.assentar();
  m = montar();
  check("re-selar aceite: sai o POST e o servidor fica com os bracos pedidos",
        posts().length === antes + 1 && servidor.bracos.b === "modelo-fantasma"
          && servidor.selo === "selo-novo", JSON.stringify(servidor.bracos));
  check("re-selar aceite: o aviso traz o inicio do sha256 do selo novo",
        m.raiz.props.avisoSombra === "braços re-selado abcdef012345…",
        String(m.raiz.props.avisoSombra));
  check("re-selar aceite: o erro da tentativa anterior desaparece e nada fica a gravar",
        textoPlano(m.sec).indexOf("O ensaio mudou noutro sítio") === -1
          && m.raiz.props.aGravar === false && m.raiz.props.estado.sombra.selo === "selo-novo",
        textoPlano(m.sec).slice(-160));
}

// ------------------------------- a listagem de modelos pela `ordem` declarada
// Regra do dono (3.47.23): em cada perfil a tabela de modelos sai ordenada pelo
// campo `ordem` — o menor primeiro, quem nao declara ordem no fim — e uma ordem
// alterada ve-se logo a seguir a gravar, sem recarregar a pagina. A posicao no
// ficheiro so desempata; o ficheiro nao e reordenado.
function idsDasLinhas(arvoreCartao) {
  // As linhas do corpo levam `key` (o id da rota); a do cabecalho nao.
  return comTag(arvoreCartao, "tr")
    .filter(function (tr) { return tr.props && tr.props.key != null; })
    .map(function (tr) { return tr.props.key; });
}

(function () {
  const ordenar = T.ordenarCatalogo;
  function ids(lista) {
    if (typeof ordenar !== "function") return "ordenarCatalogo nao existe";
    try { return ordenar(lista).map(function (e) { return e.id; }).join(","); }
    catch (e) { return "rebentou: " + (e && e.message); }
  }
  check("ordem: o menor primeiro e quem nao declara ordem no fim",
        ids([{ id: "c", ordem: 3 }, { id: "x" }, { id: "a", ordem: 1 }, { id: "b", ordem: 2 }]) === "a,b,c,x",
        ids([{ id: "c", ordem: 3 }, { id: "x" }, { id: "a", ordem: 1 }, { id: "b", ordem: 2 }]));
  check("ordem: e numerica, nao alfabetica (10 depois de 9)",
        ids([{ id: "dez", ordem: 10 }, { id: "nove", ordem: 9 }, { id: "dois", ordem: 2 }]) === "dois,nove,dez",
        ids([{ id: "dez", ordem: 10 }, { id: "nove", ordem: 9 }, { id: "dois", ordem: 2 }]));
  check("ordem: 0 e uma ordem declarada e vem antes do 1",
        ids([{ id: "um", ordem: 1 }, { id: "sem" }, { id: "zero", ordem: 0 }]) === "zero,um,sem",
        ids([{ id: "um", ordem: 1 }, { id: "sem" }, { id: "zero", ordem: 0 }]));
  check("ordem: empate mantem a posicao do ficheiro",
        ids([{ id: "p", ordem: 2 }, { id: "q", ordem: 1 }, { id: "r", ordem: 2 }, { id: "s", ordem: 1 }]) === "q,s,p,r",
        ids([{ id: "p", ordem: 2 }, { id: "q", ordem: 1 }, { id: "r", ordem: 2 }, { id: "s", ordem: 1 }]));
  check("ordem: os que nao declaram ficam entre si pela posicao do ficheiro",
        ids([{ id: "z" }, { id: "m", ordem: 1 }, { id: "a" }, { id: "k", ordem: null }]) === "m,z,a,k",
        ids([{ id: "z" }, { id: "m", ordem: 1 }, { id: "a" }, { id: "k", ordem: null }]));
  check("ordem: um inteiro escrito como texto conta como numero",
        ids([{ id: "t9", ordem: "9" }, { id: "t10", ordem: " 10 " }, { id: "n2", ordem: 2 }]) === "n2,t9,t10",
        ids([{ id: "t9", ordem: "9" }, { id: "t10", ordem: " 10 " }, { id: "n2", ordem: 2 }]));
  // O que o `gestor` nao le como ordem (`int()` falha) tambem nao ordena aqui.
  check("ordem: valor que nao e inteiro conta como nao declarado",
        ids([{ id: "txt", ordem: "abc" }, { id: "vazio", ordem: "" }, { id: "meio", ordem: 1.5 },
             { id: "bool", ordem: true }, { id: "nan", ordem: NaN }, { id: "ok", ordem: 7 }])
          === "ok,txt,vazio,meio,bool,nan",
        ids([{ id: "txt", ordem: "abc" }, { id: "vazio", ordem: "" }, { id: "meio", ordem: 1.5 },
             { id: "bool", ordem: true }, { id: "nan", ordem: NaN }, { id: "ok", ordem: 7 }]));
  check("ordem: lista ausente ou que nao e lista da lista vazia, sem rebentar",
        ids(null) === "" && ids(undefined) === "" && ids("abc") === "" && ids({}) === "",
        [ids(null), ids(undefined), ids("abc"), ids({})].join("|"));
  check("ordem: uma entrada que nao e objecto nao chega a tabela",
        ids([null, { id: "b", ordem: 2 }, "lixo", { id: "a", ordem: 1 }]) === "a,b",
        ids([null, { id: "b", ordem: 2 }, "lixo", { id: "a", ordem: 1 }]));
  const original = [{ id: "c", ordem: 3 }, { id: "a", ordem: 1 }, { id: "b", ordem: 2 }];
  const copia = JSON.stringify(original);
  ids(original);
  check("ordem: ordenar nao mexe na lista que recebeu",
        JSON.stringify(original) === copia, JSON.stringify(original));

  // O cartao: a tabela sai pela ordem declarada, com o catalogo do `/estado`
  // na ordem do ficheiro (forma medida a 2026-11-19 no perfil `pessoal`).
  const catFicheiro = [
    { id: "deepseek-flash", provider: "p", modelo: "m1", complexidade: ["leve"], tarefas: [], acesso: "api", ordem: 4 },
    { id: "north-code-free", provider: "p", modelo: "m2", complexidade: ["leve"], tarefas: [], acesso: "api", ordem: 3 },
    { id: "claude-fable-5-2", provider: "p", modelo: "m3", complexidade: ["alta"], tarefas: [], acesso: "api", ordem: 1 },
    { id: "gpt-6.2-astra", provider: "p", modelo: "m4", complexidade: ["alta"], tarefas: [], acesso: "api", ordem: 2 },
    { id: "gpt-6.1-terra", provider: "p", modelo: "m5", complexidade: ["media"], tarefas: [], acesso: "api", ordem: null },
  ];
  const cartao = tentar("ordem: o cartao com ordens declaradas nao rebenta", function () {
    return T.CartaoPerfil({ nome: "pessoal", mais: {}, aGravar: false,
                            dados: { activo: true, catalogo: catFicheiro, tarefas: {}, estrategia: {}, analise: {} },
                            onToggle: function () {}, onModelo: function () {}, onPerfil: function () {},
                            onNovoModelo: function () {} });
  });
  check("ordem: a tabela do cartao sai pela ordem declarada, sem ordem no fim",
        idsDasLinhas(cartao).join(",")
          === "claude-fable-5-2,gpt-6.2-astra,north-code-free,deepseek-flash,gpt-6.1-terra",
        idsDasLinhas(cartao).join(","));
  check("ordem: o cartao desenha as cinco rotas, nenhuma a mais nem a menos",
        idsDasLinhas(cartao).length === 5, String(idsDasLinhas(cartao).length));
  check("ordem: a linha diz a ordem de quem a declara e cala a de quem nao declara",
        (function () {
          const linhas = comTag(cartao, "tr").filter(function (tr) { return tr.props.key != null; });
          const txt = {};
          linhas.forEach(function (tr) { txt[tr.props.key] = textoPlano(tr); });
          return /ordem 1(?!\d)/.test(txt["claude-fable-5-2"] || "")
            && /ordem 4(?!\d)/.test(txt["deepseek-flash"] || "")
            && !/ordem/.test(txt["gpt-6.1-terra"] || "x ordem");
        })());
  check("ordem: o cartao nao reordena o catalogo que recebeu do estado",
        catFicheiro.map(function (e) { return e.id; }).join(",")
          === "deepseek-flash,north-code-free,claude-fable-5-2,gpt-6.2-astra,gpt-6.1-terra");
})();

async function provaDaOrdemDepoisDeGravar() {
  // Servidor falso com estado, como o verdadeiro: gravar substitui a rota NO
  // LUGAR (a posicao no ficheiro nao muda) e o `/estado` seguinte ja traz a
  // ordem nova. O painel so mostra a ordem certa se reler e reordenar.
  const servidor = {
    rev: 1,
    catalogo: [
      { id: "a", provider: "p", modelo: "ma", complexidade: ["leve"], tarefas: [], acesso: "api", ordem: 3 },
      { id: "b", provider: "p", modelo: "mb", complexidade: ["leve"], tarefas: [], acesso: "api", ordem: 2 },
      { id: "c", provider: "p", modelo: "mc", complexidade: ["leve"], tarefas: [], acesso: "api" },
      { id: "d", provider: "p", modelo: "md", complexidade: ["leve"], tarefas: [], acesso: "api", ordem: 4 },
    ],
  };
  function responder(p) {
    if (p.url === "/estado") {
      return { rev: "rev-" + servidor.rev, idioma: "pt", activo: true,
               sombra: { activo: false, perfis: {} },
               perfis: { pessoal: { activo: true, tarefas: {}, estrategia: {}, analise: {},
                                    // So os campos que o `/estado` verdadeiro manda.
                                    catalogo: servidor.catalogo.map(function (e) {
                                      return { id: e.id, provider: e.provider, modelo: e.modelo,
                                               complexidade: e.complexidade, tarefas: e.tarefas,
                                               acesso: e.acesso, ordem: e.ordem == null ? null : e.ordem };
                                    }) } },
               opcoes: {}, erros_config: [] };
    }
    if (p.url.indexOf("/log") === 0) return { linhas: [] };
    if (p.url === "/config") return { config: { perfis: {} }, rev: "rev-" + servidor.rev };
    if (p.url.indexOf("/modelo?") === 0 && p.metodo === "GET") {
      const id = decodeURIComponent((p.url.split("&id=")[1] || ""));
      const entrada = servidor.catalogo.filter(function (e) { return e.id === id; })[0];
      return { perfil: "pessoal", id: id, novo: false, entrada: clonar(entrada),
               opcoes: { niveis: ["leve", "media", "alta"], tarefas: [], modalidades: [] },
               rev: "rev-" + servidor.rev };
    }
    if (p.url === "/modelo" && p.metodo === "POST") {
      if (p.corpo.validar) return { ok: true, erros: [] };
      const pos = servidor.catalogo.map(function (e) { return e.id; }).indexOf(p.corpo.id);
      servidor.catalogo[pos] = clonar(p.corpo.entrada);
      servidor.rev += 1;
      return { ok: true, id: p.corpo.entrada.id, rev: "rev-" + servidor.rev };
    }
    throw new Error("pedido inesperado: " + p.metodo + " " + p.url);
  }

  const painel = carregarVivo(responder);
  function linhasDoPainel() {
    const raiz = painel.render();
    const no = comTag(raiz, "CartaoPerfil").filter(function (n) { return n.props.nome === "pessoal"; })[0];
    if (!no) return { raiz: raiz, ids: null, cartao: null };
    const cartao = painel.filho("CartaoPerfil", no.props);
    return { raiz: raiz, ids: idsDasLinhas(cartao).join(","), cartao: cartao };
  }
  // O botao do SDK falso pode sair como `button` ou como `Button`.
  function botoes(arvore) { return comTag(arvore, "button").concat(comTag(arvore, "Button")); }
  // O gesto como no ecra: ✎ na linha, escrever no campo «ordem», «Gravar e voltar».
  async function mudarOrdem(id, valor) {
    const antes = linhasDoPainel();
    botoes(antes.cartao).filter(function (b) {
      return b.props.title === "Editar as opções de " + id;
    })[0].props.onClick();
    await painel.assentar();
    let raiz = painel.render();
    if (raiz.tag !== "PaginaModelo") throw new Error("a pagina do modelo nao abriu: " + raiz.tag);
    let pagina = painel.filho("PaginaModelo", raiz.props);
    comTag(pagina, "EntradaTexto").filter(function (n) { return n.props.rotulo === "ordem"; })[0]
      .props.onChange(valor);
    raiz = painel.render();
    pagina = painel.filho("PaginaModelo", raiz.props);
    const gravar = botoes(pagina).filter(function (b) {
      return /Gravar e voltar/.test(textoPlano(b.filhos));
    })[0];
    if (gravar.props.disabled) throw new Error("o botao de gravar esta preso");
    const marca = painel.pedidos.length;
    gravar.props.onClick();
    await painel.assentar();
    return painel.pedidos.slice(marca).map(function (p) {
      return p.metodo + " " + p.url + (p.corpo && p.corpo.validar ? " (validar)" : "");
    });
  }

  painel.render();
  await painel.assentar();
  let l = linhasDoPainel();
  check("ordem/gravar: ao abrir, a tabela sai pela ordem declarada e nao pela do ficheiro",
        l.ids === "b,a,d,c", String(l.ids));

  // `c` nao tinha ordem; passa a ser a primeira.
  let pedidos = await mudarOrdem("c", "1");
  l = linhasDoPainel();
  check("ordem/gravar: o gesto valida, grava e rele o estado, por esta sequencia",
        pedidos.indexOf("POST /modelo (validar)") === 0 && pedidos.indexOf("POST /modelo") === 1
          && pedidos.indexOf("GET /estado") > 1,
        JSON.stringify(pedidos));
  check("ordem/gravar: o servidor ficou com a ordem escrita, na mesma posicao do ficheiro",
        servidor.catalogo.map(function (e) { return e.id + ":" + (e.ordem == null ? "-" : e.ordem); }).join(",")
          === "a:3,b:2,c:1,d:4",
        JSON.stringify(servidor.catalogo.map(function (e) { return [e.id, e.ordem]; })));
  check("ordem/gravar: a pagina fecha e a tabela ja mostra a ordem nova, sem outro gesto",
        l.raiz.tag !== "PaginaModelo" && l.ids === "c,b,a,d", l.raiz.tag + " " + String(l.ids));
  check("ordem/gravar: a linha gravada diz «ordem 1»",
        (function () {
          const tr = comTag(l.cartao, "tr").filter(function (n) { return n.props.key === "c"; })[0];
          return !!tr && /ordem 1(?!\d)/.test(textoPlano(tr));
        })());

  // Apagar a ordem devolve a rota ao fim da lista.
  pedidos = await mudarOrdem("b", "");
  l = linhasDoPainel();
  check("ordem/gravar: apagar a ordem manda a rota para o fim, logo a seguir a gravar",
        l.ids === "c,a,d,b" && !("ordem" in servidor.catalogo[1]),
        String(l.ids) + " " + JSON.stringify(servidor.catalogo[1]));

  // Empate: `d` passa a 1, como `c`. Desempata a posicao no ficheiro (c antes de d).
  pedidos = await mudarOrdem("d", "1");
  l = linhasDoPainel();
  check("ordem/gravar: duas rotas com a mesma ordem ficam pela posicao do ficheiro",
        l.ids === "c,d,a,b", String(l.ids));
  check("ordem/gravar: nenhum erro ficou no ecra depois das tres gravacoes",
        !l.raiz.props || !l.raiz.props.erro, String(l.raiz.props && l.raiz.props.erro));
}

async function provaEstatisticas() {
  const pendentes = [];
  const painel = carregarVivo(p => {
    if (p.url === '/estatisticas' && p.metodo === 'GET')
      return new Promise((resolve, reject) => pendentes.push({ resolve, reject }));
    if (p.url === '/estado') return { idioma: 'pt', perfis: {} };
    if (p.url.startsWith('/log?')) return { linhas: [{ modelo: 'modelo-log' }] };
    if (p.url === '/config') return { config: {} };
    throw Error(p.url);
  });
  painel.render();
  await painel.assentar();
  let pagina = painel.render();
  const registo = recolher(pagina, n => n.tag === 'TabelaLog')[0];
  check('estatísticas: o registo chega com o GET das estatísticas ainda pendente',
        pendentes.length === 1 && registo && registo.props.linhas[0].modelo === 'modelo-log');
  if (!pendentes.length) return;
  const texto = () => textoPlano(painel.render());
  check('estatísticas: carregamento explícito', texto().includes('A carregar estatísticas…'));
  pendentes[0].reject(Error('404'));
  await painel.assentar();
  check('estatísticas: indisponível não é zero nem esconde o registo',
        texto().includes('Estatísticas indisponíveis.') && !texto().includes('Sem respostas') && texto().includes('Registo de utilizacao'));
  const actualizar = () => recolher(painel.render(), n => n.tag === 'button' && textoPlano(n) === 'Actualizar')[0].props.onClick();
  actualizar(); await painel.assentar();
  check('estatísticas: Actualizar repete o GET', pendentes.length === 2);
  pendentes[1].resolve({ total: 0, inicio: null, fim: null, modelos: {}, perfis: {}, tarefas: {}, superficies: {}, origens: {} });
  await painel.assentar();
  check('estatísticas: zero confirmado, sem gráfico ou percentagem fictícia',
        texto().includes('Sem respostas no detalhe retido.') && !texto().includes('NaN') && !recolher(painel.render(), n => n.tag === 'figure').length);
  actualizar();
  await painel.assentar();
  actualizar(); await painel.assentar();
  const dados = { total: 4, inicio: '2025-01-01', fim: '2026-02-02',
    modelos: { 'modelo-A': 3, '': 1 }, perfis: { pessoal: 4 }, tarefas: { editar: 4 },
    superficies: { web: 4 }, origens: { fixa: 4 } };
  pendentes[3].resolve(dados);
  await painel.assentar();
  pendentes[2].reject(Error('pedido antigo'));
  await painel.assentar();
  pagina = painel.render();
  const figuras = recolher(pagina, n => n.tag === 'figure');
  const grupos = [['Modelos', 'modelo-A', 3, '75'], ['Perfis', 'pessoal', 4, '100'],
    ['Tarefas', 'editar', 4, '100'], ['Proveniência', 'web', 4, '100'], ['Origens de decisão', 'fixa', 4, '100']];
  for (const [titulo, nome, n, pct] of grupos) {
    const figura = figuras.find(f => textoPlano(f).includes(titulo));
    check('estatísticas: gráfico e legenda de ' + titulo, figura &&
      recolher(figura, e => e.props && e.props.style && /conic-gradient/.test(e.props.style.backgroundImage)).length === 1 &&
      recolher(figura, e => e.tag === 'li').some(e => textoPlano(e).includes(nome) &&
        new RegExp('\\b' + n + '\\b').test(textoPlano(e)) && new RegExp(pct + '(?:,0)?\\s*%').test(textoPlano(e))));
  }
  const mapa = Object.fromEntries(Array.from({length: 15}, (_, i) => ['m' + i, i + 1]));
  const uiFiltros = carregar();
  for (const [modelos, limiar, esperados] of [['todos', 99, 15], ['top5', 99, 5], ['acima', 12, 3], ['acima', 15, 0]]) {
    const cartao = uiFiltros.cartaoEstatisticas({ fase: 'pronto', dados: { ...dados, total: 120, modelos: mapa, perfis: mapa } }, ['modelos','perfis'], { modelos, limiar });
    const [m, p] = recolher(cartao, n => n.tag === 'figure');
    const linhas = recolher(m, n => n.tag === 'li');
    check('filtros: ' + modelos + '/' + limiar + ' só limita modelos, ordem e percentagens do total', linhas.length === esperados &&
      (!esperados || /m14.*12[.,]5/.test(textoPlano(linhas[0]))) && recolher(p, n => n.tag === 'li').length === 15);
  }
  for (const [selecao, titulos] of [[['perfis', 'origens'], ['Perfis', 'Origens de decisão']], [[], []]]) {
    const ui = carregar(); ui.idioma.setIdioma('pt');
    const cartao = ui.cartaoEstatisticas({ fase: 'pronto', dados }, selecao);
    check('estatísticas: só desenha os gráficos seleccionados ' + selecao,
      JSON.stringify(recolher(cartao, n => n.tag === 'figcaption').map(textoPlano)) === JSON.stringify(titulos));
  }
  check('estatísticas: a chave vazia é contada', /Não registado.*1.*25/.test(texto()));
  check('estatísticas: total, período e âmbito', /Total de respostas.*4/.test(texto()) &&
    /01\/01\/2025.*02\/02\/2026/.test(texto()) && /detalhe retido.*todos os perfis/i.test(texto()));
  check('estatísticas: os cartões partilham uma grelha, registo primeiro', recolher(pagina,
    e => e.props && e.props.className === 'vmm-utilizacao' && e.filhos.length === 2 &&
      textoPlano(e.filhos[0]).includes('Registo de utilizacao') && textoPlano(e.filhos[1]).includes('Estatísticas de utilização')).length === 1);
  const en = carregar();
  en.idioma.setIdioma('en');
  check('estatísticas: interface inglesa e identificadores intactos', typeof en.cartaoEstatisticas === 'function' && (() => {
    const text = textoPlano(en.cartaoEstatisticas({ fase: 'pronto', dados: { ...dados, modelos: { Tarefas: 3, '': 1 } } }));
    return ['Usage statistics', 'Models', 'Profiles', 'Tasks', 'Provenance', 'Decision origins',
      'Tarefas', 'Not recorded', 'Total responses', 'Period', 'retained detail', 'all profiles'].every(s => text.includes(s));
  })());
  actualizar(); await painel.assentar(); pendentes[4].resolve({}); await painel.assentar();
  check('estatísticas: payload incompleto não simula ausência de respostas', texto().includes('Estatísticas indisponíveis.'));
}

async function provaEscolhaGraficos() {
  const ids = ['modelos', 'perfis', 'tarefas', 'superficies', 'origens'];
  let cfg = { perfis: {}, idioma: 'pt' };
  const painel = carregarVivo(p => {
    if (p.url === '/estado') return { perfis: {}, idioma: 'pt', painel_estatisticas: true, painel_graficos: cfg.painel_graficos || ids };
    if (p.url === '/config') {
      if (p.metodo === 'POST') cfg = p.corpo.config;
      return { config: cfg, rev: 'r' };
    }
    if (p.url === '/estatisticas') return Object.assign({ total: 1, inicio: null, fim: null }, Object.fromEntries(ids.map(k => [k, { a: 1 }])));
    return {};
  });
  const config = () => painel.filho('PaginaConfig', painel.render().props);
  const caixas = () => recolher(config(), n => n.tag === 'input' && n.props.type === 'checkbox' && ids.includes(n.props.value));
  painel.render(); await painel.assentar();
  recolher(painel.render(), n => n.tag === 'button' && textoPlano(n) === 'Editar configuração')[0].props.onClick(); await painel.assentar();
  check('escolha global: cinco opções inicialmente activas', caixas().length === 5 && caixas().every(n => n.props.checked));
  for (const id of ids) caixas().find(n => n.props.value === id)?.props.onChange();
  check('escolha global: vazio é um rascunho, nenhum POST', JSON.stringify(JSON.parse(painel.render().props.configJSON).painel_graficos) === '[]' && !painel.pedidos.some(p => p.metodo === 'POST'));
  painel.render().props.onGravar(); await painel.assentar();
  check('escolha global: Gravar e voltar persiste vazio e remove as figuras', JSON.stringify(cfg.painel_graficos) === '[]' && recolher(painel.render(), n => n.tag === 'figure').length === 0);
}

async function provaInterruptorEstatisticas() {
  for (const [idioma, inicial] of [['pt', false], ['en', true], ['pt', undefined], ['en', 'false']]) {
    let activo = inicial, rev = 'abertura';
    const painel = carregarVivo(p => {
      if (p.url === '/estado') return { idioma, rev, perfis: {}, painel_estatisticas: activo };
      if (p.url.startsWith('/log?')) return { linhas: [] };
      if (p.url === '/config') return { config: {}, rev };
      if (p.url === '/estatisticas') return {};
      if (p.url === '/painel-estatisticas' && p.metodo === 'POST') {
        if (p.corpo.rev !== rev) throw Object.assign(Error('conflict'), { status: 409 });
        activo = p.corpo.activo; rev = 'gravada'; return { rev, painel_estatisticas: activo };
      }
      throw Error(p.url);
    });
    const pedidosStats = () => painel.pedidos.filter(p => p.url === '/estatisticas').length;
    const titulo = idioma === 'pt' ? 'Estatísticas de utilização' : 'Usage statistics';
    const abre = idioma === 'pt' ? 'Editar configuração' : 'Edit configuration';
    const botao = (arvore, texto) => recolher(arvore, n => n.tag === 'button' && textoPlano(n) === texto)[0];
    painel.render();
    check('estatísticas: aguarda /estado antes do GET', pedidosStats() === 0);
    await painel.assentar();
    const actualizar = recolher(painel.render(), n => n.tag === 'button' && /^(Actualizar|Refresh)$/.test(textoPlano(n)))[0];
    actualizar.props.onClick(); await painel.assentar();
    check('estatísticas: montagem e actualização respeitam ' + inicial, pedidosStats() === (activo === false ? 0 : 2) &&
      textoPlano(painel.render()).includes(titulo) === (activo !== false) && recolher(painel.render(), n => n.tag === 'TabelaLog').length === 1);
    if (activo === false) {
      const grelha = recolher(painel.render(), n => n.props && n.props.className === 'vmm-utilizacao')[0];
      const visiveis = grelha.filhos.filter(Boolean);
      check('desligado: o registo é o único cartão e ocupa todas as colunas', visiveis.length === 1 &&
        recolher(visiveis[0], n => n.tag === 'TabelaLog').length === 1 &&
        /\.vmm-utilizacao\s*>\s*:only-child\s*\{\s*grid-column:\s*1\s*\/\s*-1/.test(fs.readFileSync(path.join(__dirname, 'dist/style.css'), 'utf8')));
    }
    botao(painel.render(), abre).props.onClick(); await painel.assentar();
    const config = () => painel.filho('PaginaConfig', painel.render().props);
    const switchNo = () => recolher(config(), n => n.props && n.props.role === 'switch' && n.props['aria-label'] === titulo)[0];
    const suporta = typeof inicial === 'boolean';
    check('configuração: só oferece interruptor com booleano: ' + inicial, !!switchNo() === suporta);
    if (!suporta || !switchNo()) continue;
    check('configuração: ajuda na língua activa', switchNo().props.title === (idioma === 'pt' ? 'Grava de imediato' : 'Saves immediately'));
    painel.render().props.onMudarJSON('{"rascunho":42}');
    check('configuração: estado e acção ' + idioma, switchNo().props['aria-checked'] === inicial && textoPlano(switchNo()) ===
      (idioma === 'pt' ? 'Desligado — ligar' : 'On — turn off'));
    switchNo().props.onClick(); await painel.assentar();
    const corpo = painel.pedidos.filter(p => p.url === '/painel-estatisticas')[0].corpo;
    check('configuração: POST global com revisão da página', Object.keys(corpo).sort().join(',') === 'activo,rev' && corpo.rev === 'abertura' && corpo.activo === !inicial);
    const cfg = JSON.parse(painel.render().props.configJSON);
    check('configuração: conserva rascunho e acompanha o valor gravado', cfg.rascunho === 42 && cfg.painel_estatisticas === !inicial && switchNo().props['aria-checked'] === !inicial);
    rev = 'externa';
    for (let i = 0; i < 2; i++) {
      switchNo().props.onClick(); await painel.assentar();
      check('configuração: após 409 não adopta a revisão alheia', painel.pedidos.filter(p => p.url === '/painel-estatisticas').at(-1).corpo.rev === 'gravada' &&
        textoPlano(config()).includes(idioma === 'pt' ? 'Descartar e abrir' : 'Discard and reopen'));
    }
  }
}

async function provaFiltros() {
  const inicial = { periodo: 'sempre', modelos: 'todos', limiar: 0 };
  let filtros = inicial, rev = '1';
  const posts = [], gets = [];
  const painel = carregarVivo(p => {
    if (p.url === '/estado') return { idioma: 'pt', perfis: {}, painel_estatisticas: true, painel_filtros: filtros, rev };
    if (p.url === '/config') return { config: { painel_filtros: filtros }, rev };
    if (p.url.startsWith('/estatisticas')) return new Promise(resolve => gets.push({ url: p.url, resolve }));
    if (p.url === '/painel-filtros') return new Promise((resolve, reject) => posts.push({ p, reject, aceitar() {
      filtros = p.corpo.painel_filtros; rev += 'x'; resolve({ ok: true, rev, painel_filtros: filtros });
    } }));
    return {};
  });
  const dados = nome => ({ total: 1, inicio: null, fim: null, ...Object.fromEntries(['modelos','perfis','tarefas','superficies','origens'].map(k => [k, { [nome]: 1 }])) });
  const config = () => painel.filho('PaginaConfig', painel.render().props);
  const campo = k => recolher(config(), n => n.props && n.props.name === k)[0];
  const mudar = (k, valor) => campo(k).props.onChange({ target: { value: valor } });
  painel.render(); await painel.assentar();
  recolher(painel.render(), n => n.tag === 'button' && textoPlano(n) === 'Editar configuração')[0].props.onClick();
  await painel.assentar();
  check('filtros: apresenta controlos globais na configuração', !!campo('periodo') && !!campo('modelos'));
  if (!campo('periodo')) return;
  mudar('periodo', 'semana'); mudar('modelos', 'acima'); mudar('periodo', 'mes');
  painel.render().props.onMudarJSON('{"rascunho":"em voo"}');
  check('filtros: primeiro POST imediato e único em voo', posts.length === 1 && posts[0].p.metodo === 'POST' && JSON.stringify(posts[0].p.corpo) === JSON.stringify({ rev: '1', painel_filtros: { ...inicial, periodo: 'semana' } }));
  posts[0].aceitar(); await painel.assentar();
  check('filtros: agrega escolhas em voo e usa a revisão confirmada', posts.length === 2 && posts[1].p.corpo.rev === '1x' && posts[1].p.corpo.painel_filtros.periodo === 'mes' && posts[1].p.corpo.painel_filtros.modelos === 'acima');
  posts[1].aceitar(); await painel.assentar();
  const cfg = JSON.parse(painel.render().props.configJSON);
  check('filtros: funde revisão sem perder edições feitas durante o POST', cfg.rascunho === 'em voo' && cfg.painel_filtros.periodo === 'mes');
  check('filtros: períodos da API', gets.some(x => x.url.endsWith('?periodo=semana')) && gets.at(-1).url.endsWith('?periodo=mes'));
  gets.at(-1).resolve(dados('mensal')); gets[0].resolve(dados('obsoleto')); await painel.assentar();
  check('filtros: pré-visualização não aceita um GET antigo', textoPlano(config()).includes('mensal') && !textoPlano(config()).includes('obsoleto'));
  for (const mau of ['', '1.5', '-1', '2x', '1e2', '9007199254740992']) mudar('limiar', mau);
  check('filtros: limiar inválido não é arredondado nem enviado', posts.length === 2);
  mudar('limiar', '1'); posts[2].aceitar(); await painel.assentar();
  check('filtros: limiar imediato estrito e sem novo GET', posts[2].p.corpo.painel_filtros.limiar === 1 && textoPlano(config()).includes('Nenhum modelo acima do limiar.') && gets.length === 3);
  mudar('modelos', 'top5'); posts[3].reject(Object.assign(Error('conflito'), { status: 409 })); await painel.assentar();
  check('filtros: conflito mantém rascunho e repõe preferência confirmada', campo('modelos').props.value === 'acima' && JSON.parse(painel.render().props.configJSON).rascunho === 'em voo' && textoPlano(config()).includes('Descartar e abrir'));
  painel.render().props.onMudarJSON('{"incompleto":');
  mudar('limiar', '0'); posts[4].aceitar(); await painel.assentar();
  check('filtros: zero aceite e JSON incompleto preservado literalmente', posts[4].p.corpo.painel_filtros.limiar === 0 && painel.render().props.configJSON === '{"incompleto":');
  painel.render().props.onMudarJSON('{"incompleto":42}');
  const reparado = JSON.parse(painel.render().props.configJSON);
  check('filtros: ao reparar o JSON não se perde o filtro já gravado', reparado.incompleto === 42 && reparado.painel_filtros?.limiar === 0);
  painel.render().props.onDescartar(); await painel.assentar();
  check('filtros: painel e pré-visualização aplicam o mesmo filtro', !textoPlano(painel.render()).includes('Nenhum modelo acima do limiar.') && textoPlano(painel.render()).includes('mensal'));
}

function fechar() {
  console.log("");
  if (falhas) {
    console.log(falhas + " de " + total + " verificacoes FALHARAM");
    process.exit(1);
  }
  console.log(total + "/" + total + " verificacoes OK");
}

provaDoGestoPorPerfil()
  .catch(function (e) {
    check("gesto: a prova de ponta a ponta correu ate ao fim", false, String(e && e.stack ? e.stack : e));
  })
  .then(provaDoReSelarRecusado)
  .catch(function (e) {
    check("re-selar: a prova de ponta a ponta correu ate ao fim", false, String(e && e.stack ? e.stack : e));
  })
  .then(provaDaOrdemDepoisDeGravar)
  .catch(function (e) {
    check("ordem/gravar: a prova de ponta a ponta correu ate ao fim", false, String(e && e.stack ? e.stack : e));
  })
  .then(provaEstatisticas)
  .catch(e => check('estatísticas: prova completa', false, e.stack))
  .then(provaEscolhaGraficos)
  .catch(e => check('escolha global: prova completa', false, e.stack))
  .then(provaInterruptorEstatisticas)
  .catch(e => check('interruptor: prova completa', false, e.stack))
  .then(provaFiltros)
  .catch(e => check('filtros: prova completa', false, e.stack))
  .then(fechar);
