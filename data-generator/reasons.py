"""Pool de textos livres para `transactions.reason_text`.

Existe um par semanticamente equivalente por grupo. Isso é o que sustenta o
passo 6 do roteiro: dois textos com palavras diferentes que o Atlas Search por
igualdade/termo não conecta, mas o Vector Search conecta.
"""

# Cada tupla é um grupo semântico. Índice 0 e 1 dizem a mesma coisa com palavras
# diferentes; são o par usado na demo.
SEMANTIC_GROUPS: list[tuple[str, ...]] = [
    (
        "pagamento de aluguel do apartamento",
        "quitacao da mensalidade do imovel alugado",
        "aluguel referente ao mes corrente",
    ),
    (
        "adiantamento para fornecedor de materiais",
        "sinal antecipado ao vendedor de insumos",
        "pagamento parcial a fornecedor",
    ),
    (
        "reembolso de despesas de viagem",
        "devolucao de gastos com deslocamento a trabalho",
        "ressarcimento de diarias",
    ),
    (
        "emprestimo entre familiares",
        "ajuda financeira para parente proximo",
        "dinheiro emprestado para a familia",
    ),
    (
        "pagamento de servico de consultoria",
        "honorarios por assessoria prestada",
        "fatura de consultoria tecnica",
    ),
    (
        "compra de equipamento de informatica",
        "aquisicao de computador e perifericos",
        "pagamento de notebook novo",
    ),
    (
        "transferencia para conta propria",
        "movimentacao entre contas do mesmo titular",
        "envio de recursos para minha outra conta",
    ),
    (
        "pagamento de curso e material didatico",
        "mensalidade escolar e apostilas",
        "matricula em curso profissionalizante",
    ),
]

# Textos usados dentro das redes de fraude. Deliberadamente vagos e repetitivos:
# é assim que "motivo" costuma aparecer em estruturação real.
RING_GROUPS: list[tuple[str, ...]] = [
    (
        "acerto de contas entre socios",
        "divisao de valores combinada entre parceiros",
        "repasse acordado entre os socios",
    ),
    (
        "antecipacao de recebivel de venda",
        "adiantamento sobre faturamento futuro",
        "antecipacao de valores a receber",
    ),
    (
        "pagamento de servico prestado sem contrato",
        "remuneracao por trabalho informal",
        "valor combinado por servico avulso",
    ),
]

ALL_LEGIT = [t for g in SEMANTIC_GROUPS for t in g]
ALL_RING = [t for g in RING_GROUPS for t in g]
ALL_TEXTS = ALL_LEGIT + ALL_RING

# Par canônico da demo: mesmo significado, zero palavras relevantes em comum.
DEMO_PAIR = (SEMANTIC_GROUPS[0][0], SEMANTIC_GROUPS[0][1])
