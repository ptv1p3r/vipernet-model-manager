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
  const React = {
    createElement: function () { return null; },
    useState: function (v) { return [v, function () {}]; },
    useEffect: function () {},
    useCallback: function (f) { return f; },
  };
  const janela = {
    __HERMES_PLUGIN_SDK__: { React: React, hooks: {}, components: {} },
    __HERMES_PLUGINS__: { register: function () {} },
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
  return ferramentas;
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
      /falhou no último turno/.test(rFalhou.alerta) && /length 320tok/.test(rFalhou.alerta), rFalhou.alerta);

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

console.log("");
if (falhas) {
  console.log(falhas + " de " + total + " verificacoes FALHARAM");
  process.exit(1);
}
console.log(total + "/" + total + " verificacoes OK");
