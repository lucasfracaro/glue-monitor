# Glue Monitor

Painel em Streamlit que mostra o resultado da **última execução do dia** de cada carga de um
AWS Glue Job genérico, aquele job único que recebe um dicionário de parâmetros por execução
e carrega uma tabela diferente a cada chamada.

Você sobe um arquivo com a lista de cargas `(apelido, payload)`. O painel lista as execuções
do job no dia uma vez só, associa cada execução à carga cujo payload ela atende, pega a mais
recente de cada carga e classifica o resultado lendo o log no CloudWatch.

A avaliação é feita por `avaliar_job_glue_visual`, de `funcoes_glue.py` do
[aws-data-utils](https://github.com/lucasfracaro/aws-data-utils) (cópia em `vendor/`).

## Rodar

```bash
pip install -r requirements.txt
streamlit run app.py
```

Para ver o painel sem AWS, ligue **Modo demonstração** na barra lateral
(ou `GLUE_MONITOR_DEMO=1 streamlit run app.py`).

As credenciais seguem a cadeia padrão do boto3 (variáveis de ambiente, `~/.aws`, SSO). O
perfil e a região escolhidos na barra lateral viram a sessão padrão do boto3.

Permissões necessárias: `glue:GetJobRuns`, `glue:GetJobRun` e `logs:GetLogEvents`.

Valores padrão por variável de ambiente: `GLUE_JOB_NAME`, `AWS_REGION`, `AWS_PROFILE`.

## Arquivo de cargas

O apelido de cada carga é o nome que aparece no painel. O jeito mais simples é subir o mesmo
`.py` que você já usa com `avaliar_job_glue_por_lista_payloads_nomeados`:

```python
lista_payloads = [
    ("Clientes (D-1)", {"tablename": "tb_cli_raw_001", "database": "trusted"}),
    ("Contratos ativos", {"tablename": "tb_ctr_raw_007", "database": "trusted"}),
]
```

O arquivo só é lido com `ast.literal_eval`: nada nele é executado. Outros formatos aceitos:

| Formato | Exemplo |
|---|---|
| JSON | `[["Clientes", {"tablename": "clientes"}]]`, `[{"nome": "Clientes", "payload": {...}}]` ou `{"Clientes": {...}}` |
| JSONL / YAML | os mesmos formatos do JSON |
| CSV / Excel | coluna `nome` com o apelido + uma coluna por chave do payload (células vazias são ignoradas) |

- Chaves com `--` são aceitas; o prefixo é removido, como nos argumentos do Glue.
- Sem apelido, o nome é montado com as duas primeiras chaves do payload.
- Apelidos repetidos ganham o sufixo `(2)`, porque as execuções são agrupadas pelo nome.

Há exemplos em [`exemplos/`](exemplos).

## Como uma execução é associada a uma carga

Cada chave do payload é comparada, como texto normalizado (`20260927.0` = `"20260927"`), com o
argumento de mesmo nome da execução. Argumentos extras na execução são ignorados. As regras
de `funcoes_glue` ficam em **Regras de avaliação** na barra lateral:

| Opção | Parâmetro |
|---|---|
| Comparar só algumas chaves | `chaves_comparacao` |
| Ignorar chaves ausentes na execução | `ignorar_chaves_vazias_no_run` |
| Exigir ao menos uma chave comparada | `exigir_ao_menos_um_filtro_aplicado` |
| Permitir uma execução em mais de uma carga | `permitir_multiplos_matches` |
| Ler log das execuções com sucesso / das falhas sem mensagem | `avaliar_Logs_em_sucesso` / `avaliar_Logs_em_falha` |

## Resultados

| Painel | Quando |
|---|---|
| ❌ Fracasso | FAILED, TIMEOUT, STOPPED, ERROR, EXPIRED |
| 🟠 Falso positivo | SUCCEEDED, mas com erro no log |
| ❔ Indeterminado | status desconhecido ou falha ao ler o log |
| ⚠️ Alerta | SUCCEEDED com WARN no log |
| ⚪ Sem execução | nenhuma execução do dia atendeu o payload |
| 🔄 Em execução | STARTING, RUNNING, WAITING, STOPPING |
| ✅ Positivo | SUCCEEDED sem erro nem aviso no log |

Ao clicar numa carga aparecem o payload, as chaves ignoradas na comparação, o erro completo, um
diagnóstico (`classificar_erro_detalhado` + `gerar_diagnostico`) e todas as execuções do dia
daquela carga. A tabela pode ser filtrada e baixada em CSV.

O **Dicionário de erros** (opcional, JSON) traduz trechos de mensagem em categoria, resumo,
impacto e ação sugerida; [`exemplos/dicionario_erros.json`](exemplos/dicionario_erros.json) serve
de ponto de partida.

## Desempenho

As execuções do dia são listadas uma única vez, e todas as cargas são comparadas em memória.
As chamadas extras à AWS são só para ler o log da última execução de cada carga. O resultado
fica em cache por 60 s; há atualização automática opcional (1, 5 ou 15 min).

## Estrutura

```
app.py          a tela (Streamlit)
nucleo.py       leitura do arquivo de cargas, chamada a funcoes_glue, montagem do quadro
simulador.py    imita os clientes glue e logs do boto3 (modo demonstração e testes)
vendor/         funcoes_glue.py, datas.py e utils_gerais.py do aws-data-utils
exemplos/       arquivos de cargas e dicionário de erros de exemplo
tests/          python -m pytest -q
```

## Licença

MIT © 2026 Lucas Fracaro. Veja [LICENSE](LICENSE).
