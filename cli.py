#!/usr/bin/env python3
"""vmm — CLI do vipernet-model-manager.

Uso:
  vmm status [--json]                 estado global e por perfil
  vmm on|off [--perfil NOME]          liga/desliga (sem --perfil: global)
  vmm perfis                          perfis geridos e perfis conhecidos
  vmm perfil add NOME [--copiar-de X]
  vmm perfil rm NOME
  vmm rota list [PERFIL] [--json]
  vmm rota add PERFIL PROVIDER MODELO --complexidade leve,media [--id ID] [--acesso api|subscricao] [--esforco baixo|medio|alto] [--custo N] [--ordem N]
  vmm rota esforco PERFIL ID baixo|medio|alto|-
  vmm rota rm PERFIL ID
  vmm simular PERFIL "texto"          classifica o texto e mostra a rota escolhida
  vmm verificar [PERFIL]              resolve credenciais de cada rota (sem as imprimir)
  vmm log [-n N] [--perfil NOME] [--provider P] [--sessao S] [--evento E]
  vmm caminho                         imprime o caminho do config

Codigos de saida: 0 ok · 1 ha problema · 2 uso invalido · 3 nao encontrado.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

_AQUI = Path(__file__).resolve().parent


def _gestor():
    spec = importlib.util.spec_from_file_location("vmm_gestor", _AQUI / "gestor.py")
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _analise():
    spec = importlib.util.spec_from_file_location("vmm_analise", _AQUI / "analise.py")
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _analise_modelo():
    """O classificador por modelo — outra casa, outro modulo.

    `_analise()` e a heuristica (`analise.py`) e nao tem `normalizar` nem
    `classificar_lote`; confundir os dois da `AttributeError` em vez de um
    resultado errado, mas o comando falha na mesma.
    """
    spec = importlib.util.spec_from_file_location("vmm_analise_modelo",
                                                  _AQUI / "analise_modelo.py")
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


g = _gestor()


def _retencao():
    """Compactacao do ledger e poda dos backups — outro modulo, outra casa."""
    spec = importlib.util.spec_from_file_location("vmm_retencao", _AQUI / "retencao.py")
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _veredicto():
    """Veredicto de entrega, legivel por maquina — modulo proprio."""
    spec = importlib.util.spec_from_file_location("vmm_veredicto", _AQUI / "veredicto.py")
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _cfg() -> tuple[dict[str, Any], list[str]]:
    return g.carregar()


def _gravar_cas(cfg: dict[str, Any], rev_lido: str) -> None:
    """Grava so se a config no disco continuar na versao que este comando leu.

    O CLI le, muda em memoria e grava o documento inteiro: se outra sessao
    escreveu nesse intervalo, gravar reporia o passado e a escolha dela
    desaparecia sem erro nenhum. Com `rev_esperado`, o `gestor` compara dentro
    da trava e levanta `Conflito` em vez de ganhar.
    """
    try:
        g.gravar(cfg, rev_esperado=rev_lido or None)
    except g.Conflito as exc:
        # Nao e falha de gravacao: e a outra escrita a ser respeitada. Dizer
        # "recusada" com a versao actual e o que evita repetir as cegas.
        raise ValueError(f"a config mudou no disco (rev {exc.rev_actual}); "
                         f"releia com `vmm status` e repita o comando") from exc


def _erro(msg: str, codigo: int = 1) -> int:
    print(f"vmm: {msg}", file=sys.stderr)
    return codigo


def _tabela(linhas: list[list[str]]) -> str:
    if not linhas:
        return ""
    larguras = [max(len(str(l[i])) for l in linhas) for i in range(len(linhas[0]))]
    return "\n".join("  ".join(str(c).ljust(larguras[i]) for i, c in enumerate(l)).rstrip()
                     for l in linhas)


# --------------------------------------------------------------------------- comandos


def cmd_status(args) -> int:
    cfg, erros = _cfg()
    for erro in erros:
        print(f"aviso: {erro}", file=sys.stderr)
    perfis = cfg.get("perfis") or {}
    if args.json:
        print(json.dumps({"config": str(g.ficheiro_config()), "activo": cfg.get("activo", True),
                          "perfis": perfis}, ensure_ascii=False, indent=2))
        return 1 if erros else 0
    print(f"config: {g.ficheiro_config()}")
    print(f"global: {'ligado' if cfg.get('activo', True) else 'DESLIGADO'}"
          f" · registar: {'sim' if cfg.get('registar', True) else 'nao'}")
    print(f"perfis geridos: {len(perfis)}")
    est_g = g.estrategia_global(cfg)
    print(f"estrategia global: {est_g['modo']}"
          + (f" por {est_g['por']}" if est_g["modo"] == "rodizio" else "")
          + f" ({est_g['origem']})")
    linhas = [["perfil", "activo", "estrategia", "rotacao", "origem", "rotas", "niveis"]]
    for nome, p in sorted(perfis.items()):
        rotas = p.get("catalogo") or []
        niveis = sorted({n for r in rotas for n in (r.get("complexidade") or [])},
                        key=lambda n: g.NIVEIS.index(n))
        # A estrategia em vigor, nao o delta: um perfil que herda tem de mostrar
        # o que esta a decidir, e a coluna `origem` diz de onde vem.
        est = g.estrategia_efectiva(cfg, nome)
        linhas.append([nome, "sim" if p.get("activo", True) else "NAO",
                       est["modo"], est["por"], est["origem"],
                       str(len(rotas)), ",".join(niveis) or "-"])
    print(_tabela(linhas))
    return 1 if erros else 0


def cmd_on_off(args) -> int:
    cfg, _ = _cfg()
    rev_lido = g.rev(cfg)
    ligar = args.cmd == "on"
    if args.perfil:
        p = (cfg.get("perfis") or {}).get(args.perfil)
        if not isinstance(p, dict):
            return _erro(f"perfil '{args.perfil}' nao esta na config (vmm perfis)", 3)
        p["activo"] = ligar
        alvo = f"perfil {args.perfil}"
    else:
        cfg["activo"] = ligar
        alvo = "global"
    try:
        _gravar_cas(cfg, rev_lido)
    except ValueError as exc:
        return _erro(f"config recusada: {exc}")
    print(f"{alvo}: {'ligado' if ligar else 'desligado'}")
    print("o gateway le a config a quente (cache de 5 s); nao e preciso reiniciar.")
    return 0


def _sombra_mod():
    """Pre-registo, sorteio e sonda de vida — outro modulo, outra casa."""
    spec = importlib.util.spec_from_file_location("vmm_sombra", _AQUI / "sombra.py")
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _chamar_modelo(provider: str, modelo: str) -> dict[str, Any]:
    """Uma chamada minima ao modelo, so para provar que responde.

    `provider` vem declarado no pre-registo. Vazio significa "nao declarado" e
    cai-se na inferencia antiga (`/` ou `:` -> openrouter), que e um palpite:
    fica registado como tal na resposta para a sonda nao se fazer passar por
    medida quando foi adivinhacao.

    Nunca imprime a credencial nem o corpo completo de um erro: o que sai daqui
    vai para o ledger e para a conversa.
    """
    import urllib.error
    import urllib.request

    spec = importlib.util.spec_from_file_location("vmm_seams", _AQUI / "seams.py")
    seams = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(seams)  # type: ignore[union-attr]
    inferido = not str(provider or "").strip()
    if inferido:
        provider = "openrouter" if ("/" in modelo or ":" in modelo) else "deepseek"
    try:
        rota = seams.resolver_provider(provider, modelo)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "http": 0, "provider": provider,
                "provider_inferido": inferido,
                "motivo": f"rota: {type(exc).__name__}: {str(exc)[:110]}"}
    corpo = json.dumps({
        "model": modelo,
        "messages": [{"role": "user", "content": "Responde apenas: OK"}],
        "max_tokens": 8,
    }).encode()
    pedido = urllib.request.Request(
        rota["base_url"].rstrip("/") + "/chat/completions",
        data=corpo,
        headers={"Authorization": "Bearer " + rota["api_key"],
                 "Content-Type": "application/json",
                 "User-Agent": "curl/8.5.0"},
    )
    try:
        with urllib.request.urlopen(pedido, timeout=45) as resposta:
            dados = json.loads(resposta.read().decode())
        ok = bool(dados.get("choices"))
        return {"ok": ok, "http": resposta.status, "provider": provider,
                "provider_inferido": inferido,
                "motivo": "" if ok else "sem choices"}
    except urllib.error.HTTPError as exc:
        texto = exc.read().decode(errors="replace")
        try:
            msg = json.loads(texto).get("error", {}).get("message", texto)
        except (ValueError, AttributeError):
            msg = texto
        return {"ok": False, "http": exc.code, "provider": provider,
                "provider_inferido": inferido, "motivo": str(msg)[:160]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "http": 0, "provider": provider,
                "provider_inferido": inferido,
                "motivo": f"{type(exc).__name__}: {str(exc)[:120]}"}


def cmd_sombra(args) -> int:
    """Estado, sonda de vida e interruptor do ensaio em sombra."""
    acao = getattr(args, "acao", None) or "estado"
    if acao in ("on", "off"):
        cfg, _ = _cfg()
        rev_lido = g.rev(cfg)
        bloco = cfg.get("sombra")
        if not isinstance(bloco, dict):
            bloco = {}
        bloco["activo"] = acao == "on"
        bloco.setdefault("vida_ttl_h", 24)
        cfg["sombra"] = bloco
        try:
            _gravar_cas(cfg, rev_lido)
        except ValueError as exc:
            return _erro(f"config recusada: {exc}")
        print(f"ensaio em sombra: {'ligado' if acao == 'on' else 'desligado'}")
        if acao == "on":
            print("um braco sem vida medido mantem o ensaio parado (vmm sombra vida).")
        return 0

    # Mesmo caminho que o middleware le (`_pre_registo_ensaio`): o desenho vive
    # em `aprendiz/sombra`. Com a CLI a procurar em `sombra/` o desenho selado
    # nunca era encontrado e a sonda nao podia autorizar o ensaio.
    base = Path(g.directorio()) / "aprendiz" / "sombra"
    ficheiros = sorted(base.glob("pre-registo-*.json"))
    if not ficheiros:
        return _erro("sem pre-registo em aprendiz/sombra", 3)
    try:
        registo = json.loads(ficheiros[-1].read_text(encoding="utf-8"))
    except ValueError as exc:
        return _erro(f"pre-registo ilegivel: {exc}")
    sombra = _sombra_mod()
    if not sombra.verificar(registo):
        return _erro("selo do pre-registo nao confere — recusado", 1)

    if acao == "vida":
        veredicto = sombra.sondar_vida(registo, _chamar_modelo)
        destino = base / "vida.json"
        destino.parent.mkdir(parents=True, exist_ok=True)
        temporario = destino.with_suffix(".json.tmp")
        temporario.write_text(json.dumps(veredicto, indent=2, ensure_ascii=False) + "\n",
                              encoding="utf-8")
        temporario.replace(destino)
        for braco in ("a", "b"):
            d = veredicto[f"braco_{braco}"]
            estado = "vivo" if d.get("ok") else "MORTO"
            print(f"braco {braco}: {d.get('modelo')} — {estado} "
                  f"(HTTP {d.get('http')}) {d.get('motivo') or ''}".rstrip())
        print(f"todos vivos: {veredicto['todos_vivos']} | gravado em {destino}")
        return 0 if veredicto["todos_vivos"] else 1

    cfg, _ = _cfg()
    _bloco = cfg.get("sombra")
    bloco = _bloco if isinstance(_bloco, dict) else {}
    print(f"pre-registo: {ficheiros[-1].name} | selo ok | sha {str(registo.get('sha256'))[:16]}")
    print(f"ensaio: {registo.get('ensaio')} | unidade: {registo.get('unidade_de_randomizacao')}")
    print(f"braco a = {registo.get('braco_a')} | braco b = {registo.get('braco_b')}")
    print(f"n por braco = {registo.get('n_por_braco')} | margem = {registo.get('margem_nao_inferioridade')}")
    print(f"interruptor: {'ligado' if bloco.get('activo') else 'desligado'}")
    vida = {}
    caminho_vida = base / "vida.json"
    if caminho_vida.exists():
        try:
            vida = json.loads(caminho_vida.read_text(encoding="utf-8"))
        except ValueError:
            vida = {}
    if vida:
        print(f"vida: {vida.get('ts')} | todos vivos: {vida.get('todos_vivos')}")
        for braco in ("a", "b"):
            d = vida.get(f"braco_{braco}") or {}
            print(f"  braco {braco}: {'vivo' if d.get('ok') else 'MORTO'} "
                  f"(HTTP {d.get('http')}) {str(d.get('motivo') or '')[:90]}")
    else:
        print("vida: nunca medida — o ensaio nao aplica sem ela (vmm sombra vida)")
    return 0


def cmd_classificador(args) -> int:
    """Le e escreve a definicao do classificador por modelo (global e por perfil).

    Sem argumentos mostra o que esta efectivamente em vigor em cada perfil —
    que e a pergunta verdadeira, porque um perfil herda o global e um perfil
    que declare o seu substitui-o por inteiro.
    """
    cfg, erros = _cfg()
    rev_lido = g.rev(cfg)
    for erro in erros:
        print(f"aviso: {erro}", file=sys.stderr)
    perfis = cfg.get("perfis") or {}

    if args.cmd == "ver" or args.cmd is None:
        linhas = [["perfil", "classificador", "provider", "modelo", "via", "esforco/confianca",
                   "origem"]]
        for nome in sorted(perfis):
            # Uma so casa para a pergunta: `classificador_efectivo` resolve a
            # heranca e o "e utilizavel?" — o CLI nao repete essa logica.
            c = g.classificador_efectivo(cfg, nome)
            # Com `via: decisoes` o esforco e o formato nao existem; o que ha e
            # um piso de confianca. Mostrar os dois na mesma coluna evita uma
            # tabela larga cheia de celulas vazias.
            if c["via"] == "decisoes":
                alavanca = f"conf>={c['confianca_min']:.2f}"
            else:
                alavanca = c["esforco"] or "-"
            linhas.append([
                nome,
                "ligado" if c["modo"] == "modelo" else "desligado",
                c["provider"] or "-",
                c["modelo"] or "-",
                c["via"],
                alavanca,
                c["origem"] + (f" ({c['motivo']})" if c["motivo"] else ""),
            ])
        print(_tabela(linhas))
        global_c = (cfg.get("analise") or {}).get("classificador") or {}
        estado = "ligado" if global_c.get("activo") else "desligado"
        print(f"\nglobal: {estado} · {global_c.get('provider') or '-'}"
              f"/{global_c.get('modelo') or '-'}")
        if not (global_c.get("provider") and global_c.get("modelo")):
            print("sem classificador definido: a complexidade e decidida pela heuristica.")
        return 1 if erros else 0

    if args.cmd == "lote":
        # Classificacao em lote: N pedidos num so pedido. Serve para medir um
        # corpus inteiro sem pagar uma chamada por caso.
        textos = list(getattr(args, "textos", None) or [])
        if not textos:
            return _erro("lote exige pelo menos um texto", 2)
        perfil = args.perfil or "default"
        if perfil not in perfis:
            return _erro(f"perfil '{perfil}' nao esta na config (vmm perfis)", 3)
        c = g.classificador_efectivo(cfg, perfil)
        if c["modo"] != "modelo":
            return _erro(f"perfil '{perfil}' sem classificador por modelo "
                         f"({c['motivo'] or 'desligado'})", 1)
        definicao, erros_d = _analise_modelo().normalizar(
            (g.perfil_config(cfg, perfil).get("analise") or {}).get("classificador"))
        if erros_d:
            return _erro("; ".join(erros_d), 2)
        try:
            _seams = _seams_mod()
            if not _seams.seam_disponivel():
                return _erro(f"resolve_runtime_provider indisponivel: {_seams.seam_erro()}", 1)
        except Exception as exc:  # noqa: BLE001
            return _erro(f"resolve_runtime_provider indisponivel: {exc}", 1)

        def resolver(provider: str, modelo: str) -> dict:
            rt = _seams.resolver_provider(provider, modelo)
            return {"base_url": rt.get("base_url") or "", "api_key": rt.get("api_key") or "",
                    "source": rt.get("source") or ""}

        r = _analise_modelo().classificar_lote(textos, definicao, resolver, perfil=perfil)
        if r.get("erro"):
            print(f"erro: {r['erro']}", file=sys.stderr)
        for texto_pedido, nivel in zip(textos, r.get("niveis") or []):
            print(f"{nivel or '?':6s}  {texto_pedido[:70]}")
        print(f"\n{r['provider']}/{r['modelo']} · {r.get('pedidos')} pedido(s) · "
              f"{r.get('ms')} ms · formato={r.get('formato')} · "
              f"pedido={r.get('formato_pedido') or 'texto'} · "
              f"tokens {r.get('tokens_entrada')}→{r.get('tokens_saida')}"
              + (f" · {r['custo_usd']:.6f} USD" if r.get("custo_usd") is not None else ""))
        return 1 if r.get("erro") else 0

    alvo = "global"
    if args.cmd in ("on", "off"):
        if args.perfil:
            p = perfis.get(args.perfil)
            if not isinstance(p, dict):
                return _erro(f"perfil '{args.perfil}' nao esta na config (vmm perfis)", 3)
            p.setdefault("analise", {})["classificador"] = dict(
                p.get("analise", {}).get("classificador") or {})
            bloco = p["analise"]["classificador"]
            alvo = f"perfil {args.perfil}"
        else:
            cfg.setdefault("analise", {}).setdefault("classificador", {})
            bloco = cfg["analise"]["classificador"]
        bloco["activo"] = args.cmd == "on"
        if args.cmd == "on":
            if args.provider:
                bloco["provider"] = args.provider
            if args.modelo:
                bloco["modelo"] = args.modelo
            if getattr(args, "esforco", None) is not None:
                if str(args.esforco).strip() in ("", "-", "nenhum"):
                    bloco.pop("esforco", None)
                else:
                    esforco_c, erro_c = g.normalizar_esforco(args.esforco)
                    if erro_c:
                        return _erro(erro_c, 2)
                    bloco["esforco"] = esforco_c
            if getattr(args, "formato", None) is not None:
                valor_f = str(args.formato).strip().lower()
                if valor_f not in ("auto", "on", "off"):
                    return _erro("--formato deve ser auto, on ou off", 2)
                bloco["formato_json"] = valor_f
            if getattr(args, "via", None) is not None:
                valor_v = str(args.via).strip().lower()
                if valor_v in ("", "-"):
                    bloco.pop("via", None)
                elif valor_v in _analise_modelo().VIAS:
                    bloco["via"] = _analise_modelo().VIAS[valor_v]
                else:
                    return _erro("--via deve ser chat ou decisoes", 2)
            if getattr(args, "confianca_min", None) is not None:
                try:
                    piso = float(args.confianca_min)
                except (TypeError, ValueError):
                    return _erro("--confianca-min deve ser um numero entre 0 e 1", 2)
                if not (0.0 <= piso <= 1.0):
                    return _erro("--confianca-min deve estar entre 0 e 1", 2)
                bloco["confianca_min"] = piso
            if not (bloco.get("provider") and bloco.get("modelo")):
                return _erro("ligar exige --provider e --modelo", 2)
        try:
            _gravar_cas(cfg, rev_lido)
        except ValueError as exc:
            return _erro(f"config recusada: {exc}")
        print(f"{alvo}: classificador {'ligado' if args.cmd == 'on' else 'desligado'}"
              + (f" ({bloco.get('provider')}/{bloco.get('modelo')}"
                 f", esforco={bloco.get('esforco') or 'nao declarado'})"
                 if args.cmd == "on" else ""))
        if args.cmd == "off" and args.perfil is None:
            print("o perfil que quiser desligar um global ligado declara "
                  "{'activo': false} no seu bloco.")
        return 0

    return _erro(f"comando desconhecido: {args.cmd}", 2)


def cmd_perfis(args) -> int:
    cfg, _ = _cfg()
    perfis = cfg.get("perfis") or {}
    print("geridos:")
    for nome, p in sorted(perfis.items()):
        estado = "ligado" if p.get("activo", True) else "desligado"
        print(f"  {nome}: {estado}, {len(p.get('catalogo') or [])} rotas")
    try:
        raiz = Path(g.directorio()).parent.parent / "profiles"
        conhecidos = sorted(d.name for d in raiz.iterdir() if d.is_dir()) if raiz.is_dir() else []
    except Exception:
        conhecidos = []
    if conhecidos:
        print("existentes que nao estao na config (nao geridos):")
        for nome in conhecidos:
            if nome not in perfis:
                print(f"  {nome}")
    return 0


def cmd_perfil_add(args) -> int:
    cfg, _ = _cfg()
    rev_lido = g.rev(cfg)
    perfis = cfg.setdefault("perfis", {})
    if args.nome in perfis:
        return _erro(f"perfil '{args.nome}' ja existe", 2)
    if args.copiar_de:
        base = perfis.get(args.copiar_de)
        if not isinstance(base, dict):
            return _erro(f"perfil de origem '{args.copiar_de}' nao existe", 3)
        novo = json.loads(json.dumps(base))
    else:
        novo = {"activo": True, "catalogo": []}
    perfis[args.nome] = novo
    try:
        _gravar_cas(cfg, rev_lido)
    except ValueError as exc:
        return _erro(f"config recusada: {exc}")
    print(f"perfil '{args.nome}' acrescentado"
          + (f" (copia de '{args.copiar_de}')" if args.copiar_de else " (sem rotas)"))
    return 0


def cmd_estrategia(args) -> int:
    """Le ou muda a estrategia: global, ou de um perfil. Listas fechadas.

    Sem `--modo`/`--por`/`--herdar` e leitura. Muda-se o que se declarar; o
    `--herdar` apaga o bloco (no perfil) ou o global, e e o unico gesto que
    exprime "volta ao que estava" — sem ele o bloco ficava a fixar o valor de
    hoje para sempre.
    """
    cfg, _ = _cfg()
    rev_lido = g.rev(cfg)
    perfis = cfg.get("perfis") or {}
    if args.perfil and args.perfil not in perfis:
        return _erro(f"perfil '{args.perfil}' nao existe", 3)

    if not (args.modo or args.por or args.herdar):
        if args.json:
            print(json.dumps({
                "global": g.estrategia_global(cfg),
                "opcoes": g.opcoes_estrategia(),
                "perfis": {n: g.estrategia_efectiva(cfg, n) for n in sorted(perfis)},
            }, ensure_ascii=False, indent=2))
            return 0
        eg = g.estrategia_global(cfg)
        print(f"global: {eg['modo']} / {eg['por']} ({eg['origem']})")
        linhas = [["perfil", "estrategia", "rotacao", "origem"]]
        for nome in sorted(perfis):
            e = g.estrategia_efectiva(cfg, nome)
            linhas.append([nome, e["modo"], e["por"], e["origem"]])
        print(_tabela(linhas))
        print(f"valores possiveis: modo={'|'.join(g.ESTRATEGIAS)} "
              f"por={'|'.join(g.POR)}")
        return 0

    if args.herdar:
        if args.perfil:
            perfis[args.perfil].pop("estrategia", None)
            perfis[args.perfil].pop("rodizio", None)
        else:
            cfg.pop("estrategia", None)
    else:
        if args.perfil:
            est = g.estrategia_efectiva(cfg, args.perfil)
            alvo = perfis[args.perfil]
            alvo.pop("rodizio", None)
        else:
            est = g.estrategia_global(cfg)
            alvo = cfg
        est.pop("origem", None)
        if args.modo:
            est["modo"] = args.modo
        if args.por:
            est["por"] = args.por
        alvo["estrategia"] = est

    try:
        _gravar_cas(cfg, rev_lido)
    except ValueError as exc:
        # O `ValueError` traz a razao com os valores aceites: mostra-la e o que
        # ensina a lista fechada, um "invalido" seco nao.
        return _erro(f"config recusada: {exc}")

    lido = g.estrategia_efectiva(_cfg()[0], args.perfil) if args.perfil \
        else dict(g.estrategia_global(_cfg()[0]))
    if args.herdar:
        print(f"{args.perfil or 'global'}: voltou a herdar -> "
              f"{lido['modo']} / {lido['por']}")
    else:
        print(f"{args.perfil or 'global'}: {lido['modo']} / {lido['por']}"
              f" ({lido.get('origem', 'global')})")
    return 0


def cmd_perfil_rm(args) -> int:
    cfg, _ = _cfg()
    rev_lido = g.rev(cfg)
    perfis = cfg.get("perfis") or {}
    if args.nome not in perfis:
        return _erro(f"perfil '{args.nome}' nao existe", 3)
    del perfis[args.nome]
    try:
        _gravar_cas(cfg, rev_lido)
    except ValueError as exc:
        return _erro(f"config recusada: {exc}")
    print(f"perfil '{args.nome}' removido (deixa de ser gerido)")
    return 0


def cmd_rota_list(args) -> int:
    cfg, _ = _cfg()
    perfis = cfg.get("perfis") or {}
    alvos = [args.perfil] if args.perfil else sorted(perfis)
    if args.perfil and args.perfil not in perfis:
        return _erro(f"perfil '{args.perfil}' nao existe", 3)
    if args.json:
        print(json.dumps({n: perfis[n].get("catalogo") or [] for n in alvos},
                         ensure_ascii=False, indent=2))
        return 0
    for nome in alvos:
        p = perfis[nome]
        est = g.estrategia_efectiva(cfg, nome)
        print(f"[{nome}] estrategia={est['modo']} rotacao_por={est['por']} "
              f"origem={est['origem']} "
              f"activo={'sim' if p.get('activo', True) else 'NAO'}")
        linhas = [["id", "provider", "modelo", "acesso", "complexidade", "esforco"]]
        for r in p.get("catalogo") or []:
            linhas.append([r.get("id", ""), r.get("provider", ""), r.get("modelo", ""),
                           r.get("acesso", "?"),
                           ",".join(r.get("complexidade") or []),
                           r.get("esforco") or "-"])
        print(_tabela(linhas))
        print()
    return 0


def _niveis(texto: str) -> list[str]:
    itens = [t.strip().lower() for t in texto.replace(";", ",").split(",") if t.strip()]
    maus = [t for t in itens if t not in g.NIVEIS]
    if maus:
        raise ValueError(f"complexidade invalida: {', '.join(maus)} (use {', '.join(g.NIVEIS)})")
    if not itens:
        raise ValueError("--complexidade vazia")
    return list(dict.fromkeys(itens))


def cmd_rota_add(args) -> int:
    cfg, _ = _cfg()
    rev_lido = g.rev(cfg)
    perfis = cfg.get("perfis") or {}
    p = perfis.get(args.perfil)
    if not isinstance(p, dict):
        return _erro(f"perfil '{args.perfil}' nao existe (vmm perfil add {args.perfil})", 3)
    try:
        niveis = _niveis(args.complexidade)
    except ValueError as exc:
        return _erro(str(exc), 2)
    entrada = {"id": args.id or f"{args.provider}:{args.modelo}",
               "provider": args.provider, "modelo": args.modelo,
               "complexidade": niveis,
               "acesso": args.acesso or g.acesso_por_provider(args.provider)}
    if getattr(args, "esforco", None):
        esforco, erro_e = g.normalizar_esforco(args.esforco)
        if erro_e:
            return _erro(erro_e, 2)
        entrada["esforco"] = esforco
    # Custo e ordem entram na chave de escolha (custo, ordem, id). Sem custo
    # declarado a rota ordena depois das que o declaram — nao vale zero.
    if getattr(args, "custo", None) is not None:
        entrada["custo"] = args.custo
    if getattr(args, "ordem", None) is not None:
        entrada["ordem"] = args.ordem
    p.setdefault("catalogo", []).append(entrada)
    try:
        _gravar_cas(cfg, rev_lido)
    except ValueError as exc:
        return _erro(f"config recusada: {exc}")
    extras = [f"acesso={entrada['acesso']}"]
    if entrada.get("esforco"):
        extras.append(f"esforco={entrada['esforco']}")
    if entrada.get("custo") is not None:
        extras.append(f"custo={entrada['custo']}")
    if entrada.get("ordem") is not None:
        extras.append(f"ordem={entrada['ordem']}")
    print(f"rota '{entrada['id']}' acrescentada a {args.perfil} para {','.join(niveis)}"
          f" ({', '.join(extras)})")
    return 0


def cmd_rota_esforco(args) -> int:
    """Define (ou limpa com '-') o esforco de uma rota existente."""
    cfg, _ = _cfg()
    rev_lido = g.rev(cfg)
    p = (cfg.get("perfis") or {}).get(args.perfil)
    if not isinstance(p, dict):
        return _erro(f"perfil '{args.perfil}' nao existe", 3)
    alvo = next((r for r in (p.get("catalogo") or []) if r.get("id") == args.id), None)
    if alvo is None:
        return _erro(f"rota '{args.id}' nao existe em {args.perfil}", 3)
    if str(args.valor).strip() in ("-", "nenhum", "limpar"):
        alvo.pop("esforco", None)
        novo = "nao declarado"
    else:
        esforco, erro_e = g.normalizar_esforco(args.valor)
        if erro_e:
            return _erro(erro_e, 2)
        alvo["esforco"] = esforco
        novo = esforco
    try:
        _gravar_cas(cfg, rev_lido)
    except ValueError as exc:
        return _erro(f"config recusada: {exc}")
    print(f"rota '{args.id}' de {args.perfil}: esforco={novo}")
    return 0


def cmd_rota_rm(args) -> int:
    cfg, _ = _cfg()
    rev_lido = g.rev(cfg)
    p = (cfg.get("perfis") or {}).get(args.perfil)
    if not isinstance(p, dict):
        return _erro(f"perfil '{args.perfil}' nao existe", 3)
    catalogo = p.get("catalogo") or []
    restantes = [r for r in catalogo if r.get("id") != args.id]
    if len(restantes) == len(catalogo):
        return _erro(f"rota '{args.id}' nao existe em {args.perfil}", 3)
    p["catalogo"] = restantes
    try:
        _gravar_cas(cfg, rev_lido)
    except ValueError as exc:
        return _erro(f"config recusada: {exc}")
    print(f"rota '{args.id}' removida de {args.perfil}")
    return 0


def cmd_superficie_list(args) -> int:
    """Mostra as regras de superficie de um perfil e de onde vem cada uma."""
    cfg, _ = _cfg()
    perfis = [args.perfil] if args.perfil else sorted((cfg.get("perfis") or {}))
    saida = False
    for nome in perfis:
        p = g.perfil_config(cfg, nome)
        if p is None:
            if args.perfil:
                return _erro(f"perfil '{nome}' nao e gerido por este config", 3)
            continue
        regras = (p.get("analise") or {}).get("superficies") or {}
        prop = ((cfg.get("perfis", {}).get(nome) or {}).get("analise") or {}).get("superficies") or {}
        if not regras:
            print(f"{nome}: sem regras de superficie")
            saida = True
            continue
        print(f"{nome}:")
        for sup, regra in sorted(regras.items()):
            origem = "perfil" if sup in prop else "global"
            if regra.get("activo") is False:
                alvo = "(anulada)"
            elif regra.get("entrada"):
                alvo = f"entrada={regra['entrada']}"
            elif regra.get("nivel"):
                alvo = f"nivel={regra['nivel']}"
            else:
                alvo = f"{regra.get('provider')}/{regra.get('modelo')}"
            print(f"  {sup:14s} -> {alvo:28s} [{origem}]")
        # Camada de modelo: um modelo que declara superficies restringe-se a
        # elas; quem nao declara herda o comportamento do perfil.
        declarados = [(e.get("id"), e.get("superficies"))
                      for e in (p.get("catalogo") or [])
                      if isinstance(e.get("superficies"), dict) and e["superficies"]]
        if declarados:
            print(f"  {'modelos':14s} (superficie -> permitido)")
            for mid, mapa in declarados:
                regras_m = " ".join(f"{s}={'sim' if v else 'nao'}"
                                    for s, v in sorted(mapa.items()))
                print(f"    {str(mid)[:24]:24s} {regras_m}")
        saida = True
    return 0 if saida else 0


def cmd_superficie_modelo(args) -> int:
    """Liga/desliga uma superficie para um modelo, ou devolve-o a heranca.

    A camada de modelo nunca aponta para outro modelo: so diz se este pode
    servir a superficie. A escolha de qual serve continua a ser do perfil.
    """
    valor = str(args.valor or "").strip().lower()
    mapa_permitido = {"on": True, "sim": True, "true": True,
                      "off": False, "nao": False, "false": False}
    if valor in ("herdar", "herda", "reset"):
        novo = None
    elif valor in mapa_permitido:
        novo = mapa_permitido[valor]
    else:
        return _erro(f"valor invalido '{args.valor}' (use on|off|herdar)", 2)
    sup = str(args.superficie or "").strip().lower()
    if not sup:
        return _erro("superficie vazia", 2)
    cfg, _ = _cfg()
    rev = g.rev(cfg)
    p = g.perfil_config(cfg, args.perfil)
    if p is None:
        return _erro(f"perfil '{args.perfil}' nao e gerido por este config", 3)
    candidatos = g.catalogo_utilizavel(p)
    alvo = next((e for e in candidatos if str(e.get("id")) == args.id), None)
    if alvo is None:
        # Aceita tambem o provider/modelo, para nao obrigar a decorar ids.
        alvo = next((e for e in candidatos
                     if f"{e['provider']}/{e['modelo']}" == args.id), None)
    if alvo is None:
        ids = ", ".join(str(e.get("id")) for e in candidatos) or "(catalogo vazio)"
        return _erro(f"modelo '{args.id}' nao existe em {args.perfil}: {ids}", 3)
    # Escreve-se na entrada DECLARADA no perfil, nao na copia fundida que
    # `perfil_config` devolve: mutar a copia e uma escrita que se perde em
    # silencio (o comando dizia "recusado" e o ficheiro ficava igual).
    entrada = None
    for e in ((cfg.get("perfis", {}).get(args.perfil) or {}).get("catalogo") or []):
        if str(e.get("id")) == str(alvo.get("id")):
            entrada = e
            break
    if entrada is None:
        return _erro(f"modelo '{args.id}' vem do catalogo global, nao de "
                     f"{args.perfil}: declare-o no perfil antes de o limitar", 3)
    mapa = entrada.get("superficies")
    mapa = dict(mapa) if isinstance(mapa, dict) else {}
    if novo is None:
        mapa.pop(sup, None)
        if not mapa:
            entrada.pop("superficies", None)
        else:
            entrada["superficies"] = mapa
        estado = "herda do perfil"
    else:
        mapa[sup] = novo
        entrada["superficies"] = mapa
        estado = "permitido" if novo else "recusado"
    try:
        _gravar_cas(cfg, rev)
    except ValueError as exc:
        return _erro(str(exc))
    # Releitura do disco: o rc do `gravar` nao e prova de que a chave ficou.
    depois, _ = _cfg()
    ficou = g.superficies_do_modelo(
        {"catalogo": ((depois.get("perfis", {}).get(args.perfil) or {})
                      .get("catalogo") or [])}, str(alvo.get("id")))
    if novo is None and ficou is not None:
        return _erro("a escrita nao ficou no disco (mapa ainda presente)", 1)
    if novo is not None and (ficou or {}).get(sup) is not novo:
        return _erro("a escrita nao ficou no disco (valor diferente)", 1)
    print(f"{args.perfil}: {alvo.get('id')} superficie '{sup}' -> {estado}")
    return 0


def _alvo_superficie(valor: str, perfil: str) -> tuple[dict | None, str]:
    """Traduz o alvo escrito na linha de comando numa regra valida."""
    texto = str(valor or "").strip()
    if not texto:
        return None, "alvo vazio (usa entrada:<id>, nivel:<leve|media|alta> ou provider/modelo)"
    if texto.lower().startswith("nivel:"):
        nivel = texto.split(":", 1)[1].strip().lower()
        if nivel not in g.NIVEIS:
            return None, f"nivel invalido: {nivel}"
        return {"nivel": nivel}, ""
    if texto.lower().startswith("entrada:"):
        return {"entrada": texto.split(":", 1)[1].strip()}, ""
    if "/" in texto:
        prov, modelo = texto.split("/", 1)
        if prov.strip() and modelo.strip():
            return {"provider": prov.strip(), "modelo": modelo.strip()}, ""
        return None, "provider/modelo incompleto"
    return {"entrada": texto}, ""


def cmd_superficie_add(args) -> int:
    """Liga uma superficie (cron, api_server, cli...) a uma rota do perfil."""
    cfg, _ = _cfg()
    rev_lido = g.rev(cfg)
    p = (cfg.get("perfis") or {}).get(args.perfil)
    if not isinstance(p, dict):
        return _erro(f"perfil '{args.perfil}' nao existe", 3)
    regra, erro_a = _alvo_superficie(args.alvo, args.perfil)
    if erro_a:
        return _erro(erro_a, 2)
    sup = str(args.superficie or "").strip().lower()
    if not sup:
        return _erro("superficie vazia", 2)
    analise = p.setdefault("analise", {})
    if not isinstance(analise, dict):
        return _erro(f"perfis.{args.perfil}.analise nao e um objecto", 3)
    superficies = analise.setdefault("superficies", {})
    if not isinstance(superficies, dict):
        return _erro(f"perfis.{args.perfil}.analise.superficies nao e um objecto", 3)
    superficies[sup] = regra
    try:
        _gravar_cas(cfg, rev_lido)
    except ValueError as exc:
        return _erro(f"config recusada: {exc}")
    print(f"{args.perfil}: superficie '{sup}' -> {regra}")
    return 0


def cmd_superficie_rm(args) -> int:
    cfg, _ = _cfg()
    rev_lido = g.rev(cfg)
    p = (cfg.get("perfis") or {}).get(args.perfil)
    if not isinstance(p, dict):
        return _erro(f"perfil '{args.perfil}' nao existe", 3)
    sup = str(args.superficie or "").strip().lower()
    superficies = ((p.get("analise") or {}).get("superficies") or {})
    if sup not in superficies:
        return _erro(f"{args.perfil}: superficie '{sup}' nao tem regra propria", 3)
    superficies.pop(sup)
    if not superficies:
        (p.get("analise") or {}).pop("superficies", None)
    try:
        _gravar_cas(cfg, rev_lido)
    except ValueError as exc:
        return _erro(f"config recusada: {exc}")
    print(f"{args.perfil}: regra da superficie '{sup}' removida")
    return 0


def _retencao_efectiva(cfg: dict[str, Any]) -> dict[str, int]:
    """Os numeros que valem: o declarado sobrepoe-se ao padrao, chave a chave."""
    valores = dict(g.RETENCAO_PADRAO)
    declarado = cfg.get("retencao")
    if isinstance(declarado, dict):
        for chave, valor in declarado.items():
            if chave in valores and isinstance(valor, int) and not isinstance(valor, bool):
                valores[chave] = valor
    return valores


def cmd_retencao(args) -> int:
    """Estado do ledger; com `--aplicar`, dobra o detalhe antigo em resumo.

    Sem `--aplicar` nao escreve nada — e o comando que se corre para ver o
    tamanho antes de decidir. O padrao vem do bloco `retencao` do config.
    """
    cfg, erros = _cfg()
    if erros:
        return _erro("config com erros: " + "; ".join(erros))
    rt = _retencao()
    valores = _retencao_efectiva(cfg)
    dias = args.dias if args.dias is not None else valores["dias_completo"]
    if dias < 0:
        return _erro("--dias tem de ser >= 0", 2)
    caminho = g.caminho_ledger()
    estado = rt.relatorio(caminho)
    if "bytes" in estado:
        print(f"ledger {caminho}")
        print(f"  {estado['bytes'] / 1048576:.2f} MB, {estado['linhas']} linhas "
              f"({estado['linhas_detalhe']} de detalhe, {estado['resumos']} resumos)")
    else:
        print(f"ledger {caminho} (inexistente)")
        return 0
    if not args.aplicar:
        print(f"  politica: detalhe dos ultimos {dias} dias (correr --aplicar para compactar)")
        return 0
    resultado = rt.compactar(caminho, dias=dias)
    if not resultado.get("aplicado"):
        print(f"  nada a compactar (corte {resultado.get('corte')})")
        return 0
    antes = resultado.get("bytes_antes", 0)
    depois = resultado.get("bytes_depois", 0)
    print(f"  dobradas {resultado['dobradas']} linhas de {resultado['dias_dobrados']} dias "
          f"em {resultado['resumos']} resumos")
    print(f"  {antes / 1048576:.2f} MB -> {depois / 1048576:.2f} MB "
          f"({100 * depois / max(antes, 1):.1f}%)")
    if resultado.get("ilegiveis"):
        print(f"  {resultado['ilegiveis']} linhas ilegiveis preservadas")
    return 0


def cmd_veredicto(args) -> int:
    """Regista ou le o veredicto de uma entrega.

    Sem `--estado` e leitura: lista os ultimos veredictos (opcionalmente de uma
    tarefa) ou, com `--agregado`, o estado agregado de uma tarefa. Com `--estado`
    escreve — e recusa antes de escrever se a fonte ou a evidencia faltarem.
    """
    vd = _veredicto()
    caminho = g.caminho_ledger()

    if args.estado is None:
        if args.agregado:
            if not args.tarefa:
                return _erro("--agregado exige --tarefa", 2)
            print(json.dumps(vd.estado_da_tarefa(caminho, args.tarefa),
                             indent=2, ensure_ascii=False))
            return 0
        if args.resumo:
            print(json.dumps(vd.resumo(caminho), indent=2, ensure_ascii=False))
            return 0
        todos = vd.veredictos(caminho)
        if args.tarefa:
            todos = [e for e in todos if e.get("tarefa") == args.tarefa]
        todos = todos[-args.n:]
        if args.json:
            print(json.dumps(todos, indent=2, ensure_ascii=False))
            return 0
        if not todos:
            print("sem veredictos registados")
            return 0
        linhas = [["ts", "tarefa", "estado", "fonte", "evidencia"]]
        for e in todos:
            linhas.append([
                str(e.get("ts", ""))[:19],
                str(e.get("tarefa", ""))[:28],
                str(e.get("estado", "")),
                str(e.get("fonte", "")),
                str(e.get("evidencia", ""))[:44],
            ])
        print(_tabela(linhas))
        return 0

    detalhe = None
    if args.detalhe:
        detalhe = {}
        for par in args.detalhe:
            if "=" in par:
                k, v = par.split("=", 1)
                detalhe[k.strip()] = v.strip()
    try:
        evento = vd.registar(
            caminho,
            tarefa=args.tarefa or "",
            estado=args.estado,
            fonte=args.fonte or "",
            evidencia=args.evidencia or "",
            detalhe=detalhe,
            perfil=args.perfil,
            sessao=args.sessao,
            turno=args.turno,
        )
    except ValueError as exc:
        return _erro(f"veredicto recusado: {exc}", 2)
    print(f"veredicto registado: {evento['tarefa']} = {evento['estado']} "
          f"(fonte {evento['fonte']})")
    return 0


def cmd_backups(args) -> int:
    """Estado dos backups do config; com `--aplicar`, poda a rotina."""
    cfg, erros = _cfg()
    if erros:
        return _erro("config com erros: " + "; ".join(erros))
    rt = _retencao()
    valores = _retencao_efectiva(cfg)
    manter = args.manter if args.manter is not None else valores["backups_manter"]
    semanais = args.semanais if args.semanais is not None else valores["backups_semanais"]
    if manter < 1:
        return _erro("--manter tem de ser >= 1", 2)
    if semanais < 0:
        return _erro("--semanais tem de ser >= 0", 2)
    pasta = g.directorio()
    estado = rt.relatorio(g.caminho_ledger(), pasta)
    print(f"backups em {pasta}")
    print(f"  {estado.get('backups_datados', 0)} datados, "
          f"{estado.get('backups_manuais', 0)} a mao, "
          f"{estado.get('backups_bytes', 0) / 1048576:.2f} MB")
    if not args.aplicar:
        print(f"  politica: ficam {manter} + {semanais}/semana (correr --aplicar para podar)")
        return 0
    resultado = rt.podar_backups(pasta, manter=manter, semanais=semanais)
    if not resultado.get("aplicado"):
        print("  nada a podar")
        return 0
    print(f"  removidos {resultado['removidos']} de {resultado['datados_antes']} datados "
          f"({resultado['bytes_libertados'] / 1048576:.2f} MB livres), "
          f"ficaram {resultado['datados_depois']}")
    if resultado.get("semanais_guardados"):
        print(f"  semanais preservados: {', '.join(resultado['semanais_guardados'])}")
    return 0


def cmd_simular(args) -> int:
    cfg, _ = _cfg()
    p = g.perfil_config(cfg, args.perfil)
    if p is None:
        return _erro(f"perfil '{args.perfil}' nao e gerido por este config", 3)
    analise = p.get("analise") or {}
    a = _analise()
    decisao = a.decidir([{"role": "user", "content": args.texto}],
                        limiares=analise.get("limiares"),
                        janela=int(analise.get("janela_mensagens", 8) or 8),
                        maximo=int(analise.get("varredura_max_mensagens", 60) or 60),
                        pesos=analise.get("pesos"))
    nivel = decisao["nivel"] or "(reset)"
    print(f"perfil: {args.perfil}")
    print(f"nivel: {nivel} (origem={decisao['origem']}, score={decisao.get('score')}, "
          f"sinais={','.join(decisao.get('sinais') or [])})")
    tarefa = a.detectar_tarefa([{"role": "user", "content": args.texto}], p.get("tarefas") or {})
    associadas = [r for r in g.catalogo_utilizavel(p) if tarefa in r.get("tarefas", [])]
    if tarefa:
        print(f"tarefa: {tarefa} (origem={'tarefa' if associadas else 'analise; sem associacao'})")
    if nivel in g.NIVEIS or associadas:
        lista = associadas or g.candidatos(g.catalogo_utilizavel(p), nivel)
        if not lista:
            print(f"rota: nenhuma — o catalogo de {args.perfil} nao cobre '{nivel}'")
            return 1
        # o rodizio real depende da sessao; a ordem mostrada e a que o plugin usa
        print("candidatas (ordem efetiva: ordem, custo, id):")
        for r in g.ordenar_candidatos(lista):
            print(f"  {r['id']}: {r['provider']} / {r['modelo']}")
    return 0


def _seams_mod():
    """Carrega `seams.py` tanto em pacote (Hermes) como standalone (CLI/testes)."""
    try:
        from . import seams as mod  # type: ignore[import-not-found]
        return mod
    except Exception:  # noqa: BLE001 — carregado por ficheiro, sem `__package__`
        import importlib.util as _ilu
        global _SEAMS_CACHE
        if _SEAMS_CACHE is not None:
            return _SEAMS_CACHE
        # `_AQUI` e o directorio do ficheiro JA resolvido: usar `__file__` cru
        # falha quando o CLI e invocado pelo symlink /opt/data/bin/vmm, porque
        # procurava /opt/data/bin/seams.py e o comando dizia "seam indisponivel".
        caminho = _AQUI / "seams.py"
        spec = _ilu.spec_from_file_location("vmm_seams", caminho)
        if spec is None or spec.loader is None:
            raise RuntimeError("seams.py nao encontrado junto de cli.py")
        mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules.setdefault("vmm_seams", mod)
        _SEAMS_CACHE = mod
        return mod


_SEAMS_CACHE = None


def cmd_verificar(args) -> int:
    cfg, _ = _cfg()
    perfis = cfg.get("perfis") or {}
    alvos = [args.perfil] if args.perfil else sorted(perfis)
    if args.perfil and args.perfil not in perfis:
        return _erro(f"perfil '{args.perfil}' nao existe", 3)
    try:
        _seams = _seams_mod()
        if not _seams.seam_disponivel():
            return _erro(f"resolve_runtime_provider indisponivel: {_seams.seam_erro()}", 1)
    except Exception as exc:
        return _erro(f"resolve_runtime_provider indisponivel: {exc}", 1)
    # Contexto do perfil alvo: home + escopo de segredos. Sem isto, o CLI
    # resolveria sempre no escopo do processo de arranque e perfis com .env
    # proprio (pessoal/trabalho) pareceriam sem credenciais.
    contexto = None
    try:
        from hermes_constants import reset_hermes_home_override, set_hermes_home_override
        from agent.secret_scope import (build_profile_secret_scope, reset_secret_scope,
                                        set_secret_scope)

        def _entrar(casa) -> tuple[Any, Any] | None:
            token_home = set_hermes_home_override(str(casa))
            token_scope = set_secret_scope(build_profile_secret_scope(casa),
                                           profile_home=str(casa))
            return token_home, token_scope

        def _sair(tokens) -> None:
            token_home, token_scope = tokens
            reset_secret_scope(token_scope)
            reset_hermes_home_override(token_home)

        contexto = (_entrar, _sair)
    except Exception as exc:
        print(f"aviso: contexto por perfil indisponivel ({exc}); "
              f"a resolver no contexto do processo", file=sys.stderr)
    falhas = 0
    for nome in alvos:
        if nome == "*":
            continue
        print(f"[{nome}]")
        casa = g.casa_do_perfil(nome)
        tokens = None
        if contexto is not None:
            try:
                tokens = contexto[0](casa)
            except Exception as exc:
                print(f"  aviso: nao foi possivel montar o contexto do perfil: {exc}",
                      file=sys.stderr)
        try:
            for r in perfis[nome].get("catalogo") or []:
                try:
                    rt = _seams.resolver_provider(r["provider"], r["modelo"])
                    chave = rt.get("api_key") or ""
                    marca = "ok" if len(chave) > 15 else f"chave curta ({len(chave)} B)"
                    if not chave or len(chave) <= 15:
                        falhas += 1
                    print(f"  {r['id']}: {marca}; acesso={r.get('acesso', '?')}; "
                          f"base_url={rt.get('base_url')}; "
                          f"fonte={rt.get('source')}; chave={len(chave)} B")
                except Exception as exc:
                    falhas += 1
                    print(f"  {r['id']}: FALHA {type(exc).__name__}: {exc}")
        finally:
            if tokens is not None:
                try:
                    contexto[1](tokens)
                except Exception:
                    pass
    return 1 if falhas else 0


def cmd_log(args) -> int:
    caminho = g.caminho_ledger()
    if not caminho.exists():
        return _erro(f"sem ledger em {caminho}", 3)
    linhas = caminho.read_text(encoding="utf-8", errors="replace").splitlines()
    filtradas: list[str] = []
    for linha in linhas:
        try:
            ev = json.loads(linha)
        except Exception:
            continue
        if args.perfil and ev.get("perfil") != args.perfil:
            continue
        if args.provider and ev.get("provider") != args.provider:
            continue
        if args.sessao and ev.get("sessao") != args.sessao:
            continue
        if args.evento and ev.get("evento") != args.evento:
            continue
        filtradas.append(linha)
    for linha in filtradas[-max(1, args.n):]:
        print(linha)
    return 0


def cmd_caminho(args) -> int:
    print(g.ficheiro_config())
    print(g.caminho_ledger())
    print(g.caminho_erros())
    return 0


def cmd_auditoria(args) -> int:
    """Quem escreveu a config, e se o que esta no disco veio desse caminho.

    `rc 1` quando ha prova de escrita fora do caminho unico ou uma quebra na
    cadeia: e o sinal que um script de vigia pode ler. `--json` para consumo
    por maquina, o texto para leitura humana.
    """
    info = g.verificar_cadeia(getattr(args, "caminho", None))
    if args.json:
        print(json.dumps(info, ensure_ascii=False, indent=2))
    else:
        print(f"config:  {info['config']}")
        print(f"registo: {info['registo']}"
              f" ({'existe' if info['existe'] else 'AUSENTE'})")
        print(f"linhas:  {info['linhas']}"
              + (f" ({info['ilegiveis']} ilegiveis)" if info["ilegiveis"] else ""))
        print(f"vivo:    {info['vivo_sha'] or '-'}")
        print(f"ultimo:  {info['ultimo_depois_sha'] or '-'}")
        if info["quebras_reconciliadas"]:
            print(f"reconciliadas: linhas {info['quebras_reconciliadas']} "
                  "(quebra declarada com testemunha igual)")
        if info["fora_da_cadeia"]:
            print("VEREDITO: escrita FORA do caminho unico "
                  "(o config vivo nao e o que o ultimo escritor deixou)")
        elif not info["existe"]:
            print("VEREDITO: sem registo de escritas e sem config — nada a atribuir")
        elif info["quebras_nao_explicadas"]:
            print(f"VEREDITO: cadeia quebrada SEM explicacao nas linhas "
                  f"{info['quebras_nao_explicadas']}")
        elif info["quebras_reconciliadas"]:
            print("VEREDITO: OK com quebras declaradas — a config viva veio do "
                  "caminho unico; a historia tem escritas manuais reconciliadas")
        else:
            print("VEREDITO: OK — a config viva foi escrita pelo caminho unico")
    return 1 if (info["fora_da_cadeia"] or not info["cadeia_continua"]) else 0


# --------------------------------------------------------------------------- main


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="vmm", description="vipernet-model-manager")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("status", help="estado global e por perfil")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_status)

    for nome in ("on", "off"):
        s = sub.add_parser(nome, help=f"{nome} o plugin")
        s.add_argument("--perfil")
        s.set_defaults(func=cmd_on_off)

    s = sub.add_parser("classificador", help="modelo que decide a complexidade")
    s.add_argument("cmd", nargs="?", choices=["ver", "on", "off", "lote"], default="ver")
    s.add_argument("textos", nargs="*", help="textos a classificar (cmd=lote)")
    s.add_argument("--perfil", help="actua so neste perfil (por omissao: global)")
    s.add_argument("--provider", help="provider do modelo classificador")
    s.add_argument("--modelo", help="modelo que classifica leve/media/alta")
    s.add_argument("--esforco",
                   help="esforco do classificador: baixo|medio|alto ('-' limpa)")
    s.add_argument("--via", help="transporte: chat|decisoes ('-' repoe chat)")
    s.add_argument("--confianca-min", dest="confianca_min", type=float,
                   help="piso de confianca da decisao (0-1; 0 aceita tudo)")
    s.add_argument("--formato", choices=["auto", "on", "off"],
                   help="response_format json_object: auto (pede e degrada), on, off")
    s.set_defaults(func=cmd_classificador)

    s = sub.add_parser("perfis", help="perfis geridos e existentes")
    s.set_defaults(func=cmd_perfis)

    s = sub.add_parser("perfil", help="gerir perfis")
    sp = s.add_subparsers(dest="sub", required=True)
    a = sp.add_parser("add")
    a.add_argument("nome")
    a.add_argument("--copiar-de")
    a.set_defaults(func=cmd_perfil_add)
    r = sp.add_parser("rm")
    r.add_argument("nome")
    r.set_defaults(func=cmd_perfil_rm)

    s = sub.add_parser("estrategia",
                       help="ver ou mudar a estrategia (global ou por perfil)")
    s.add_argument("--perfil", help="sem isto, mexe-se na estrategia global")
    # `choices` no argparse e a lista fechada ja na linha de comando: o erro
    # aparece antes de ler a config, e as mensagens do `validar` cobrem o resto.
    s.add_argument("--modo", choices=list(g.ESTRATEGIAS))
    s.add_argument("--por", choices=list(g.POR), help="so se aplica a rodizio")
    s.add_argument("--herdar", action="store_true",
                   help="apagar o bloco e voltar a herdar o global")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_estrategia)

    s = sub.add_parser("rota", help="gerir rotas (provider+modelo+complexidade)")
    sr = s.add_subparsers(dest="sub", required=True)
    a = sr.add_parser("list")
    a.add_argument("perfil", nargs="?")
    a.add_argument("--json", action="store_true")
    a.set_defaults(func=cmd_rota_list)
    a = sr.add_parser("add")
    a.add_argument("perfil")
    a.add_argument("provider")
    a.add_argument("modelo")
    a.add_argument("--complexidade", required=True,
                   help="leve,media,alta (lista separada por virgulas)")
    a.add_argument("--id")
    a.add_argument("--acesso", choices=["api", "subscricao"],
                   help="rota de acesso; por omissao infere do provider "
                        "(openai-codex=subscricao, restantes=api)")
    a.add_argument("--esforco", choices=["baixo", "medio", "alto"],
                   help="esforco de raciocinio da rota (omisso: nao declarado)")
    a.add_argument("--custo", type=float,
                   help="preco por milhao de tokens; 0 e gratis. "
                        "Sem custo a rota ordena depois das que o declaram")
    a.add_argument("--ordem", type=int,
                   help="desempate entre custos iguais (o id fecha o empate)")
    a.set_defaults(func=cmd_rota_add)
    a = sr.add_parser("esforco", help="mudar o esforco de uma rota existente")
    a.add_argument("perfil")
    a.add_argument("id")
    a.add_argument("valor", help="baixo|medio|alto, ou '-' para nao declarar")
    a.set_defaults(func=cmd_rota_esforco)
    a = sr.add_parser("rm")
    a.add_argument("perfil")
    a.add_argument("id")
    a.set_defaults(func=cmd_rota_rm)

    # Superficie: o contexto de sessao (cron, api_server, cli...). Uma regra
    # aqui e deterministica e ganha a tarefa e a analise; so o marcador
    # explicito na mensagem (`complexidade: alta`) manda mais.
    s = sub.add_parser("superficie",
                       help="ligar uma superficie de sessao (cron, api_server...) a uma rota")
    ss = s.add_subparsers(dest="sub", required=True)
    a = ss.add_parser("list", help="ver as regras de um perfil (ou de todos)")
    a.add_argument("perfil", nargs="?")
    a.set_defaults(func=cmd_superficie_list)
    a = ss.add_parser("add", help="criar/substituir uma regra")
    a.add_argument("perfil")
    a.add_argument("superficie", help="cron, api_server, cli, whatsapp...")
    a.add_argument("alvo", help="entrada:<id> | nivel:<leve|media|alta> | provider/modelo")
    a.set_defaults(func=cmd_superficie_add)
    a = ss.add_parser("rm", help="remover a regra do perfil")
    a.add_argument("perfil")
    a.add_argument("superficie")
    a.set_defaults(func=cmd_superficie_rm)

    a = ss.add_parser("modelo",
                      help="dizer se um MODELO pode servir uma superficie (on|off|herdar)")
    a.add_argument("perfil")
    a.add_argument("id", help="id do modelo no catalogo (ou provider/modelo)")
    a.add_argument("superficie", help="cron, api_server, cli, whatsapp... ou * para todas")
    a.add_argument("valor", help="on (pode servir) | off (nao serve) | herdar (volta ao perfil)")
    a.set_defaults(func=cmd_superficie_modelo)

    s = sub.add_parser("simular", help="classificar um texto e ver a rota")
    s.add_argument("perfil")
    s.add_argument("texto")
    s.set_defaults(func=cmd_simular)

    s = sub.add_parser("verificar", help="resolver credenciais de cada rota")
    s.add_argument("perfil", nargs="?")
    s.set_defaults(func=cmd_verificar)

    s = sub.add_parser("log", help="ler o ledger")
    s.add_argument("-n", type=int, default=10)
    s.add_argument("--perfil")
    s.add_argument("--provider")
    s.add_argument("--sessao")
    s.add_argument("--evento")
    s.set_defaults(func=cmd_log)

    s = sub.add_parser("caminho", help="caminhos da config e do ledger")
    s.set_defaults(func=cmd_caminho)

    # Retencao: sem `--aplicar` nada e escrito. O tamanho ve-se primeiro; so
    # depois se decide. Os limites por omissao vem do bloco `retencao` do config,
    # para o CLI e o job agendado usarem o mesmo numero.
    s = sub.add_parser("sombra", help="ensaio A/B: pre-registo, vida dos bracos, interruptor")
    s.add_argument("acao", nargs="?", default="estado",
                   choices=["estado", "vida", "on", "off"])
    s.set_defaults(func=cmd_sombra)

    s = sub.add_parser("retencao", help="ledger: tamanho e compactacao do detalhe antigo")
    s.add_argument("--dias", type=int, help="dias de detalhe a manter (padrao: config)")
    s.add_argument("--aplicar", action="store_true", help="compactar de facto")
    s.set_defaults(func=cmd_retencao)

    s = sub.add_parser(
        "veredicto",
        help="veredicto de entrega: registar (com --estado) ou ler (sem ele)",
    )
    s.add_argument("--tarefa", help="nome da tarefa (obrigatorio para escrever)")
    s.add_argument("--estado", choices=["pass", "falha", "parcial"],
                   help="estado a registar; sem isto o comando so le")
    s.add_argument("--fonte", help="quem afirma: gate, releitura, testes, ha, jira, "
                                  "wiki, git, skill, manual")
    s.add_argument("--evidencia", help="prova concreta, curta (obrigatoria para escrever)")
    s.add_argument("--detalhe", action="append", metavar="K=V",
                   help="par chave=valor adicional (repetivel)")
    s.add_argument("--perfil", help="perfil a que o veredicto pertence")
    s.add_argument("--sessao", help="sessao a que o veredicto pertence")
    s.add_argument("--turno", help="turn_id a que o veredicto pertence")
    s.add_argument("--listar", dest="listar", action="store_true",
                   help="(implicito) listar em vez de escrever")
    s.add_argument("--n", type=int, default=20, help="quantos listar (padrao 20)")
    s.add_argument("--json", action="store_true", help="saida em JSON")
    s.add_argument("--resumo", action="store_true", help="contagens por estado e fonte")
    s.add_argument("--agregado", action="store_true",
                   help="estado agregado de --tarefa, todas as fontes")
    s.set_defaults(func=cmd_veredicto)

    s = sub.add_parser("backups", help="backups do config: contagem e poda da rotina")
    s.add_argument("--manter", type=int, help="quantos datados ficam (padrao: config)")
    s.add_argument("--semanais", type=int, help="quantos por semana ISO para la dos primeiros")
    s.add_argument("--aplicar", action="store_true", help="podar de facto")
    s.set_defaults(func=cmd_backups)

    # A auditoria le o que o caminho unico ja grava. `rc 1` quando ha prova de
    # escrita por fora: um script de vigia pode limitar-se a olhar para o rc.
    s = sub.add_parser("auditoria",
                       help="quem escreveu a config e se o disco veio desse caminho")
    s.add_argument("--json", action="store_true")
    s.add_argument("--caminho", help="ficheiro de config alternativo (testes)")
    s.set_defaults(func=cmd_auditoria)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
