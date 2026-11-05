"""
Configuracao salva do painel: guarda as escolhas da barra lateral e uma copia
dos arquivos enviados num diretorio local, para o painel reabrir como estava.

Local: $GLUE_MONITOR_CONFIG_DIR, senao $XDG_CONFIG_HOME/glue-monitor,
senao ~/.config/glue-monitor. Nao guarda credenciais, so o nome do perfil AWS.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

ARQUIVO_CONFIG = "config.json"
TIPOS_ARQUIVO = ("payloads", "erros")  # arquivo de cargas e dicionario de erros


def diretorio() -> Path:
    if os.getenv("GLUE_MONITOR_CONFIG_DIR"):
        return Path(os.environ["GLUE_MONITOR_CONFIG_DIR"]).expanduser()
    base = os.getenv("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base).expanduser() / "glue-monitor"


def _escrever(caminho: Path, conteudo: bytes) -> None:
    """Escrita atomica: um arquivo pela metade nunca fica no lugar do anterior."""
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=caminho.parent, delete=False) as tmp:
        tmp.write(conteudo)
    os.replace(tmp.name, caminho)


def carregar() -> dict:
    """Configuracao salva, ou {} se nao houver (ou se estiver corrompida)."""
    try:
        dados = json.loads((diretorio() / ARQUIVO_CONFIG).read_text("utf-8"))
        return dados if isinstance(dados, dict) else {}
    except (OSError, ValueError):
        return {}


def salvar(config: dict) -> bool:
    """Grava a configuracao se mudou. Devolve True se gravou."""
    atual = carregar()
    novo = {**atual, **config}
    if novo == atual:
        return False
    _escrever(diretorio() / ARQUIVO_CONFIG,
              json.dumps(novo, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"))
    return True


# --------------------------------------------------------------------------- #
# Copias dos arquivos enviados
# --------------------------------------------------------------------------- #
def _pasta_arquivo(tipo: str) -> Path:
    if tipo not in TIPOS_ARQUIVO:
        raise ValueError(tipo)
    return diretorio() / "arquivos" / tipo


def salvar_arquivo(tipo: str, nome: str, conteudo: bytes) -> bool:
    """Guarda a copia do arquivo enviado (substitui a anterior). True se mudou."""
    salvo = arquivo_salvo(tipo)
    nome = Path(nome).name or "arquivo"
    if salvo and salvo["nome"] == nome and salvo["conteudo"] == conteudo:
        return False
    pasta = _pasta_arquivo(tipo)
    if pasta.exists():
        shutil.rmtree(pasta)
    _escrever(pasta / nome, conteudo)
    return True


def arquivo_salvo(tipo: str) -> dict | None:
    """{'nome', 'conteudo', 'salvo_em'} do arquivo guardado, ou None."""
    pasta = _pasta_arquivo(tipo)
    try:
        arquivos = [p for p in pasta.iterdir() if p.is_file() and not p.name.startswith(".")]
    except OSError:
        return None
    if not arquivos:
        return None
    caminho = max(arquivos, key=lambda p: p.stat().st_mtime)
    return {
        "nome": caminho.name,
        "conteudo": caminho.read_bytes(),
        "salvo_em": datetime.fromtimestamp(caminho.stat().st_mtime),
    }


def remover_arquivo(tipo: str) -> None:
    shutil.rmtree(_pasta_arquivo(tipo), ignore_errors=True)


def limpar() -> None:
    """Apaga toda a configuracao salva (volta aos padroes)."""
    (diretorio() / ARQUIVO_CONFIG).unlink(missing_ok=True)
    for tipo in TIPOS_ARQUIVO:
        remover_arquivo(tipo)
