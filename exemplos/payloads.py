# Exemplo de arquivo .py aceito pelo painel: a mesma lista que voce passa para
# avaliar_job_glue_por_lista_payloads_nomeados. O painel so le a lista com
# ast.literal_eval; nada deste arquivo e executado.
lista_payloads = [
    ("Clientes", {"tablename": "clientes", "database": "trusted"}),
    ("Contratos", {"tablename": "contratos", "database": "trusted"}),
    ("Vendas", {"tablename": "vendas", "database": "trusted", "dt_ref": 20261029}),
]
