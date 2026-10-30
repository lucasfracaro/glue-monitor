"""
glue-monitor: estado da ultima execucao do dia de cada carga (payload) de um
Glue Job, usando avaliar_job_glue_visual de funcoes_glue.

Rodar:
    streamlit run app.py
"""
from __future__ import annotations

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


@st.cache_data(ttl=60, show_spinner=False)
def consultar(payloads_json: str, nome_job: str, data_ref: date, fuso: str, opcoes_json: str,
              erros_json: str, perfil: str, regiao: str, demo: bool, rodada: int):
    resultado = nucleo.executar_avaliacao(
        lista_payloads=[tuple(p) for p in json.loads(payloads_json)],
        nome_job=nome_job, data_referencia=data_ref, fuso=fuso,
        opcoes=json.loads(opcoes_json), dicionario_erros=json.loads(erros_json),
        perfil=perfil, regiao=regiao, simulado=demo,
    )
    resultado["consultado_em"] = datetime.now(ZoneInfo(fuso))
    return resultado


def texto_erro(linha) -> str:
    if linha.get("erro_resumido") and linha.get("erro_categoria") != "NAO_CLASSIFICADO":
        return linha["erro_resumido"]
    return linha.get("detalhe_erro") or ""


# --------------------------------------------------------------------------- #
# Painel
# --------------------------------------------------------------------------- #
def painel():
    rodada = int(time.time() // intervalo) if intervalo else 0
    try:
        with st.spinner(f"Avaliando {len(lista_payloads)} cargas do job {nome_job}..."):
            r = consultar(
                json.dumps(lista_payloads, default=str), nome_job, data_ref, fuso,
                json.dumps(opcoes, sort_keys=True), json.dumps(dicionario_erros),
                perfil, regiao, demo, rodada,
            )
    except Exception as erro:
        st.error(f"Não foi possível avaliar o job: {type(erro).__name__}: {erro}")
        st.caption("Confira credenciais (perfil/variáveis AWS), região e nome do job. As permissões necessárias "
                   "são glue:GetJobRuns, glue:GetJobRun e logs:GetLogEvents. Para testar sem AWS, ligue o modo demonstração.")
        return

    quadro: pd.DataFrame = r["quadro"]
    historico: pd.DataFrame = r["historico"]
    st.caption(
        f"Job **{nome_job}** · {len(lista_payloads)} cargas de **{origem}** · dia {data_ref:%d/%m/%Y} · "
        f"{r['total_execucoes_dia']} execuções do job no dia · consultado às {r['consultado_em']:%H:%M:%S}"
        + (" · 🧪 dados simulados" if demo else "")
    )

    # Indicadores
    contagem = nucleo.resumo_por_resultado(quadro)
    blocos = [("Total", len(quadro))] + [(nucleo.rotulo(k), v) for k, v in contagem.items()]
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

    # Filtros
    f1, f2 = st.columns([2, 3])
    filtro = f1.multiselect("Resultado", nucleo.ORDEM_RESULTADOS, format_func=nucleo.rotulo, placeholder="Todos")
    busca = f2.text_input("Buscar", placeholder="nome da carga, valor do payload, erro...")

    agora = pd.Timestamp(datetime.now(ZoneInfo(fuso))).tz_localize(None)
    tabela = pd.DataFrame({
        "Resultado": quadro["resultado_operacional"].map(nucleo.rotulo),
        "Carga": quadro["name"],
        "Estado AWS": quadro.get("status_aws"),
        "Início": quadro["started_on"],
        "Fim": quadro["completed_on"],
        "Duração": [
            nucleo.formatar_duracao(t if pd.notna(t) and t else
                                    ((agora - i).total_seconds() if pd.notna(i) and pd.isna(f) else None))
            for t, i, f in zip(quadro.get("execution_time", pd.Series([None] * len(quadro))),
                               quadro["started_on"], quadro["completed_on"])
        ],
        "Execuções no dia": quadro["execucoes_no_dia"],
        "Tipo de erro": quadro.get("tipo_erro"),
        "Erro": [texto_erro(l) for l in quadro.to_dict("records")],
        "Ação sugerida": quadro.get("acao_sugerida"),
        "Console": quadro.get("link"),
    })
    tabela["_posicao"] = range(len(tabela))
    tabela["_busca"] = (quadro["name"].astype(str) + " "
                        + quadro["payload"].map(lambda p: json.dumps(p, ensure_ascii=False, default=str)) + " "
                        + tabela["Erro"].astype(str)).str.lower()
    if filtro:
        tabela = tabela[quadro["resultado_operacional"].isin(filtro)]
    if busca:
        tabela = tabela[tabela["_busca"].str.contains(busca.lower(), regex=False)]

    colunas = ["Resultado", "Carga", "Estado AWS", "Início", "Fim", "Duração", "Execuções no dia",
               "Tipo de erro", "Erro", "Ação sugerida", "Console"]
    if tabela["Ação sugerida"].isna().all():
        colunas.remove("Ação sugerida")
    if tabela["Console"].isna().all():
        colunas.remove("Console")

    selecao = st.dataframe(
        tabela,
        hide_index=True,
        column_order=colunas,
        column_config={
            "Início": st.column_config.DatetimeColumn(format="DD/MM HH:mm:ss"),
            "Fim": st.column_config.DatetimeColumn(format="DD/MM HH:mm:ss"),
            "Erro": st.column_config.TextColumn(width="large"),
            "Console": st.column_config.LinkColumn(display_text="abrir ↗"),
        },
        on_select="rerun",
        selection_mode="single-row",
        key="tabela",
    )

    # Detalhe da carga selecionada
    linhas = [i for i in (selecao.selection.rows if selecao else []) if i < len(tabela)]
    if linhas:
        detalhe(quadro.iloc[int(tabela.iloc[linhas[0]]["_posicao"])], historico, r["total_execucoes_dia"])
    else:
        st.caption("Clique numa linha para ver o payload, o diagnóstico do erro e as execuções do dia.")

    st.download_button(
        "⬇️ Baixar CSV",
        tabela[colunas].to_csv(index=False).encode("utf-8-sig"),
        file_name=f"glue_monitor_{nome_job}_{data_ref:%Y%m%d}.csv",
        mime="text/csv",
    )


def detalhe(linha: pd.Series, historico: pd.DataFrame, total_execucoes_dia: int):
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
                st.markdown(f"**Última execução:** `{linha.get('job_run_id')}`  \n"
                            f"**Status operacional:** {linha.get('status_operacional')}")
                if str(linha.get("log_localizado")) == "True":  # bool ou numpy.bool_
                    st.markdown(f"**Log:** {int(linha['qt_erros_log'])} linha(s) de erro, "
                                f"{int(linha.get('qt_warnings_log') or 0)} de aviso")
            campos = [("diagnostico", "Diagnóstico")]
            if linha.get("erro_categoria") not in (None, "NAO_CLASSIFICADO") and pd.notna(linha.get("erro_categoria")):
                campos = [("erro_resumido", "Resumo"), ("impacto_negocio", "Impacto"),
                          ("acao_sugerida", "Ação sugerida")] + campos
            for campo, titulo in campos:
                if isinstance(linha.get(campo), str) and linha[campo]:
                    st.markdown(f"**{titulo}:** {linha[campo]}")
            if isinstance(linha.get("detalhe_erro"), str) and linha["detalhe_erro"]:
                st.markdown("**Detalhe do erro**")
                st.code(linha["detalhe_erro"], language=None, wrap_lines=True)

        execucoes = historico[historico["name"] == linha["name"]]
        if not execucoes.empty:
            st.markdown("**Execuções do dia para esta carga**")
            st.dataframe(
                execucoes.drop(columns=["name"]).rename(columns={
                    "job_run_id": "Run ID", "status": "Estado", "started_on": "Início",
                    "completed_on": "Fim", "error_message": "Erro AWS"}),
                hide_index=True,
                column_config={
                    "Início": st.column_config.DatetimeColumn(format="HH:mm:ss"),
                    "Fim": st.column_config.DatetimeColumn(format="HH:mm:ss"),
                },
            )


st.fragment(run_every=intervalo or None)(painel)()
