"""Perfis de sócio pessoa física: ocupação, faixa de renda e faixa etária.

## Por que isto existe

Sem estes campos, toda pessoa da base é indistinguível de qualquer outra: mesmo
formato de nome, mesma quantidade de contas, nenhum atributo que diferencie.
Isso aparece na tela de dois jeitos ruins:

- a busca por um primeiro nome comum devolve dez linhas com a **mesma nota de
  relevância**, porque todas casam do mesmo jeito no mesmo campo. A nota deixa de
  ordenar coisa alguma e vira ruído numérico;
- o painel do nó não tem nada para dizer sobre o sócio além do nome, e
  o analista não consegue julgar se aquele vínculo faz sentido.

Um cadastro de verdade tem perfil. Um sócio administrador de 55 anos com renda
alta e um estudante de 22 sem renda declarada aparecem no mesmo lugar da cadeia
societária e significam coisas diferentes — sócio laranja é exatamente esse
descompasso entre o papel na empresa e o perfil da pessoa. Nenhuma query da POV
usa o perfil: ele existe para o analista **ler** o grupo depois de encontrá-lo. Se
entrasse na busca, a demonstração estaria se autoconfirmando.

## Como é atribuído

Deterministicamente, a partir do `seed_index` da pessoa. Nada de `random` sem
semente: rodar de novo tem que produzir exatamente a mesma base, que é a mesma
invariante do resto do gerador.

A distribuição é enviesada de propósito para parecer uma carteira de banco de
varejo — muito assalariado e autônomo, pouca gente de renda alta —, e as redes de
grupos de vitrine recebem o mesmo perfil da população: nenhuma query usa perfil.
"""
from __future__ import annotations

import hashlib

# (rótulo, peso). Pesos são inteiros para a escolha ser determinística por módulo.
OCUPACOES: list[tuple[str, int]] = [
    ("assalariado", 30),
    ("autonomo", 22),
    ("aposentado", 12),
    ("servidor publico", 8),
    ("estudante", 8),
    ("empresario", 6),
    ("profissional liberal", 6),
    ("desempregado", 5),
    ("sem ocupacao declarada", 3),
]

FAIXAS_RENDA: list[tuple[str, int]] = [
    ("ate 2 SM", 34),
    ("2 a 5 SM", 30),
    ("5 a 10 SM", 18),
    ("10 a 20 SM", 10),
    ("acima de 20 SM", 4),
    ("nao declarada", 4),
]

FAIXAS_ETARIAS: list[tuple[str, int]] = [
    ("18-24", 14),
    ("25-34", 27),
    ("35-44", 24),
    ("45-59", 22),
    ("60+", 13),
]


def _escolhe(opcoes: list[tuple[str, int]], semente: str) -> str:
    """Escolha determinística e ponderada. Mesmo `semente`, mesmo resultado."""
    total = sum(peso for _, peso in opcoes)
    # sha1 do texto em vez de `hash()`: o `hash()` do Python é aleatorizado por
    # processo desde a 3.3, e a base deixaria de ser reproduzível entre execuções.
    n = int(hashlib.sha1(semente.encode()).hexdigest(), 16) % total
    for rotulo, peso in opcoes:
        if n < peso:
            return rotulo
        n -= peso
    return opcoes[-1][0]


def perfil(seed_index: int) -> dict[str, str]:
    """Perfil da população legítima."""
    s = str(seed_index)
    return {
        "occupation": _escolhe(OCUPACOES, "ocup" + s),
        "income_band": _escolhe(FAIXAS_RENDA, "renda" + s),
        "age_band": _escolhe(FAIXAS_ETARIAS, "idade" + s),
    }
