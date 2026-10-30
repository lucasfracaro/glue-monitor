"""
Simulador do boto3 para o painel: imita os clientes "glue" e "logs" usados por
funcoes_glue (get_job_runs, get_job_run, get_log_events), para testar o painel
sem AWS. As funcoes reais de funcoes_glue rodam sem alteracao por cima dele.
"""
from __future__ import annotations

import contextlib
import random
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

# (estado AWS, tipo de log) em ordem cronologica; [] = sem execucao no dia
CENARIOS = [
    [("SUCCEEDED", "limpo")],
    [("FAILED", "erro_aws")],
    [("SUCCEEDED", "warn")],                         # -> ALERTA
    [("RUNNING", "limpo")],
    [("FAILED", "erro_aws"), ("SUCCEEDED", "limpo")],  # falhou e foi reprocessado
    [("SUCCEEDED", "erro")],                         # -> FALSO_POSITIVO
    [],
    [("TIMEOUT", "erro_aws")],
    [("SUCCEEDED", "limpo"), ("SUCCEEDED", "limpo")],
    [("FAILED", "sem_mensagem")],                    # erro so no log
    [("FAILED", "erro_aws"), ("SUCCEEDED", "erro"), ("SUCCEEDED", "limpo")],  # 3 tentativas
    [("STOPPED", "erro_aws")],
]

ERROS_AWS = [
    "AnalysisException: Path does not exist: s3://datalake-raw/{tabela}/dt=20261029/",
    "An error occurred while calling o112.pyWriteDynamicFrame. Access Denied (Service: Amazon S3; Status Code: 403)",
    "AnalysisException: cannot resolve 'id_cliente' given input columns: [id, nome , dt]",
    "Py4JJavaError: java.lang.OutOfMemoryError: GC overhead limit exceeded",
]


class _Glue:
    def __init__(self, runs):
        self._runs = runs

    def get_paginator(self, nome):
        assert nome == "get_job_runs"
        return self

    def paginate(self, JobName, **_):
        runs = sorted(self._runs.get(JobName, []), key=lambda r: r["StartedOn"], reverse=True)
        for i in range(0, max(len(runs), 1), 50):
            yield {"JobRuns": runs[i:i + 50]}

    def get_job_run(self, JobName, RunId, **_):
        for r in self._runs.get(JobName, []):
            if r["Id"] == RunId:
                return {"JobRun": r}
        raise KeyError(RunId)


class _Logs:
    def __init__(self, logs):
        self._logs = logs

    def get_log_events(self, logGroupName, logStreamName, startFromHead=True, nextToken=None, **_):
        if not logGroupName.endswith("/output"):
            raise RuntimeError(f"ResourceNotFoundException: {logGroupName}")
        eventos = [] if nextToken else self._logs.get(logStreamName, [])
        return {"events": eventos, "nextForwardToken": "f/fim"}


class Boto3Simulado:
    """Substituto do modulo boto3 com execucoes ficticias para cada payload."""

    def __init__(self, lista_payloads, nome_job, dia: date, fuso="America/Sao_Paulo", semente=7):
        rng = random.Random(semente)
        tz = ZoneInfo(fuso)
        inicio = datetime.combine(dia, time.min, tzinfo=tz)
        agora = datetime.now(tz)
        limite = min(inicio + timedelta(days=1), agora) if agora > inicio else inicio + timedelta(days=1)
        janela = max((limite - inicio).total_seconds(), 900)
        self.runs: dict[str, list[dict]] = {nome_job: []}
        self.logs: dict[str, list[dict]] = {}

        def novo_run(argumentos, estado, tipo_log, comeco, tabela):
            duracao = timedelta(seconds=rng.uniform(0.02, 0.12) * janela)
            run_id = "jr_" + "".join(rng.choices("0123456789abcdef", k=64))
            run = {
                "Id": run_id, "Attempt": 0, "JobName": nome_job, "JobRunState": estado,
                "StartedOn": comeco.astimezone(timezone.utc), "Arguments": argumentos,
                "LogGroupName": "/aws-glue/jobs", "WorkerType": "G.1X", "NumberOfWorkers": 2,
                "GlueVersion": "4.0",
            }
            if estado not in ("RUNNING", "STARTING", "WAITING"):
                run["CompletedOn"] = (comeco + duracao).astimezone(timezone.utc)
                run["ExecutionTime"] = int(duracao.total_seconds())
                run["DPUSeconds"] = run["ExecutionTime"] * 2.0
            if tipo_log == "erro_aws":
                run["ErrorMessage"] = (
                    "Job run timed out" if estado == "TIMEOUT"
                    else "Job run stopped by user" if estado == "STOPPED"
                    else rng.choice(ERROS_AWS).format(tabela=tabela)
                )
            ms = int(comeco.timestamp() * 1000)
            linhas = [f"INFO Iniciando carga {tabela}", f"INFO Lidos {rng.randint(1000, 90000)} registros"]
            if tipo_log == "warn":
                linhas.append(f"WARN Coluna 'dt_atualizacao' nula em {rng.randint(1, 40)} linhas")
            if tipo_log in ("erro", "sem_mensagem"):
                linhas.append(f"ERROR Falha ao inserir na tabela trusted.{tabela}: target table has 12 columns but inserted data has 11 columns")
            linhas.append("INFO Fim do processamento")
            self.logs[run_id] = [{"timestamp": ms + i * 1000, "message": m, "ingestionTime": ms + i * 1000 + 50}
                                 for i, m in enumerate(linhas)]
            self.runs[nome_job].append(run)
            return comeco + duracao

        for i, (_, payload) in enumerate(lista_payloads):
            argumentos = {f"--{k}": str(v) for k, v in payload.items()}
            argumentos["--enable-metrics"] = "true"
            tabela = str(next(iter(payload.values()), "tabela")).split(".")[-1].strip("/").split("/")[-1] or "tabela"
            comeco = inicio + timedelta(seconds=rng.uniform(0.05, 0.45) * janela)
            for estado, tipo_log in CENARIOS[(i * 5 + semente) % len(CENARIOS)]:
                fim = novo_run(dict(argumentos), estado, tipo_log, comeco, tabela)
                comeco = fim + timedelta(seconds=rng.uniform(0.01, 0.04) * janela)

        # execucoes de outras cargas do mesmo job, que nao batem com nenhum payload
        for j in range(3):
            novo_run({"--tablename": f"carga_avulsa_{j}"}, "SUCCEEDED", "limpo",
                     inicio + timedelta(seconds=rng.uniform(0.05, 0.8) * janela), f"carga_avulsa_{j}")

    def client(self, servico, *args, **kwargs):
        if servico == "glue":
            return _Glue(self.runs)
        if servico == "logs":
            return _Logs(self.logs)
        raise ValueError(servico)


@contextlib.contextmanager
def boto3_simulado(modulo, lista_payloads, nome_job, dia, fuso="America/Sao_Paulo"):
    """Troca temporariamente o boto3 usado por `modulo` (ex.: funcoes_glue) pelo simulador."""
    original = modulo.boto3
    modulo.boto3 = Boto3Simulado(lista_payloads, nome_job, dia, fuso)
    try:
        yield modulo.boto3
    finally:
        modulo.boto3 = original
