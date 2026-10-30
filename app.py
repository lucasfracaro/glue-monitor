"""
glue-monitor: estado da ultima execucao do dia de cada carga (payload) de um
Glue Job, usando avaliar_job_glue_visual de funcoes_glue.

Rodar:
    streamlit run app.py
"""
from __future__ import annotations

import inspect
import json
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

PASTA = Path(__file__).resolve().parent
if str(PASTA) not in sys.path:
    sys.path.insert(0, str(PASTA))

import nucleo  # noqa: E402

st.set_page_config(page_title="Glue Monitor", page_icon="🧩", layout="wide")

EXEMPLO_PAYLOADS = PASTA / "exemplos" / "payloads.json"
EXEMPLO_ERROS = PASTA / "exemplos" / "dicionario_erros.json"


# --------------------------------------------------------------------------- #
# Barra lateral
# --------------------------------------------------------------------------- #
with st.sidebar:
    st.header("⚙️ Configuração")
    arquivo = st.file_uploader(
        "Arquivo de payloads",
        type=["py", "txt", "json", "jsonl", "yaml", "yml", "csv", "xlsx"],
        help="A mesma lista [(nome, payload), ...] que você passa para "
             "avaliar_job_glue_por_lista_payloads_nomeados, em .py, JSON, YAML, CSV ou Excel.",
    )

    st.subheader("Job e AWS")
    demo = st.toggle("Modo demonstração (sem AWS)", value=os.getenv("GLUE_MONITOR_DEMO") == "1")
    nome_job = st.text_input("Glue Job", os.getenv("GLUE_JOB_NAME", "meu_job"))
    regiao = st.text_input("Região", os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION", "us-east-1")))
    perfil = st.text_input("Perfil AWS (opcional)", os.getenv("AWS_PROFILE", ""), disabled=demo)

    st.subheader("Data")
    fuso = st.text_input("Fuso horário", "America/Sao_Paulo")
    try:
        hoje = datetime.now(ZoneInfo(fuso)).date()
    except Exception:
        st.error("Fuso horário inválido.")
        st.stop()
    data_ref = st.date_input("Dia", value=hoje, max_value=hoje, format="DD/MM/YYYY")

    with st.expander("Regras de avaliação"):
        logs_sucesso = st.checkbox("Ler log das execuções com sucesso", True,
                                   help="Detecta FALSO_POSITIVO e ALERTA. Faz uma leitura no CloudWatch por carga.")
        logs_falha = st.checkbox("Ler log das falhas sem ErrorMessage", True)
        ignorar_vazias = st.checkbox("Ignorar chaves ausentes na execução", True,
                                     help="ignorar_chaves_vazias_no_run")
        exigir_filtro = st.checkbox("Exigir ao menos uma chave comparada", True,
                                    help="exigir_ao_menos_um_filtro_aplicado")
        multiplos = st.checkbox("Permitir uma execução em mais de uma carga", True,
                                help="permitir_multiplos_matches")
        chaves_campo = st.text_input("Comparar só estas chaves (opcional)", "",
                                     help="Separadas por vírgula. Vazio = todas as chaves preenchidas do payload.")

    with st.expander("Dicionário de erros (opcional)"):
        arquivo_erros = st.file_uploader(
            "JSON {trecho_da_mensagem: {erro_categoria, erro_resumido, impacto_negocio, acao_sugerida}}",
            type=["json"],
        )
        usar_erros_exemplo = st.checkbox("Usar o dicionário de exemplo", value=arquivo_erros is None)

    intervalo = st.selectbox("Atualização automática", [0, 60, 300, 900],
                             format_func=lambda s: "Desligada" if s == 0 else f"A cada {s // 60} min")
    if st.button("🔄 Atualizar agora", type="primary"):
        st.cache_data.clear()


# --------------------------------------------------------------------------- #
# Entradas
# --------------------------------------------------------------------------- #
st.title("🧩 Glue Monitor")

try:
    if arquivo is not None:
        lista_payloads = nucleo.carregar_payloads(arquivo.getvalue(), arquivo.name)
        origem = arquivo.name
    else:
        lista_payloads = nucleo.carregar_payloads(EXEMPLO_PAYLOADS.read_bytes(), EXEMPLO_PAYLOADS.name)
        origem = f"{EXEMPLO_PAYLOADS.name} (exemplo, envie o seu na barra lateral)"
except Exception as erro:
    st.error(f"Não consegui ler o arquivo de payloads: {erro}")
    st.stop()

dicionario_erros = None
try:
    if arquivo_erros is not None:
        dicionario_erros = json.loads(arquivo_erros.getvalue().decode("utf-8-sig"))
    elif usar_erros_exemplo and EXEMPLO_ERROS.exists():
        dicionario_erros = json.loads(EXEMPLO_ERROS.read_text("utf-8"))
except Exception as erro:
    st.warning(f"Dicionário de erros ignorado: {erro}")

opcoes = {
    "avaliar_Logs_em_sucesso": logs_sucesso,
    "avaliar_Logs_em_falha": logs_falha,
    "ignorar_chaves_vazias_no_run": ignorar_vazias,
    "exigir_ao_menos_um_filtro_aplicado": exigir_filtro,
    "permitir_multiplos_matches": multiplos,
    "chaves_comparacao": [c.strip().lstrip("-") for c in chaves_campo.split(",") if c.strip()] or None,
}


VISAO_ULTIMA = "Última execução por carga"
VISAO_TODAS = "Todas as execuções do dia"

CORES = {
    "FRACASSO": "#e5484d", "FALSO_POSITIVO": "#f76b15", "INDETERMINADO": "#8e8c99", "ALERTA": "#f5b400",
    nucleo.SEM_EXECUCAO: "#b8b8b8", "EM_EXECUCAO": "#3e8ef7", "POSITIVO": "#30a46c",
}


@st.cache_data(ttl=60, show_spinner=False)
def consultar(payloads_json: str, nome_job: str, data_ref: date, fuso: str, opcoes_json: str,
              erros_json: str, perfil: str, regiao: str, demo: bool, todas: bool, rodada: int):
    resultado = nucleo.executar_avaliacao(
        lista_payloads=[tuple(p) for p in json.loads(payloads_json)],
        nome_job=nome_job, data_referencia=data_ref, fuso=fuso,
        opcoes=json.loads(opcoes_json), dicionario_erros=json.loads(erros_json),
        perfil=perfil, regiao=regiao, simulado=demo, todas_execucoes=todas,
    )
    resultado["consultado_em"] = datetime.now(ZoneInfo(fuso))
    return resultado


def texto(valor) -> str:
    """Texto para a tabela: vazio em vez de None/NaN."""
    if valor is None or (not isinstance(valor, (list, dict)) and pd.isna(valor)):
        return ""
    return str(valor)


def hora(valor) -> str:
    return "" if valor is None or pd.isna(valor) else pd.Timestamp(valor).strftime("%H:%M:%S")


def texto_erro(linha) -> str:
    resumo, categoria = texto(linha.get("erro_resumido")), texto(linha.get("erro_categoria"))
    if resumo and categoria != "NAO_CLASSIFICADO":
        return resumo
    return texto(linha.get("detalhe_erro"))


def rotulo_execucao(linha) -> str:
    if linha.get("avaliada"):
        return nucleo.rotulo(linha["resultado_operacional"])
    return f"{nucleo.icone_execucao(linha)} {texto(linha.get('status_aws'))}"


def colunas_visiveis(tabela: pd.DataFrame, colunas: list[str]) -> list[str]:
    """Esconde colunas opcionais que vieram todas vazias."""
    opcionais = {"Ação sugerida", "Console", "Tipo de erro"}
    return [c for c in colunas if c not in opcionais or (tabela[c] != "").any()]


def filtrar(tabela: pd.DataFrame, resultados: pd.Series, filtro: list, busca: str) -> pd.DataFrame:
    mascara = pd.Series(True, index=tabela.index)
    if filtro:
        mascara &= resultados.isin(filtro).values
    if busca:
        mascara &= tabela["_busca"].str.contains(busca.lower(), regex=False)
    return tabela[mascara]


def texto_busca(df: pd.DataFrame, erros: pd.Series) -> pd.Series:
    return (df["name"].astype(str) + " "
            + df["payload"].map(lambda p: json.dumps(p, ensure_ascii=False, default=str)) + " "
            + erros.astype(str) + " " + df.get("job_run_id", pd.Series("", index=df.index)).map(texto)).str.lower()


# --------------------------------------------------------------------------- #
# Painel
# --------------------------------------------------------------------------- #
def painel():
    visao = st.radio(
        "Visão", [VISAO_ULTIMA, VISAO_TODAS], horizontal=True, key="visao", label_visibility="collapsed",
        help="Todas as execuções lê o log de cada execução do dia (mais chamadas ao CloudWatch).",
    )
    todas = visao == VISAO_TODAS
    rodada = int(time.time() // intervalo) if intervalo else 0
    try:
        with st.spinner(f"Avaliando {len(lista_payloads)} cargas do job {nome_job}..."):
            r = consultar(
                json.dumps(lista_payloads, default=str), nome_job, data_ref, fuso,
                json.dumps(opcoes, sort_keys=True), json.dumps(dicionario_erros),
                perfil, regiao, demo, todas, rodada,
            )
    except Exception as erro:
        st.error(f"Não foi possível avaliar o job: {type(erro).__name__}: {erro}")
        st.caption("Confira credenciais (perfil/variáveis AWS), região e nome do job. As permissões necessárias "
                   "são glue:GetJobRuns, glue:GetJobRun e logs:GetLogEvents. Para testar sem AWS, ligue o modo demonstração.")
        return

    quadro: pd.DataFrame = r["quadro"]
    execucoes: pd.DataFrame = r["execucoes"]
    st.caption(
        f"Job **{nome_job}** · {len(lista_payloads)} cargas de **{origem}** · dia {data_ref:%d/%m/%Y} · "
        f"{r['total_execucoes_dia']} execuções do job no dia, {len(execucoes)} delas das suas cargas · "
        f"consultado às {r['consultado_em']:%H:%M:%S}" + (" · 🧪 dados simulados" if demo else "")
    )

    # Indicadores: situacao atual (ultima execucao) de cada carga
    contagem = nucleo.resumo_por_resultado(quadro)
    blocos = [("Cargas", len(quadro))] + [(nucleo.rotulo(k), v) for k, v in contagem.items()]
    for inicio in (0, 4):
        for coluna, (titulo, valor) in zip(st.columns(4), blocos[inicio:inicio + 4]):
            coluna.metric(titulo, valor)

    problemas = contagem["FRACASSO"] + contagem["FALSO_POSITIVO"] + contagem["INDETERMINADO"]
    if problemas:
        st.error(f"{problemas} carga(s) com problema na última execução do dia.")
    elif contagem["ALERTA"] or contagem[nucleo.SEM_EXECUCAO]:
        st.warning(f"{contagem['ALERTA']} com alerta no log, {contagem[nucleo.SEM_EXECUCAO]} sem execução no dia.")
    elif contagem["EM_EXECUCAO"]:
        st.info(f"{contagem['EM_EXECUCAO']} carga(s) em execução; nenhum problema até agora.")
    else:
        st.success("Todas as cargas terminaram como POSITIVO.")

    f1, f2 = st.columns([2, 3])
    filtro = f1.multiselect("Resultado", nucleo.ORDEM_RESULTADOS, format_func=nucleo.rotulo, placeholder="Todos")
    busca = f2.text_input("Buscar", placeholder="nome da carga, valor do payload, erro, run id...")
    agora = pd.Timestamp(datetime.now(ZoneInfo(fuso))).tz_localize(None)

    if todas:
        visao_todas(quadro, execucoes, filtro, busca, agora, r["total_execucoes_dia"])
    else:
        visao_ultima(quadro, execucoes, filtro, busca, agora, r["total_execucoes_dia"])


def visao_ultima(quadro, execucoes, filtro, busca, agora, total_execucoes_dia):
    registros = quadro.to_dict("records")
    tabela = pd.DataFrame({
        "Resultado": [nucleo.rotulo(l["resultado_operacional"]) for l in registros],
        "Carga": quadro["name"],
        "Estado AWS": [texto(l.get("status_aws")) for l in registros],
        "Início": [hora(l.get("started_on")) for l in registros],
        "Fim": [hora(l.get("completed_on")) for l in registros],
        "Duração": [nucleo.duracao_execucao(l, agora) for l in registros],
        "Execuções no dia": quadro["execucoes_no_dia"],
        "Histórico do dia": quadro["historico_dia"].map(texto),
        "Tipo de erro": [texto(l.get("tipo_erro")) for l in registros],
        "Erro": [texto_erro(l) for l in registros],
        "Ação sugerida": [texto(l.get("acao_sugerida")) for l in registros],
        "Console": [texto(l.get("link")) for l in registros],
    })
    tabela["_posicao"] = range(len(tabela))
    tabela["_busca"] = texto_busca(quadro, tabela["Erro"])
    tabela = filtrar(tabela, quadro["resultado_operacional"], filtro, busca)

    colunas = colunas_visiveis(tabela, ["Resultado", "Carga", "Estado AWS", "Início", "Fim", "Duração",
                                        "Execuções no dia", "Histórico do dia", "Tipo de erro", "Erro",
                                        "Ação sugerida", "Console"])
    selecao = st.dataframe(
        tabela, hide_index=True, column_order=colunas, key="tabela_ultima",
        on_select="rerun", selection_mode="single-row",
        column_config={
            "Histórico do dia": st.column_config.TextColumn(
                help="Cada execução do dia, da primeira para a última. A última mostra o resultado operacional; "
                     "as anteriores, o estado na AWS (✅ sucesso, ❌ falha, 🔄 em execução). "
                     "Em 'Todas as execuções do dia' todas mostram o resultado operacional."),
            "Erro": st.column_config.TextColumn(width="large"),
            "Console": st.column_config.LinkColumn(display_text="abrir ↗"),
        },
    )
    linhas = [i for i in (selecao.selection.rows if selecao else []) if i < len(tabela)]
    if linhas:
        detalhe_carga(quadro.iloc[int(tabela.iloc[linhas[0]]["_posicao"])], execucoes, total_execucoes_dia, agora)
    else:
        st.caption("Clique numa carga para ver o payload, o diagnóstico do erro e as execuções do dia.")
    baixar(tabela, colunas, "ultima")


def visao_todas(quadro, execucoes, filtro, busca, agora, total_execucoes_dia):
    sem_execucao = quadro.loc[quadro["resultado_operacional"] == nucleo.SEM_EXECUCAO, "name"].tolist()
    if execucoes.empty:
        st.info("Nenhuma execução das cargas neste dia.")
        return

    # mesma ordem das cargas na visao resumida (problemas primeiro); dentro da carga, mais recente primeiro
    posicao_carga = {nome: i for i, nome in enumerate(quadro["name"])}
    df = execucoes.assign(_carga=execucoes["name"].map(posicao_carga)) \
        .sort_values(["_carga", "started_on"], ascending=[True, False]).reset_index(drop=True)

    linha_do_tempo(df, agora, [n for n in quadro["name"] if n in set(df["name"])])

    registros = df.to_dict("records")
    tabela = pd.DataFrame({
        "Resultado": [rotulo_execucao(l) for l in registros],
        "Carga": df["name"],
        "Nº": [f"{l['ordem_no_dia']}ª de {l['total_no_dia']}" for l in registros],
        "Atual": df["atual"],
        "Estado AWS": [texto(l.get("status_aws")) for l in registros],
        "Início": [hora(l.get("started_on")) for l in registros],
        "Fim": [hora(l.get("completed_on")) for l in registros],
        "Duração": [nucleo.duracao_execucao(l, agora) for l in registros],
        "Tipo de erro": [texto(l.get("tipo_erro")) for l in registros],
        "Erro": [texto_erro(l) for l in registros],
        "Ação sugerida": [texto(l.get("acao_sugerida")) for l in registros],
        "Console": [texto(l.get("link")) for l in registros],
    })
    tabela["_posicao"] = range(len(tabela))
    tabela["_busca"] = texto_busca(df, tabela["Erro"])
    tabela = filtrar(tabela, df["resultado_operacional"], filtro, busca)

    colunas = colunas_visiveis(tabela, ["Resultado", "Carga", "Nº", "Atual", "Estado AWS", "Início", "Fim",
                                        "Duração", "Tipo de erro", "Erro", "Ação sugerida", "Console"])
    selecao = st.dataframe(
        tabela, hide_index=True, column_order=colunas, key="tabela_todas",
        on_select="rerun", selection_mode="single-row",
        column_config={
            "Atual": st.column_config.CheckboxColumn(help="Última execução do dia desta carga (a que vale como situação atual)"),
            "Erro": st.column_config.TextColumn(width="large"),
            "Console": st.column_config.LinkColumn(display_text="abrir ↗"),
        },
    )
    if sem_execucao:
        st.caption("Sem execução no dia: " + ", ".join(sem_execucao))
    linhas = [i for i in (selecao.selection.rows if selecao else []) if i < len(tabela)]
    if linhas:
        detalhe_execucao(df.iloc[int(tabela.iloc[linhas[0]]["_posicao"])], agora)
    else:
        st.caption("Clique numa execução para ver o payload e o diagnóstico do erro.")
    baixar(tabela, colunas, "todas")


def linha_do_tempo(df, agora, ordem_cargas):
    dados = pd.DataFrame({
        "Carga": df["name"],
        "Início": df["started_on"].dt.strftime("%Y-%m-%dT%H:%M:%S"),
        "Fim": [(f if pd.notna(f) else agora).strftime("%Y-%m-%dT%H:%M:%S") for f in df["completed_on"]],
        "Resultado": [rotulo_execucao(l) for l in df.to_dict("records")],
        "Duração": [nucleo.duracao_execucao(l, agora) for l in df.to_dict("records")],
        "Execução": [f"{o}ª de {t}" for o, t in zip(df["ordem_no_dia"], df["total_no_dia"])],
    })
    rotulos = [nucleo.rotulo(k) for k in CORES]
    presentes = [r for r in rotulos if r in set(dados["Resultado"])]
    extras = sorted(set(dados["Resultado"]) - set(rotulos))  # execucoes so com estado AWS
    spec = {
        "height": max(140, 34 * len(ordem_cargas)),
        "encoding": {
            "y": {"field": "Carga", "type": "nominal", "sort": ordem_cargas, "title": None},
            "x": {"field": "Início", "type": "temporal", "title": None, "axis": {"format": "%H:%M"}},
            "color": {
                "field": "Resultado", "type": "nominal",
                "scale": {"domain": presentes + extras,
                          "range": [CORES[k] for k in CORES if nucleo.rotulo(k) in presentes] + ["#8e8c99"] * len(extras)},
                "legend": {"orient": "bottom", "title": None},
            },
            "tooltip": [
                {"field": "Carga"}, {"field": "Execução"}, {"field": "Resultado"},
                {"field": "Início", "type": "temporal", "format": "%H:%M:%S"},
                {"field": "Fim", "type": "temporal", "format": "%H:%M:%S"}, {"field": "Duração"},
            ],
        },
        "layer": [
            {"mark": {"type": "bar", "cornerRadius": 3, "height": 14}, "encoding": {"x2": {"field": "Fim"}}},
            {"mark": {"type": "point", "filled": True, "size": 60}},  # execucoes curtas continuam visiveis
        ],
    }
    with st.expander("Linha do tempo do dia", expanded=True):
        largura = {"width": "stretch"} if "width" in inspect.signature(st.vega_lite_chart).parameters \
            else {"use_container_width": True}
        st.vega_lite_chart(dados, spec, **largura)


def mostrar_erro(linha):
    campos = [("diagnostico", "Diagnóstico")]
    if texto(linha.get("erro_categoria")) not in ("", "NAO_CLASSIFICADO"):
        campos = [("erro_resumido", "Resumo"), ("impacto_negocio", "Impacto"),
                  ("acao_sugerida", "Ação sugerida")] + campos
    for campo, titulo in campos:
        if texto(linha.get(campo)):
            st.markdown(f"**{titulo}:** {linha[campo]}")
    if texto(linha.get("detalhe_erro")):
        st.markdown("**Detalhe do erro**")
        st.code(linha["detalhe_erro"], language=None, wrap_lines=True)


def info_execucao(linha):
    st.markdown(f"**Execução:** `{linha.get('job_run_id')}`  \n"
                f"**Status operacional:** {texto(linha.get('status_operacional')) or texto(linha.get('status_aws'))}")
    if linha.get("log_localizado"):
        st.markdown(f"**Log:** {int(linha.get('qt_erros_log') or 0)} linha(s) de erro, "
                    f"{int(linha.get('qt_warnings_log') or 0)} de aviso")
    if texto(linha.get("link")):
        st.markdown(f"[Abrir no console da AWS ↗]({linha['link']})")


def detalhe_carga(linha: pd.Series, execucoes: pd.DataFrame, total_execucoes_dia: int, agora):
    with st.container(border=True):
        st.subheader(f"{nucleo.rotulo(linha['resultado_operacional'])} · {linha['name']}")
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Payload**")
            st.json(linha["payload"])
            if isinstance(linha.get("filtros_ignorados"), dict) and linha["filtros_ignorados"]:
                st.markdown("**Chaves ignoradas na comparação**")
                st.json(linha["filtros_ignorados"])
        with c2:
            if linha["resultado_operacional"] == nucleo.SEM_EXECUCAO:
                st.markdown(
                    f"Nenhuma das **{total_execucoes_dia}** execuções do job neste dia bateu com o payload. "
                    "Confira se os valores estão iguais aos argumentos do job (sem o `--`)."
                )
            else:
                info_execucao(linha)
            mostrar_erro(linha)

        da_carga = execucoes[execucoes["name"] == linha["name"]].sort_values("started_on", ascending=False)
        if not da_carga.empty:
            st.markdown("**Execuções do dia para esta carga**")
            registros = da_carga.to_dict("records")
            st.dataframe(pd.DataFrame({
                "Nº": [f"{l['ordem_no_dia']}ª" for l in registros],
                "Resultado": [rotulo_execucao(l) for l in registros],
                "Início": [hora(l.get("started_on")) for l in registros],
                "Fim": [hora(l.get("completed_on")) for l in registros],
                "Duração": [nucleo.duracao_execucao(l, agora) for l in registros],
                "Erro": [texto_erro(l) for l in registros],
                "Run ID": [texto(l.get("job_run_id")) for l in registros],
            }), hide_index=True)


def detalhe_execucao(linha: pd.Series, agora):
    with st.container(border=True):
        st.subheader(f"{rotulo_execucao(linha)} · {linha['name']} · {linha['ordem_no_dia']}ª de {linha['total_no_dia']}")
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Payload**")
            st.json(linha["payload"])
        with c2:
            info_execucao(linha)
            st.markdown(f"**Início:** {hora(linha.get('started_on'))} · **Fim:** {hora(linha.get('completed_on')) or 'em andamento'}"
                        f" · **Duração:** {nucleo.duracao_execucao(linha, agora)}")
            mostrar_erro(linha)


def baixar(tabela, colunas, sufixo):
    st.download_button(
        "⬇️ Baixar CSV",
        tabela[colunas].to_csv(index=False).encode("utf-8-sig"),
        file_name=f"glue_monitor_{nome_job}_{data_ref:%Y%m%d}_{sufixo}.csv",
        mime="text/csv",
    )


st.fragment(run_every=intervalo or None)(painel)()
