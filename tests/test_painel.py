"""Testes do painel (rodar: python -m pytest -q).

Usam o simulador no lugar da AWS; as funcoes de funcoes_glue rodam de verdade.
"""
import json
import sys
import types
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    import boto3  # noqa: F401
except ImportError:  # o simulador substitui o boto3; basta o modulo existir
    sys.modules["boto3"] = types.ModuleType("boto3")

import nucleo  # noqa: E402

PASTA = Path(__file__).resolve().parent.parent / "exemplos"
ONTEM = date.today() - timedelta(days=1)


def test_formatos_de_arquivo():
    esperado = [("Clientes", {"tablename": "clientes"}), ("Contratos", {"tablename": "contratos"})]
    py = 'lista = [("Clientes", {"tablename": "clientes"}), ("Contratos", {"tablename": "contratos"})]'
    assert nucleo.carregar_payloads(py, "x.py") == esperado
    assert nucleo.carregar_payloads('[("Clientes", {"tablename": "clientes"}), ("Contratos", {"--tablename": "contratos"})]', "x.txt") == esperado
    assert nucleo.carregar_payloads('[["Clientes", {"tablename": "clientes"}], ["Contratos", {"tablename": "contratos"}]]', "x.json") == esperado
    assert nucleo.carregar_payloads('[{"nome": "Clientes", "payload": {"tablename": "clientes"}}, {"name": "Contratos", "payload": {"tablename": "contratos"}}]', "x.json") == esperado
    assert nucleo.carregar_payloads('{"Clientes": {"tablename": "clientes"}, "Contratos": {"tablename": "contratos"}}', "x.json") == esperado
    assert nucleo.carregar_payloads('[{"_nome": "Clientes", "tablename": "clientes"}, {"_nome": "Contratos", "tablename": "contratos"}]', "x.json") == esperado
    assert nucleo.carregar_payloads("- [Clientes, {tablename: clientes}]\n- [Contratos, {tablename: contratos}]\n", "x.yaml") == esperado
    assert nucleo.carregar_payloads("nome;tablename;dt_ref\nClientes;clientes;\nContratos;contratos;\n", "x.csv") == esperado
    assert nucleo.carregar_payloads((PASTA / "payloads.py").read_text("utf-8"), "payloads.py")[2] == \
        ("Vendas", {"tablename": "vendas", "database": "trusted", "dt_ref": 20261029})


def test_xlsx():
    import io
    import pandas as pd

    buffer = io.BytesIO()
    pd.DataFrame({"Nome": ["Clientes", "Contratos"], "tablename": ["clientes", "contratos"], "dt_ref": [None, "20261029"]}).to_excel(buffer, index=False)
    assert nucleo.carregar_payloads(buffer.getvalue(), "x.xlsx") == [
        ("Clientes", {"tablename": "clientes"}), ("Contratos", {"tablename": "contratos", "dt_ref": "20261029"})]


def test_nomes_repetidos_e_sem_nome():
    lista = nucleo.carregar_payloads('[["A", {"t": 1}], ["A", {"t": 2}], {"t": 3, "x": "y"}]', "x.json")
    assert [n for n, _ in lista] == ["A", "A (2)", "t=3, x=y"]


def test_arquivo_invalido():
    for texto, nome in (("[]", "x.json"), ("print('oi')", "x.py"), ('[1, 2]', "x.json")):
        try:
            nucleo.carregar_payloads(texto, nome)
        except (ValueError, SyntaxError):
            continue
        raise AssertionError(f"deveria falhar: {texto}")


def _avaliar(opcoes=None, erros=None, dia=ONTEM):
    lista = nucleo.carregar_payloads((PASTA / "payloads.json").read_bytes(), "payloads.json")
    return lista, nucleo.executar_avaliacao(lista, "meu_job", dia, opcoes=opcoes, dicionario_erros=erros,
                                            regiao="sa-east-1", simulado=True)


def test_uma_linha_por_carga_com_todos_os_resultados():
    lista, r = _avaliar()
    quadro = r["quadro"]
    assert len(quadro) == len(lista) and set(quadro["name"]) == {n for n, _ in lista}
    assert set(quadro["resultado_operacional"]) == set(nucleo.ORDEM_RESULTADOS) - {"INDETERMINADO"}
    # problemas primeiro
    assert list(quadro["_ordem_resultado"]) == sorted(quadro["_ordem_resultado"])
    sem = quadro[quadro["resultado_operacional"] == nucleo.SEM_EXECUCAO].iloc[0]
    assert sem["execucoes_no_dia"] == 0 and pd_isna(sem["job_run_id"])
    # 3 execucoes avulsas nao batem com nenhum payload
    assert r["total_execucoes_dia"] == r["historico"].shape[0] + 3


def test_ultima_execucao_e_historico():
    _, r = _avaliar()
    quadro, historico = r["quadro"], r["historico"]
    reprocessada = quadro[quadro["execucoes_no_dia"] == 2]
    assert not reprocessada.empty
    for _, linha in reprocessada.iterrows():
        execs = historico[historico["name"] == linha["name"]].sort_values("started_on")
        assert execs.iloc[-1]["job_run_id"] == linha["job_run_id"]
        assert linha["started_on"] == execs.iloc[-1]["started_on"]


def test_falso_positivo_alerta_e_erro_so_no_log():
    _, r = _avaliar()
    fp = r["quadro"][r["quadro"]["resultado_operacional"] == "FALSO_POSITIVO"].iloc[0]
    assert fp["status_aws"] == "SUCCEEDED" and fp["qt_erros_log"] >= 1 and "target table has" in fp["detalhe_erro"]
    al = r["quadro"][r["quadro"]["resultado_operacional"] == "ALERTA"].iloc[0]
    assert al["qt_warnings_log"] >= 1
    so_log = r["quadro"][(r["quadro"]["status_aws"] == "FAILED") & (r["quadro"]["qt_erros_log"] > 0)]
    assert not so_log.empty and so_log.iloc[0]["detalhe_erro"].startswith("ERROR")


def test_dicionario_de_erros_e_diagnostico():
    erros = json.loads((PASTA / "dicionario_erros.json").read_text("utf-8"))
    _, r = _avaliar(erros=erros)
    com_erro = r["quadro"][r["quadro"]["detalhe_erro"].notna()]
    assert not com_erro.empty
    assert (com_erro["erro_categoria"] != "NAO_CLASSIFICADO").all()
    timeout = com_erro[com_erro["status_aws"] == "TIMEOUT"].iloc[0]
    assert timeout["erro_categoria"] == "TIMEOUT" and timeout["tipo_erro"] == "TIMEOUT"
    assert "timeout" in timeout["diagnostico"].lower()


def test_sem_ler_logs_sucesso_vira_positivo():
    _, r = _avaliar(opcoes={"avaliar_Logs_em_sucesso": False})
    assert not r["quadro"]["resultado_operacional"].isin(["ALERTA", "FALSO_POSITIVO"]).any()


def test_chaves_comparacao():
    # comparando so "database", todos os payloads batem com todas as execucoes deles e de outros
    _, r = _avaliar(opcoes={"chaves_comparacao": ["database"]})
    assert (r["quadro"]["resultado_operacional"] != nucleo.SEM_EXECUCAO).all()


def test_modo_real_sem_execucoes_no_dia():
    """Caminho sem simulador: usa a sessao padrao do boto3 (aqui, um falso vazio)."""
    chamadas = {}

    class BotoVazio:
        def setup_default_session(self, **kw):
            chamadas.update(kw)

        def client(self, servico, *a, **k):
            return types.SimpleNamespace(get_paginator=lambda _: types.SimpleNamespace(paginate=lambda **_: iter([{"JobRuns": []}])))

    original = nucleo.fg.boto3
    nucleo.fg.boto3 = BotoVazio()
    try:
        r = nucleo.executar_avaliacao([("X", {"tablename": "x"})], "meu_job", ONTEM, perfil="prd", regiao="sa-east-1")
    finally:
        nucleo.fg.boto3 = original
    assert chamadas == {"profile_name": "prd", "region_name": "sa-east-1"}
    assert list(r["quadro"]["resultado_operacional"]) == [nucleo.SEM_EXECUCAO]
    assert r["total_execucoes_dia"] == 0 and r["historico"].empty


def test_link_console_e_duracao():
    assert nucleo.link_console("sa-east-1", "meu job", "jr_1").endswith("#/job/meu%20job/run/jr_1")
    assert nucleo.link_console(None, "j", "jr") is None
    assert nucleo.formatar_duracao(3725) == "1h02m" and nucleo.formatar_duracao(65) == "1m05s"
    assert nucleo.formatar_duracao(None) == ""


def pd_isna(v):
    import pandas as pd

    return v is None or pd.isna(v)


if __name__ == "__main__":
    falhas = 0
    for nome, fn in list(globals().items()):
        if nome.startswith("test_"):
            try:
                fn()
                print(f"ok    {nome}")
            except Exception as e:
                falhas += 1
                print(f"FALHA {nome}: {type(e).__name__}: {e}")
    sys.exit(1 if falhas else 0)
