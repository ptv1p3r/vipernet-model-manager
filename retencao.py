"""Retencao do ledger e dos backups do config.

Duas politicas independentes, ambas **aditivas**: o detalhe antigo e dobrado
num resumo, nunca removido sem deixar os agregados. Aplicam-se pelo CLI do
plugin (`vmm retencao`, `vmm backups`) ou directamente por este modulo, e sao
idempotentes — correr duas vezes seguidas na segunda nao muda nada.

Ledger
    As linhas com mais de ``dias_completo`` dias sao substituidas por **um**
    evento ``resumo_dia`` por dia, com contagens por evento, perfil, nivel e
    modelo, somas de tokens e o rasto do classificador. O painel le o ledger
    de tras para a frente e nunca precisa das linhas antigas; `extrair_amostra`
    filtra por evento e ignora o resumo; `replay_turno` exige prefixo de turno
    e tambem o ignora. Logo a compactacao e invisivel para os leitores.

Backups
    Ficam os ``backups_manter`` mais recentes mais **um por semana ISO** para
    la desse numero. Os nomeados a mao (``config.json.bak-<palavra>``, sem
    carimbo de data) nunca sao tocados: sao marcos, nao rotina.

Trava
    O append do ledger e um ``O_APPEND`` sem trava. Sem uma trava cooperativa,
    um ``os.replace`` a meio de uma compactacao deitava fora as linhas
    escritas entre a leitura e a troca. O append toma a trava em modo
    partilhado (varios escritores em paralelo) e a compactacao em modo
    exclusivo.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfo

FUSO = ZoneInfo("Europe/Lisbon")

DIAS_COMPLETO_PADRAO = 7
BACKUPS_MANTER_PADRAO = 10
BACKUPS_SEMANAIS_PADRAO = 1

EVENTO_RESUMO = "resumo_dia"
SUFIXO_TRAVA = ".lock"

# `config.json.bak-20261104T175857` — so os com carimbo de data sao podaveis.
_CARIMBO = re.compile(r"\.bak-(\d{8}T\d{6})$")
# `/opt/data/.../config.json.bak-antes-dos-free-reais` — marco a mao, intocavel.
_CARIMBO_QUALQUER = re.compile(r"\.bak-")


# --------------------------------------------------------------------------
# tempo
# --------------------------------------------------------------------------
def agora() -> datetime:
    """Agora, com fuso explicito — nunca o relogio ingénuo do sistema."""
    return datetime.now(FUSO)


def _instante(valor: Any) -> datetime | None:
    """`ts` do ledger -> datetime com fuso. Devolve None se nao for legivel."""
    if not isinstance(valor, str) or not valor:
        return None
    try:
        lido = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        return None
    if lido.tzinfo is None:
        lido = lido.replace(tzinfo=timezone.utc)
    return lido


def _dia_local(instante: datetime) -> str:
    """Dia no fuso do utilizador: um «dia» e o que ele ve no calendario."""
    return instante.astimezone(FUSO).date().isoformat()


# --------------------------------------------------------------------------
# trava cooperativa
# --------------------------------------------------------------------------
@contextlib.contextmanager
def trava(caminho: Path, *, exclusiva: bool = True) -> Iterator[None]:
    """Trava no ficheiro ``<caminho>.lock`` (nunca no proprio ficheiro).

    O `os.replace` troca o inode: uma trava no ledger seria uma trava num
    objecto que deixa de ser o ledger a meio da operacao. O `.lock` ao lado
    tem inode estavel.
    """
    caminho = Path(caminho)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(caminho) + SUFIXO_TRAVA, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX if exclusiva else fcntl.LOCK_SH)
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


# --------------------------------------------------------------------------
# agregacao do resumo
# --------------------------------------------------------------------------
def _contar(mapa: dict[str, int], chave: Any) -> None:
    if chave is None or chave == "":
        return
    texto = str(chave)
    mapa[texto] = mapa.get(texto, 0) + 1


def _somar_tokens(acc: dict[str, int], tokens: Any) -> None:
    if not isinstance(tokens, dict):
        return
    for nome, valor in tokens.items():
        if isinstance(valor, (int, float)) and not isinstance(valor, bool):
            acc[str(nome)] = acc.get(str(nome), 0) + int(valor)


def _modelo_da_linha(ev: dict[str, Any]) -> str:
    """Modelo que **serviu** o pedido, nao o que foi pedido."""
    for chave in ("respondido_por", "modelo_efectivo", "escolhido_modelo"):
        valor = ev.get(chave)
        if valor:
            return str(valor)
    rota = ev.get("rota")
    if isinstance(rota, dict) and rota.get("modelo"):
        return str(rota["modelo"])
    de = ev.get("de")
    if isinstance(de, str) and de:
        return de
    return ""


def resumir_dia(dia: str, eventos: list[dict[str, Any]]) -> dict[str, Any]:
    """Uma linha de resumo que preserva o que os leitores agregam.

    Nao guarda texto de conversa (o ledger nunca o teve), nem o `lido` do
    classificador linha a linha — guarda quantas leituras houve e por que
    modelo, que e o que se consulta depois de o detalhe sair.
    """
    contagem: dict[str, int] = {}
    perfis: dict[str, int] = {}
    niveis: dict[str, int] = {}
    modelos: dict[str, int] = {}
    origens: dict[str, int] = {}
    superficies: dict[str, int] = {}
    tokens: dict[str, int] = {}
    classif: dict[str, int] = {}
    primeiro: datetime | None = None
    ultimo: datetime | None = None
    bytes_linhas = 0

    for ev in eventos:
        _contar(contagem, ev.get("evento"))
        _contar(perfis, ev.get("perfil"))
        _contar(niveis, ev.get("nivel"))
        _contar(origens, ev.get("origem"))
        _contar(superficies, ev.get("superficie"))
        modelo = _modelo_da_linha(ev)
        _contar(modelos, modelo)
        _somar_tokens(tokens, ev.get("tokens"))
        leitura = ev.get("classificador")
        if isinstance(leitura, dict):
            _contar(classif, leitura.get("modelo") or leitura.get("provider") or "desconhecido")
        instante = _instante(ev.get("ts"))
        if instante is not None:
            primeiro = instante if primeiro is None or instante < primeiro else primeiro
            ultimo = instante if ultimo is None or instante > ultimo else ultimo
        bytes_linhas += len(json.dumps(ev, ensure_ascii=False)) + 1

    return {
        "ts": (ultimo or primeiro or agora()).isoformat(),
        "evento": EVENTO_RESUMO,
        "dia": dia,
        "linhas": len(eventos),
        "bytes_originais": bytes_linhas,
        "primeiro_ts": primeiro.isoformat() if primeiro else None,
        "ultimo_ts": ultimo.isoformat() if ultimo else None,
        "eventos": dict(sorted(contagem.items())),
        "perfis": dict(sorted(perfis.items())),
        "niveis": dict(sorted(niveis.items())),
        "modelos": dict(sorted(modelos.items())),
        "origens": dict(sorted(origens.items())),
        "superficies": dict(sorted(superficies.items())),
        "tokens": dict(sorted(tokens.items())),
        "classificador_leituras": dict(sorted(classif.items())),
    }


# --------------------------------------------------------------------------
# compactacao do ledger
# --------------------------------------------------------------------------
def compactar(caminho: str | Path, dias: int = DIAS_COMPLETO_PADRAO,
              *, agora_: datetime | None = None) -> dict[str, Any]:
    """Dobra as linhas mais antigas que ``dias`` em ``resumo_dia``.

    Devolve as contagens medidas da operacao. Nao falha se o ficheiro nao
    existir: devolve ``aplicado: False`` e a razao.
    """
    caminho = Path(caminho)
    if dias < 0:
        raise ValueError(f"dias tem de ser >= 0, veio {dias!r}")
    if not caminho.exists():
        return {"aplicado": False, "motivo": "ledger inexistente", "caminho": str(caminho)}

    referencia = agora_ or agora()
    corte = referencia - timedelta(days=dias)
    resultado: dict[str, Any] = {
        "caminho": str(caminho),
        "corte": corte.isoformat(),
        "aplicado": False,
    }

    with trava(caminho, exclusiva=True):
        bruto = caminho.read_bytes()
        resultado["bytes_antes"] = len(bruto)
        guardar: list[str] = []
        dobrar: list[dict[str, Any]] = []
        ilegiveis = 0
        for linha in bruto.decode("utf-8", "replace").splitlines():
            if not linha.strip():
                continue
            try:
                ev = json.loads(linha)
            except json.JSONDecodeError:
                # Nao se apaga o que nao se entende: fica como esta.
                guardar.append(linha)
                ilegiveis += 1
                continue
            if not isinstance(ev, dict):
                guardar.append(linha)
                ilegiveis += 1
                continue
            if ev.get("evento") == EVENTO_RESUMO:
                # Resumos ja feitos: tem `dia` proprio e preservam-se.
                guardar.append(linha)
                continue
            instante = _instante(ev.get("ts"))
            if instante is None or instante >= corte:
                guardar.append(linha)
                continue
            dobrar.append(ev)

        if not dobrar:
            resultado.update({"limpas": len(guardar), "dobradas": 0, "ilegiveis": ilegiveis})
            return resultado

        por_dia: dict[str, list[dict[str, Any]]] = {}
        for ev in dobrar:
            por_dia.setdefault(_dia_local(_instante(ev.get("ts")) or referencia), []).append(ev)

        # Resumos antigos (de compactacoes anteriores) mantem-se tal e qual,
        # ordenados com os novos; so as linhas de detalhe sao substituidas.
        resumos_antigos = [ln for ln in guardar if _e_resumo(ln)]
        restantes = [ln for ln in guardar if not _e_resumo(ln)]
        resumos_novos = [json.dumps(resumir_dia(d, evs), ensure_ascii=False) for d, evs in sorted(por_dia.items())]

        finais = _juntar_resumos(resumos_antigos, resumos_novos) + restantes
        texto = "\n".join(finais) + ("\n" if finais else "")

        tmp = caminho.with_suffix(caminho.suffix + ".tmp")
        tmp.write_text(texto, encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, caminho)

        resultado.update({
            "aplicado": True,
            "limpas": len(restantes),
            "dobradas": len(dobrar),
            "dias_dobrados": len(por_dia),
            "resumos": len(resumos_antigos) + len(resumos_novos),
            "ilegiveis": ilegiveis,
            "bytes_depois": len(texto),
        })
    return resultado


def _e_resumo(linha: str) -> bool:
    return f'"evento": "{EVENTO_RESUMO}"' in linha or f'"evento":"{EVENTO_RESUMO}"' in linha


def _dia_de(linha: str) -> str | None:
    try:
        ev = json.loads(linha)
    except json.JSONDecodeError:
        return None
    return ev.get("dia") if isinstance(ev, dict) else None


def _juntar_resumos(antigos: list[str], novos: list[str]) -> list[str]:
    """Um resumo por dia, sem duplicar — o novo substitui o antigo do mesmo dia."""
    por_dia: dict[str, str] = {}
    for linha in antigos:
        dia = _dia_de(linha)
        if dia:
            por_dia[dia] = linha
    for linha in novos:
        dia = _dia_de(linha)
        if dia:
            por_dia[dia] = linha
    return [por_dia[d] for d in sorted(por_dia)]


# --------------------------------------------------------------------------
# poda dos backups
# --------------------------------------------------------------------------
def podar_backups(directorio: str | Path, manter: int = BACKUPS_MANTER_PADRAO,
                  semanais: int = BACKUPS_SEMANAIS_PADRAO) -> dict[str, Any]:
    """Fica com os ``manter`` mais recentes + ``semanais`` por semana ISO.

    Os backups sem carimbo de data (``.bak-<palavra>``) sao marcos postos a
    mao e nao entram na conta, nem sao apagados.
    """
    if manter < 1:
        raise ValueError(f"manter tem de ser >= 1, veio {manter!r}")
    if semanais < 0:
        raise ValueError(f"semanais tem de ser >= 0, veio {semanais!r}")

    pasta = Path(directorio)
    if not pasta.is_dir():
        return {"aplicado": False, "motivo": "directorio inexistente", "directorio": str(pasta)}

    datados: list[tuple[str, Path]] = []
    manuais: list[str] = []
    for item in pasta.iterdir():
        if not item.is_file() or not _CARIMBO_QUALQUER.search(item.name):
            continue
        achado = _CARIMBO.search(item.name)
        if achado:
            datados.append((achado.group(1), item))
        else:
            manuais.append(item.name)

    datados.sort(key=lambda x: x[0], reverse=True)
    ficam = {c for c, _ in datados[:manter]}
    extra_por_semana: dict[tuple[int, int], str] = {}
    for carimbo, _ in datados[manter:]:
        try:
            instante = datetime.strptime(carimbo, "%Y%m%dT%H%M%S")
        except ValueError:
            continue
        chave = instante.isocalendar()[:2]
        extra_por_semana.setdefault(chave, carimbo)

    semanais_escolhidos: list[str] = []
    for chave in sorted(extra_por_semana, reverse=True):
        if len(semanais_escolhidos) >= semanais:
            break
        semanais_escolhidos.append(extra_por_semana[chave])
    ficam |= set(semanais_escolhidos)

    removidos: list[str] = []
    libertados = 0
    for carimbo, item in datados:
        if carimbo in ficam:
            continue
        try:
            tamanho = item.stat().st_size
            item.unlink()
        except OSError:
            continue
        removidos.append(item.name)
        libertados += tamanho

    return {
        "aplicado": bool(removidos),
        "directorio": str(pasta),
        "datados_antes": len(datados),
        "datados_depois": len(ficam),
        "manuais": sorted(manuais),
        "removidos": len(removidos),
        "bytes_libertados": libertados,
        "semanais_guardados": sorted(semanais_escolhidos),
    }


def relatorio(ledger: str | Path, pasta_config: str | Path | None = None) -> dict[str, Any]:
    """Estado actual sem escrever nada — o que o CLI mostra por omissão."""
    ledger = Path(ledger)
    dados: dict[str, Any] = {"ledger": str(ledger)}
    if ledger.exists():
        linhas = 0
        resumos = 0
        dobradas = 0
        for linha in ledger.open("rb"):
            if not linha.strip():
                continue
            linhas += 1
            if _e_resumo(linha.decode("utf-8", "replace")):
                resumos += 1
            else:
                dobradas += 1
        dados.update({
            "bytes": ledger.stat().st_size,
            "linhas": linhas,
            "linhas_detalhe": dobradas,
            "resumos": resumos,
        })
    if pasta_config is not None:
        pasta = Path(pasta_config)
        datados = 0
        manuais = 0
        bytes_bak = 0
        if pasta.is_dir():
            for item in pasta.iterdir():
                if not item.is_file() or not _CARIMBO_QUALQUER.search(item.name):
                    continue
                if _CARIMBO.search(item.name):
                    datados += 1
                else:
                    manuais += 1
                with contextlib.suppress(OSError):
                    bytes_bak += item.stat().st_size
        dados.update({
            "backups_datados": datados,
            "backups_manuais": manuais,
            "backups_bytes": bytes_bak,
        })
    return dados


def semana_iso(carimbo: str) -> tuple[int, int] | None:
    """`20261104T175857` -> (ano ISO, semana ISO). Util para diagnosticar."""
    try:
        return datetime.strptime(carimbo, "%Y%m%dT%H%M%S").isocalendar()[:2]
    except ValueError:
        return None
