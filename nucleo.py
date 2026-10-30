"""
Nucleo do painel: le o arquivo de payloads, chama funcoes_glue e monta uma linha
por carga com o estado da ultima execucao do dia. Nao depende do Streamlit.
"""
from __future__ import annotations

import ast
import contextlib
import csv
import io
import json
import re
import sys
from datetime import date
from pathlib import Path
from urllib.parse import quote

import pandas as pd

# funcoes_glue, datas e utils_gerais vem do aws-data-utils (copia em vendor/)
VENDOR = Path(__file__).resolve().parent / "vendor"
if str(VENDOR) not in sys.path:
    sys.path.insert(0, str(VENDOR))

import funcoes_glue as fg  # noqa: E402
from utils_gerais import valor_vazio  # noqa: E402

SEM_EXECUCAO = "SEM_EXECUCAO"

# ordem de exibicao: o que exige atencao primeiro
RESULTADOS = {
    "FRACASSO": ("❌", "Fracasso"),
    "FALSO_POSITIVO": ("🟠", "Falso positivo"),
    "INDETERMINADO": ("❔", "Indeterminado"),
    "ALERTA": ("⚠️", "Alerta"),
    SEM_EXECUCAO: ("⚪", "Sem execução"),
    "EM_EXECUCAO": ("🔄", "Em execução"),
    "POSITIVO": ("✅", "Positivo"),
}
ORDEM_RESULTADOS = list(RESULTADOS)
CHAVES_NOME = ("_nome", "nome", "name")


def rotulo(resultado: str) -> str:
    icone, texto = RESULTADOS.get(resultado, RESULTADOS["INDETERMINADO"])
    return f"{icone} {texto}"


# --------------------------------------------------------------------------- #
# Leitura do arquivo de payloads
# --------------------------------------------------------------------------- #
def carregar_payloads(conteudo: bytes | str, nome_arquivo: str) -> list[tuple[str, dict]]:
    """Le o arquivo e devolve [(nome, payload), ...], no formato de funcoes_glue.

    Formatos aceitos:
      .py/.txt  a mesma lista que voce ja usa no codigo, ex.:
                lista_payloads = [("Clientes", {"tablename": "clientes"}), ...]
                (lido com ast.literal_eval: nada e executado)
      .json     [["Clientes", {...}]], [{"nome": "Clientes", "payload": {...}}],
                {"Clientes": {...}} ou [{"_nome": "Clientes", "tablename": ...}]
      .jsonl    um item por linha, nos mesmos formatos do JSON
      .yaml     mesmos formatos do JSON
      .csv/.xlsx  uma linha por carga; coluna "nome" + uma coluna por chave do payload
                  (celulas vazias sao ignoradas)
    """
    extensao = nome_arquivo.lower().rsplit(".", 1)[-1]
    if extensao == "xlsx":
        dados = _tabela_para_itens(pd.read_excel(io.BytesIO(conteudo), dtype=str))
        return _normalizar(dados)

    texto = conteudo.decode("utf-8-sig") if isinstance(conteudo, bytes) else conteudo
    if extensao in ("yaml", "yml"):
        import yaml

        dados = yaml.safe_load(texto)
    elif extensao == "jsonl":
        dados = [json.loads(linha) for linha in texto.splitlines() if linha.strip()]
    elif extensao == "csv":
        try:
            dialeto = csv.Sniffer().sniff(texto[:4096], delimiters=",;\t|")
        except csv.Error:
            dialeto = csv.excel
        dados = _tabela_para_itens(pd.DataFrame(list(csv.DictReader(io.StringIO(texto), dialect=dialeto))))
    elif extensao in ("py", "txt"):
        dados = _literal_python(texto)
    else:
        dados = json.loads(texto)
    return _normalizar(dados)


def _literal_python(texto: str):
    try:
        return ast.literal_eval(texto.strip())
    except (ValueError, SyntaxError):
        pass
    for no in ast.parse(texto).body:
        valor = getattr(no, "value", None)
        if isinstance(no, (ast.Assign, ast.AnnAssign, ast.Expr)) and valor is not None:
            try:
                dados = ast.literal_eval(valor)
            except ValueError:
                continue
            if isinstance(dados, (list, tuple, dict)) and dados:
                return dados
    raise ValueError("Não encontrei uma lista literal de payloads no arquivo Python.")


def _tabela_para_itens(df: pd.DataFrame) -> list[dict]:
    df.columns = [str(c).strip() for c in df.columns]
    coluna_nome = next((c for c in df.columns if c.lower() in CHAVES_NOME), None)
    itens = []
    for _, linha in df.iterrows():
        payload = {c: str(v).strip() for c, v in linha.items() if c != coluna_nome and not valor_vazio(v)}
        if payload:
            itens.append({"nome": linha[coluna_nome] if coluna_nome else None, "payload": payload})
    return itens


def _normalizar(dados) -> list[tuple[str, dict]]:
    if isinstance(dados, dict):
        lista = next((dados[k] for k in ("lista_payloads", "payloads", "dicionarios") if isinstance(dados.get(k), list)), None)
        if lista is not None:
            dados = lista
        elif dados and all(isinstance(v, dict) for v in dados.values()):
            dados = list(dados.items())
        else:
            dados = [dados]
    if not isinstance(dados, (list, tuple)) or not dados:
        raise ValueError("O arquivo precisa conter uma lista de payloads.")

    saida: list[tuple[str, dict]] = []
    vistos: dict[str, int] = {}
    for pos, item in enumerate(dados):
        if isinstance(item, (list, tuple)) and len(item) == 2 and isinstance(item[1], dict):
            nome, payload = item
        elif isinstance(item, dict) and isinstance(item.get("payload"), dict):
            nome = next((item[k] for k in CHAVES_NOME if not valor_vazio(item.get(k))), None)
            payload = item["payload"]
        elif isinstance(item, dict):
            nome = item.get("_nome")
            payload = {k: v for k, v in item.items() if k != "_nome"}
        else:
            raise ValueError(f"Item {pos + 1} inválido: use (nome, payload), {{'nome':..., 'payload':...}} ou um dicionário.")

        # funcoes_glue compara com os argumentos ja sem "--"
        payload = {(k[2:] if str(k).startswith("--") else str(k)): v for k, v in payload.items()}
        if not payload:
            raise ValueError(f"Payload vazio no item {pos + 1}.")
        if valor_vazio(nome):
            nome = ", ".join(f"{k}={v}" for k, v in list(payload.items())[:2])
        nome = str(nome).strip()
        # nomes repetidos seriam fundidos pela funcao (agrupa por name)
        if nome in vistos:
            vistos[nome] += 1
            nome = f"{nome} ({vistos[nome]})"
        else:
            vistos[nome] = 1
        saida.append((nome, payload))
    return saida


# --------------------------------------------------------------------------- #
# Avaliacao
# --------------------------------------------------------------------------- #
OPCOES_FILTRO = ("chaves_comparacao", "ignorar_chaves_vazias_no_run",
                 "exigir_ao_menos_um_filtro_aplicado", "permitir_multiplos_matches")


def link_console(regiao: str | None, job: str, run_id: str | None) -> str | None:
    if not run_id or not regiao:
        return None
    return (f"https://{regiao}.console.aws.amazon.com/gluestudio/home?region={regiao}"
            f"#/job/{quote(job, safe='')}/run/{run_id}")


def executar_avaliacao(
    lista_payloads: list[tuple[str, dict]],
    nome_job: str,
    data_referencia: date,
    fuso: str = "America/Sao_Paulo",
    opcoes: dict | None = None,
    dicionario_erros: dict | None = None,
    perfil: str | None = None,
    regiao: str | None = None,
    simulado: bool = False,
) -> dict:
    """Roda avaliar_job_glue_visual e devolve o quadro com uma linha por carga."""
    opcoes = dict(opcoes or {})
    if simulado:
        from simulador import boto3_simulado

        contexto = boto3_simulado(fg, lista_payloads, nome_job, data_referencia, fuso)
    else:
        # funcoes_glue usa boto3.client(...), que le a sessao padrao
        fg.boto3.setup_default_session(profile_name=perfil or None, region_name=regiao or None)
        contexto = contextlib.nullcontext()

    with contexto:
        resultado = fg.avaliar_job_glue_visual(
            nome_job=nome_job,
            data_referencia=data_referencia,
            lista_payloads=lista_payloads,
            last_execution=True,
            timezone=fuso,
            dicionario_erros=dicionario_erros,
            **opcoes,
        )

    # todas as execucoes do dia que bateram com cada nome (sem nova chamada a AWS)
    historico = fg.filtrar_execucoes_por_lista_payloads_nomeados(
        df_execucoes=resultado["df_execucoes"],
        lista_payloads=lista_payloads,
        **{k: v for k, v in opcoes.items() if k in OPCOES_FILTRO},
    )
    quadro = montar_quadro(resultado, lista_payloads, historico, fuso, regiao, nome_job)
    return {
        "quadro": quadro,
        "historico": _historico_legivel(historico, fuso),
        "resumo": resultado["resumo"],
        "total_execucoes_dia": len(resultado["df_execucoes"]),
    }


def _interpretar_erro(detalhe, nome_job) -> dict:
    """tipo_erro / erro_framework / diagnostico pelas funcoes de funcoes_glue."""
    if not detalhe:
        return {"tipo_erro": None, "erro_framework": None, "diagnostico": None}
    info = fg.classificar_erro_detalhado(detalhe)
    linhas_codigo = re.findall(r"line (\d+)", str(detalhe))
    info.update(
        job_name=nome_job,
        linha_erro=linhas_codigo[-1] if linhas_codigo else "(não informada)",
        coluna_nao_resolvida=fg.extrair_coluna_nao_resolvida(detalhe),
    )
    colunas = fg.extrair_colunas_disponiveis(detalhe)
    if colunas:
        info["colunas_disponiveis_limpas"] = fg.limpar_colunas_disponiveis(colunas)
        info["colunas_com_espaco"] = fg.detectar_colunas_com_espaco_suspeito(colunas)
    return {
        "tipo_erro": info.get("tipo_erro"),
        "erro_framework": info.get("erro_framework"),
        "diagnostico": fg.gerar_diagnostico(info),
    }


def _hora_local(valor, fuso):
    if valor_vazio(valor):
        return pd.NaT
    ts = pd.Timestamp(valor)
    if ts.tzinfo is not None:
        ts = ts.tz_convert(fuso).tz_localize(None)
    return ts


def _limpo(valor):
    return None if valor_vazio(valor) else valor


def montar_quadro(resultado, lista_payloads, historico, fuso, regiao, nome_job) -> pd.DataFrame:
    df_av = resultado.get("df_avaliacao_visual")
    if df_av is None or df_av.empty:
        df_av = resultado.get("df_avaliacao")
    por_nome = {} if df_av is None or df_av.empty else {r["name"]: r for _, r in df_av.iterrows()}
    contagem = {} if historico is None or historico.empty else historico["name"].value_counts().to_dict()

    linhas = []
    for ordem, (nome, payload) in enumerate(lista_payloads):
        r = por_nome.get(nome)
        if r is None:
            linhas.append({"name": nome, "resultado_operacional": SEM_EXECUCAO, "payload": payload,
                           "execucoes_no_dia": 0, "_ordem_arquivo": ordem})
            continue
        resultado_op = _limpo(r.get("resultado_operacional")) or "INDETERMINADO"
        detalhe = _limpo(r.get("detalhe_erro"))
        linhas.append({
            **_interpretar_erro(detalhe, nome_job),
            "name": nome,
            "resultado_operacional": resultado_op,
            "status_aws": _limpo(r.get("status")),
            "status_operacional": _limpo(r.get("status_operacional")),
            "started_on": _hora_local(r.get("started_on"), fuso),
            "completed_on": _hora_local(r.get("completed_on"), fuso),
            "execution_time": _limpo(r.get("execution_time")),
            "detalhe_erro": _limpo(r.get("detalhe_erro")),
            "erro_categoria": _limpo(r.get("erro_categoria")),
            "erro_resumido": _limpo(r.get("erro_resumido")),
            "impacto_negocio": _limpo(r.get("impacto_negocio")),
            "acao_sugerida": _limpo(r.get("acao_sugerida")),
            "log_localizado": bool(r.get("log_localizado")) if not valor_vazio(r.get("log_localizado")) else False,
            "qt_erros_log": _limpo(r.get("qt_erros_log")),
            "qt_warnings_log": _limpo(r.get("qt_warnings_log")),
            "job_run_id": _limpo(r.get("job_run_id")),
            "filtros_aplicados": r.get("_filtros_aplicados"),
            "filtros_ignorados": r.get("_filtros_ignorados"),
            "payload": payload,
            "execucoes_no_dia": int(contagem.get(nome, 1)),
            "link": link_console(regiao, nome_job, _limpo(r.get("job_run_id"))),
            "_ordem_arquivo": ordem,
        })

    quadro = pd.DataFrame(linhas)
    for coluna in ("started_on", "completed_on"):
        if coluna not in quadro:
            quadro[coluna] = pd.NaT
        quadro[coluna] = pd.to_datetime(quadro[coluna])
    quadro["_ordem_resultado"] = quadro["resultado_operacional"].map(
        lambda x: ORDEM_RESULTADOS.index(x) if x in ORDEM_RESULTADOS else 2)
    return quadro.sort_values(["_ordem_resultado", "_ordem_arquivo"]).reset_index(drop=True)


def _historico_legivel(historico: pd.DataFrame, fuso: str) -> pd.DataFrame:
    colunas = ["name", "job_run_id", "status", "started_on", "completed_on", "error_message"]
    if historico is None or historico.empty:
        return pd.DataFrame(columns=colunas)
    df = historico[[c for c in colunas if c in historico.columns]].copy()
    for coluna in ("started_on", "completed_on"):
        if coluna in df:
            df[coluna] = df[coluna].map(lambda v: _hora_local(v, fuso))
    return df


def resumo_por_resultado(quadro: pd.DataFrame) -> dict[str, int]:
    contagem = quadro["resultado_operacional"].value_counts().to_dict()
    return {r: int(contagem.get(r, 0)) for r in ORDEM_RESULTADOS}


def formatar_duracao(segundos) -> str:
    if valor_vazio(segundos):
        return ""
    segundos = int(float(segundos))
    h, resto = divmod(segundos, 3600)
    m, s = divmod(resto, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m{s:02d}s"
