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

console.log("");
if (falhas) {
  console.log(falhas + " de " + total + " verificacoes FALHARAM");
  process.exit(1);
}
console.log(total + "/" + total + " verificacoes OK");
