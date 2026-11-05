"""Testes da configuracao salva (rodar: python -m pytest -q)."""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import configuracao  # noqa: E402


def _novo_diretorio():
    pasta = tempfile.mkdtemp(prefix="glue-monitor-cfg-")
    os.environ["GLUE_MONITOR_CONFIG_DIR"] = pasta
    return Path(pasta)


def test_diretorio_padrao_e_variaveis():
    antigo = {k: os.environ.pop(k, None) for k in ("GLUE_MONITOR_CONFIG_DIR", "XDG_CONFIG_HOME")}
    try:
        assert configuracao.diretorio() == Path.home() / ".config" / "glue-monitor"
        os.environ["XDG_CONFIG_HOME"] = "/tmp/xdg"
        assert configuracao.diretorio() == Path("/tmp/xdg/glue-monitor")
        os.environ["GLUE_MONITOR_CONFIG_DIR"] = "/tmp/meu-cfg"
        assert configuracao.diretorio() == Path("/tmp/meu-cfg")
    finally:
        for k, v in antigo.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v


def test_salvar_e_carregar():
    pasta = _novo_diretorio()
    assert configuracao.carregar() == {}
    assert configuracao.salvar({"nome_job": "job_x", "regiao": "sa-east-1", "logs_sucesso": False}) is True
    assert configuracao.salvar({"nome_job": "job_x"}) is False  # nada mudou: nao regrava
    assert configuracao.salvar({"perfil": "prd"}) is True       # mescla com o que ja existia
    assert configuracao.carregar() == {"nome_job": "job_x", "regiao": "sa-east-1", "logs_sucesso": False, "perfil": "prd"}
    assert json.loads((pasta / "config.json").read_text("utf-8"))["perfil"] == "prd"


def test_config_corrompida_vira_vazia():
    pasta = _novo_diretorio()
    (pasta / "config.json").write_text("{nao é json", "utf-8")
    assert configuracao.carregar() == {}
    (pasta / "config.json").write_text("[1, 2]", "utf-8")
    assert configuracao.carregar() == {}
    assert configuracao.salvar({"a": 1}) and configuracao.carregar() == {"a": 1}


def test_arquivos_salvos():
    _novo_diretorio()
    assert configuracao.arquivo_salvo("payloads") is None
    assert configuracao.salvar_arquivo("payloads", "lista.json", b"[1]") is True
    assert configuracao.salvar_arquivo("payloads", "lista.json", b"[1]") is False
    salvo = configuracao.arquivo_salvo("payloads")
    assert (salvo["nome"], salvo["conteudo"]) == ("lista.json", b"[1]")
    # outro arquivo substitui o anterior
    configuracao.salvar_arquivo("payloads", "/algum/caminho/cargas.py", b"x = []")
    salvo = configuracao.arquivo_salvo("payloads")
    assert (salvo["nome"], salvo["conteudo"]) == ("cargas.py", b"x = []")
    # tipos independentes
    configuracao.salvar_arquivo("erros", "erros.json", b"{}")
    configuracao.remover_arquivo("payloads")
    assert configuracao.arquivo_salvo("payloads") is None
    assert configuracao.arquivo_salvo("erros")["nome"] == "erros.json"
    try:
        configuracao.salvar_arquivo("outro", "x", b"")
    except ValueError:
        pass
    else:
        raise AssertionError("tipo invalido deveria falhar")


def test_limpar():
    _novo_diretorio()
    configuracao.salvar({"nome_job": "x"})
    configuracao.salvar_arquivo("payloads", "a.json", b"[]")
    configuracao.salvar_arquivo("erros", "e.json", b"{}")
    configuracao.limpar()
    assert configuracao.carregar() == {}
    assert configuracao.arquivo_salvo("payloads") is None and configuracao.arquivo_salvo("erros") is None
    configuracao.limpar()  # sem nada salvo tambem funciona


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
