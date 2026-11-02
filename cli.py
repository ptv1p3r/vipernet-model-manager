#!/usr/bin/env python3
"""vmm — CLI do vipernet-model-manager.

Uso:
  vmm status [--json]                 estado global e por perfil
  vmm on|off [--perfil NOME]          liga/desliga (sem --perfil: global)
  vmm perfis                          perfis geridos e perfis conhecidos
  vmm perfil add NOME [--copiar-de X]
  vmm perfil rm NOME
  vmm rota list [PERFIL] [--json]
  vmm rota add PERFIL PROVIDER MODELO --complexidade leve,media [--id ID] [--acesso api|subscricao] [--esforco baixo|medio|alto]
  vmm rota esforco PERFIL ID baixo|medio|alto|-
  vmm rota rm PERFIL ID
  vmm simular PERFIL "texto"          classifica o texto e mostra a rota escolhida
  vmm verificar [PERFIL]              resolve credenciais de cada rota (sem as imprimir)
  vmm log [-n N] [--perfil NOME] [--provider P] [--sessao S] [--evento E]
  vmm caminho                         imprime o caminho do config
  vmm recarregar                      pede ao gateway para recarregar os plugins

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


def _cfg() -> tuple[dict[str, Any], list[str]]:
    return g.carregar()


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
    linhas = [["perfil", "activo", "estrategia", "rodizio", "rotas", "niveis"]]
    for nome, p in sorted(perfis.items()):
        rotas = p.get("catalogo") or []
        niveis = sorted({n for r in rotas for n in (r.get("complexidade") or [])},
                        key=lambda n: g.NIVEIS.index(n))
        linhas.append([nome, "sim" if p.get("activo", True) else "NAO",
                       str(p.get("estrategia") or "rodizio"),
                       str((p.get("rodizio") or {}).get("por") or "sessao"),
                       str(len(rotas)), ",".join(niveis) or "-"])
    print(_tabela(linhas))
    return 1 if erros else 0


def cmd_on_off(args) -> int:
    cfg, _ = _cfg()
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
        g.gravar(cfg)
    except ValueError as exc:
        return _erro(f"config recusada: {exc}")
    print(f"{alvo}: {'ligado' if ligar else 'desligado'}")
    print("o gateway le a config a quente (cache de 5 s); nao e preciso reiniciar.")
    return 0


def cmd_classificador(args) -> int:
    """Le e escreve a definicao do classificador por modelo (global e por perfil).

    Sem argumentos mostra o que esta efectivamente em vigor em cada perfil —
    que e a pergunta verdadeira, porque um perfil herda o global e um perfil
    que declare o seu substitui-o por inteiro.
    """
    cfg, erros = _cfg()
    for erro in erros:
        print(f"aviso: {erro}", file=sys.stderr)
    perfis = cfg.get("perfis") or {}

    if args.cmd == "ver" or args.cmd is None:
        linhas = [["perfil", "classificador", "provider", "modelo", "esforco", "formato", "origem"]]
        for nome in sorted(perfis):
            # Uma so casa para a pergunta: `classificador_efectivo` resolve a
            # heranca e o "e utilizavel?" — o CLI nao repete essa logica.
            c = g.classificador_efectivo(cfg, nome)
            linhas.append([
                nome,
                "ligado" if c["modo"] == "modelo" else "desligado",
                c["provider"] or "-",
                c["modelo"] or "-",
                c["esforco"] or "-",
                c["formato_json"] or "auto",
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
            from hermes_cli.runtime_provider import resolve_runtime_provider
        except Exception as exc:  # noqa: BLE001
            return _erro(f"resolve_runtime_provider indisponivel: {exc}", 1)

        def resolver(provider: str, modelo: str) -> dict:
            rt = resolve_runtime_provider(requested=provider, target_model=modelo)
            return {"base_url": rt.get("base_url") or "", "api_key": rt.get("api_key") or "",
                    "source": rt.get("source") or ""}

        r = _analise_modelo().classificar_lote(textos, definicao, resolver, perfil=perfil)
        if r.get("erro"):
            print(f"erro: {r['erro']}", file=sys.stderr)
        for texto_pedido, nivel in zip(textos, r.get("niveis") or []):
            print(f"{nivel or '?':6s}  {texto_pedido[:70]}")
        print(f"\n{r['provider']}/{r['modelo']} · {r.get('pedidos')} pedido(s) · "
              f"{r.get('ms')} ms · formato={r.get('formato')} · "
              f"pedido={'json' if r.get('formato_pedido') == 'json' else 'texto'} · "
              f"tokens {r.get('tokens_entrada')}→{r.get('tokens_saida')}")
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
            if not (bloco.get("provider") and bloco.get("modelo")):
                return _erro("ligar exige --provider e --modelo", 2)
        try:
            g.gravar(cfg)
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
    perfis = cfg.setdefault("perfis", {})
    if args.nome in perfis:
        return _erro(f"perfil '{args.nome}' ja existe", 2)
    if args.copiar_de:
        base = perfis.get(args.copiar_de)
        if not isinstance(base, dict):
            return _erro(f"perfil de origem '{args.copiar_de}' nao existe", 3)
        novo = json.loads(json.dumps(base))
    else:
        novo = {"activo": True, "estrategia": "rodizio", "rodizio": {"por": "sessao"},
                "catalogo": []}
    perfis[args.nome] = novo
    try:
        g.gravar(cfg)
    except ValueError as exc:
        return _erro(f"config recusada: {exc}")
    print(f"perfil '{args.nome}' acrescentado"
          + (f" (copia de '{args.copiar_de}')" if args.copiar_de else " (sem rotas)"))
    return 0


def cmd_perfil_rm(args) -> int:
    cfg, _ = _cfg()
    perfis = cfg.get("perfis") or {}
    if args.nome not in perfis:
        return _erro(f"perfil '{args.nome}' nao existe", 3)
    del perfis[args.nome]
    try:
        g.gravar(cfg)
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
        print(f"[{nome}] estrategia={p.get('estrategia')} "
              f"rodizio={(p.get('rodizio') or {}).get('por')} "
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
    p.setdefault("catalogo", []).append(entrada)
    try:
        g.gravar(cfg)
    except ValueError as exc:
        return _erro(f"config recusada: {exc}")
    print(f"rota '{entrada['id']}' acrescentada a {args.perfil} para {','.join(niveis)}"
          f" (acesso={entrada['acesso']}"
          + (f", esforco={entrada['esforco']})" if entrada.get("esforco") else ")"))
    return 0


def cmd_rota_esforco(args) -> int:
    """Define (ou limpa com '-') o esforco de uma rota existente."""
    cfg, _ = _cfg()
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
        g.gravar(cfg)
    except ValueError as exc:
        return _erro(f"config recusada: {exc}")
    print(f"rota '{args.id}' de {args.perfil}: esforco={novo}")
    return 0


def cmd_rota_rm(args) -> int:
    cfg, _ = _cfg()
    p = (cfg.get("perfis") or {}).get(args.perfil)
    if not isinstance(p, dict):
        return _erro(f"perfil '{args.perfil}' nao existe", 3)
    catalogo = p.get("catalogo") or []
    restantes = [r for r in catalogo if r.get("id") != args.id]
    if len(restantes) == len(catalogo):
        return _erro(f"rota '{args.id}' nao existe em {args.perfil}", 3)
    p["catalogo"] = restantes
    try:
        g.gravar(cfg)
    except ValueError as exc:
        return _erro(f"config recusada: {exc}")
    print(f"rota '{args.id}' removida de {args.perfil}")
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
        # o rodizio real depende da sessao; aqui mostra-se a ordem declarada
        print("candidatas (ordem declarada):")
        for r in lista:
            print(f"  {r['id']}: {r['provider']} / {r['modelo']}")
    return 0


def cmd_verificar(args) -> int:
    cfg, _ = _cfg()
    perfis = cfg.get("perfis") or {}
    alvos = [args.perfil] if args.perfil else sorted(perfis)
    if args.perfil and args.perfil not in perfis:
        return _erro(f"perfil '{args.perfil}' nao existe", 3)
    try:
        from hermes_cli.runtime_provider import resolve_runtime_provider
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
                    rt = resolve_runtime_provider(requested=r["provider"], target_model=r["modelo"])
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


def cmd_recarregar(args) -> int:
    try:
        from gateway.control_socket import reload_gateway_plugins
        from hermes_constants import get_default_hermes_root
    except Exception as exc:
        return _erro(f"recarregar exige o interpretador do Hermes: {exc}", 1)
    home = Path(get_default_hermes_root())
    resposta = reload_gateway_plugins(home)
    if not resposta:
        return _erro("nenhum gateway respondeu ao pedido de recarga", 1)
    print(json.dumps(resposta, ensure_ascii=False, indent=2))
    return 0 if resposta.get("reloaded") else 1


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

    s = sub.add_parser("recarregar", help="recarregar plugins no gateway")
    s.set_defaults(func=cmd_recarregar)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
