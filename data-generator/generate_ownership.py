#!/usr/bin/env python3
"""Gera a base societária: empresas, participações e exposição de crédito.

## O padrão que esta base representa

Não é uma rede densa para exploração analítica — é uma **árvore rasa consultada
por chave de negócio**. O banco pergunta "dado este CNPJ, qual é o grupo econômico
e quanto o grupo já deve para nós?", responde em poucos saltos, e faz isso algumas
milhares de vezes por dia sobre uma base de dezenas de milhões de registros.

Esse padrão importa porque é onde o gargalo do cliente **não** é o algoritmo de
grafo: é carregar o volume e operar a infraestrutura. A tabela de critério está
no `README.md`.

## Topologia, e por que ela é assim

Três populações, e as proporções foram escolhidas para a demo ter o que mostrar
sem deixar de parecer um cadastro real:

1. **Empresas isoladas ou de sociedade simples** (a maioria): um a três sócios
   pessoa física, nenhuma participação em outra empresa. É o caso comum, e é o
   que faz o passo 1 da demo — "esta empresa, sozinha, parece pequena".
2. **Grupos econômicos**: uma holding no topo, subsidiárias abaixo, e às vezes um
   segundo nível de subsidiárias. Profundidade 2 a 4, que é a faixa em que a
   consulta real vive.
3. **Uma cadeia longa por lote**, com até `--max-chain` níveis. Existe para
   provar que o mecanismo aguenta profundidade, não porque seja típico — e o
   roteiro diz isso em voz alta.

O vínculo que faz a demo funcionar é o **sócio em comum**: uma pessoa física que
participa de empresas em grupos diferentes. É assim que um analista descobre que
duas empresas aparentemente independentes respondem ao mesmo dono — e é a
pergunta que o `$graphLookup` responde subindo e descendo a árvore.

## Determinismo

Mesmo contrato do resto do gerador: todo `_id` vem de `det_id`, a semente é fixa,
e rodar duas vezes reescreve os mesmos documentos em vez de duplicar.

Uso:
    python generate_ownership.py                      # volume padrão
    python generate_ownership.py --companies 200000   # volume reduzido
"""
from __future__ import annotations

import argparse
import json
import random
import time
from datetime import datetime, timedelta, timezone

from faker import Faker

from common import bulk_insert, bulk_replace, det_id, get_db

SEED = 20260827
NOW = datetime(2026, 8, 27, tzinfo=timezone.utc)

UFS = ["SP", "RJ", "MG", "RS", "PR", "SC", "BA", "PE", "CE", "GO", "DF", "ES"]

# (código, descrição, setor, peso) — `_peso` lê o peso na última posição.
#
# O `setor` não vai para o banco: serve só para o gerador construir grupos que
# parecem diversificados no CNAE e são concentrados no negócio. É esse contraste
# que a análise semântica do painel encontra sozinha.
CNAES: list[tuple[str, str, str, int]] = [
    ("4120400", "construcao de edificios residenciais", "construcao", 7),
    ("4211101", "construcao de rodovias e ferrovias", "construcao", 4),
    ("4399103", "obras de alvenaria e acabamento", "construcao", 5),
    ("7112000", "servicos de engenharia e projeto de obras", "construcao", 4),
    ("6810201", "compra e venda de imoveis proprios", "imobiliario", 6),
    ("6822600", "gestao e intermediacao de imoveis de terceiros", "imobiliario", 4),
    ("6810203", "loteamento e incorporacao de imoveis", "imobiliario", 3),
    ("4711302", "comercio varejista de mercadorias em geral", "varejo", 8),
    ("4712100", "minimercados e mercearias", "varejo", 5),
    ("4781400", "comercio varejista de vestuario", "varejo", 5),
    ("4649408", "comercio atacadista de produtos de higiene", "varejo", 4),
    ("4930202", "transporte rodoviario de carga", "logistica", 6),
    ("5211701", "armazens gerais e deposito de mercadorias", "logistica", 3),
    ("5320202", "servicos de entrega rapida e encomendas", "logistica", 3),
    ("6204000", "consultoria em tecnologia da informacao", "servicos", 7),
    ("7020400", "consultoria em gestao empresarial", "servicos", 6),
    ("6920601", "servicos de contabilidade e auditoria", "servicos", 4),
    ("6911701", "servicos advocaticios", "servicos", 3),
    ("1091102", "fabricacao de produtos de padaria e confeitaria", "industria", 4),
    ("2512800", "fabricacao de esquadrias e estruturas de metal", "industria", 4),
    ("2229302", "fabricacao de artefatos de material plastico", "industria", 3),
    ("1052000", "fabricacao de laticinios e derivados do leite", "industria", 3),
    ("5611201", "restaurantes e estabelecimentos de alimentacao", "alimentacao", 6),
    ("5620104", "fornecimento de refeicoes coletivas", "alimentacao", 3),
    ("5611203", "lanchonetes e casas de cha", "alimentacao", 4),
    ("8630501", "atividade medica ambulatorial", "saude", 5),
    ("8650003", "atividades de fisioterapia e reabilitacao", "saude", 3),
    ("4771701", "comercio varejista de produtos farmaceuticos", "saude", 4),
    ("0111301", "cultivo de cereais e graos", "agro", 4),
    ("0151201", "criacao de bovinos para corte", "agro", 3),
    ("1071600", "moagem e beneficiamento de graos", "agro", 3),
    ("6462000", "holdings de instituicoes nao financeiras", "holding", 2),
]

PORTES = [("MEI", 22), ("ME", 34), ("EPP", 26), ("DEMAIS", 18)]
SITUACOES = [("ATIVA", 92), ("SUSPENSA", 5), ("BAIXADA", 3)]
QUALIFICACOES = [
    ("socio-administrador", 40),
    ("socio", 38),
    ("administrador", 12),
    ("presidente", 6),
    ("diretor", 4),
]
RATINGS = [("AA", 8), ("A", 20), ("B", 30), ("C", 24), ("D", 12), ("E", 6)]


def _peso(rng: random.Random, opcoes: list[tuple]) -> tuple:
    total = sum(o[-1] for o in opcoes)
    n = rng.randrange(total)
    for o in opcoes:
        if n < o[-1]:
            return o
        n -= o[-1]
    return opcoes[-1]


def cnpj_de(i: int) -> str:
    """CNPJ sintético de 14 dígitos. Não é válido no dígito verificador, de
    propósito: é dado de demonstração e não deve casar com empresa real."""
    return f"{i:08d}0001{(i % 90) + 10:02d}"


def gen_companies(
    rng: random.Random,
    fake: Faker,
    n: int,
    holdings: set[int],
    setor_por_empresa: dict[int, str] | None = None,
):
    setor_por_empresa = setor_por_empresa or {}
    for i in range(n):
        cnae, desc, setor, _ = _peso(rng, CNAES)
        porte, _ = _peso(rng, PORTES)
        situacao, _ = _peso(rng, SITUACOES)
        eh_holding = i in holdings
        if eh_holding:
            cnae, desc, setor = "6462000", "holdings de instituicoes nao financeiras", "holding"
            porte = "DEMAIS"
        # Empresa de grupo herda o setor do grupo: é isso que faz o grupo parecer
        # diversificado no código CNAE e ser concentrado no negócio real.
        setor_grupo = setor_por_empresa.get(i)
        if setor_grupo and not eh_holding:
            candidatas = [c for c in CNAES if c[2] == setor_grupo]
            if candidatas:
                cnae, desc, setor, _ = candidatas[i % len(candidatas)]
        yield {
            "_id": det_id("company", i),
            "cnpj": cnpj_de(i),
            "razao_social": (
                f"{fake.last_name()} {fake.last_name()} "
                f"{'Participacoes' if eh_holding else rng.choice(['Comercio', 'Servicos', 'Industria', 'Empreendimentos'])} "
                f"{rng.choice(['Ltda', 'S.A.', 'EIRELI'])}"
            ),
            "uf": rng.choice(UFS),
            "cnae": cnae,
            "cnae_descricao": desc,
            "setor": setor,
            "porte": porte,
            "situacao": situacao,
            "is_holding": eh_holding,
            "capital_social": round(
                rng.lognormvariate(11.5 if eh_holding else 9.5, 1.2), 2
            ),
            "abertura": NOW - timedelta(days=rng.randint(200, 30 * 365)),
            "seed_index": i,
        }


def gen_credit(
    rng: random.Random,
    n_companies: int,
    taxa: float,
    vitrine: dict[int, str] | None = None,
):
    """Exposição de crédito. Só parte da base tem crédito com o banco — é assim
    numa carteira real, e é o que torna a soma consolidada informativa."""
    vitrine = vitrine or {}
    for i in range(n_companies):
        papel = vitrine.get(i)
        if papel is None and rng.random() > taxa:
            continue
        rating, _ = _peso(rng, RATINGS)
        limite = round(rng.lognormvariate(12.2, 1.3), 2)

        # Grupos de vitrine: a empresa que pede crédito parece pequena e limpa, e
        # o problema está numa subsidiária dois níveis abaixo. É a estrutura que
        # faz a consulta valer a pena — sem ela, a demo mostra uma soma que o
        # analista já conseguiria fazer olhando a empresa isolada.
        if papel == "solicitante":
            rating, limite = "A", round(rng.uniform(800_000, 2_500_000), 2)
        elif papel == "inadimplente":
            rating, limite = "D", round(rng.uniform(18_000_000, 60_000_000), 2)
        elif papel == "grande":
            rating, limite = "B", round(rng.uniform(20_000_000, 90_000_000), 2)
        elif papel == "grupo":
            # As demais do grupo: valores variados e ratings espalhados, para o
            # painel do nó ter o que mostrar em qualquer empresa que o
            # apresentador clicar. Um grafo em que só três nós têm número e o
            # resto mostra zero não parece uma carteira de crédito.
            rating, _ = _peso(rng, RATINGS)
            limite = round(rng.lognormvariate(14.0, 1.1), 2)

        utilizado = round(limite * rng.uniform(0.05, 0.98), 2)
        # Inadimplência concentrada nos ratings ruins, como na vida real.
        chance_atraso = {"AA": 0.005, "A": 0.01, "B": 0.03, "C": 0.08, "D": 0.18, "E": 0.35}[rating]
        if papel == "inadimplente":
            vencido = round(utilizado * rng.uniform(0.25, 0.55), 2)
        elif papel == "solicitante":
            vencido = 0.0  # limpa por si só: é esse o ponto
        else:
            vencido = round(utilizado * rng.uniform(0.05, 0.6), 2) if rng.random() < chance_atraso else 0.0
        yield {
            "_id": det_id("credit", i),
            "company_id": det_id("company", i),
            "limite": limite,
            "utilizado": utilizado,
            "vencido": vencido,
            "rating": rating,
            "atualizado_em": NOW - timedelta(days=rng.randint(0, 90)),
        }


def socio_de(i: int, k: int, n_people: int) -> int:
    """Índice do k-ésimo sócio pessoa física da empresa `i`.

    A versão anterior espalhava o sócio por `(i * 7919 + k * 104729) % n_people`,
    uniformemente. Com 1,2 M de empresas, ~2 sócios cada e 150 k pessoas, isso
    dava **~16 empresas por pessoa** — todo mundo sócio em comum de todo mundo, e
    o sinal que a demo procura vira ruído de fundo.

    Aqui a distribuição é de cauda longa, que é o formato do cadastro real:

    - 85% das empresas puxam sócio de uma faixa larga da população, com o índice
      derivado da própria empresa: quase sempre alguém que não é sócio de mais
      nada;
    - 15% puxam de uma faixa estreita de 2% da população — os sócios recorrentes,
      que participam de dezenas de empresas. É neles que a consulta de grupo
      econômico encontra o que o cadastro isolado não mostra.
    """
    if (i + k) % 100 < 15:
        estreita = max(1, n_people // 50)
        return (i * 31 + k * 17) % estreita
    return (i * 7919 + k * 104729) % n_people


def gen_ownership(
    rng: random.Random,
    n_companies: int,
    n_people: int,
    pares: list[tuple[int, int]],
    pontes: list[tuple[int, list[int]]] | None = None,
):
    """Arestas de participação societária.

    A aresta é **dirigida**: `owner_id` participa de `owned_id`. Subir a cadeia
    (do sócio para as empresas dele) e descer (da holding para as subsidiárias)
    são dois `$graphLookup` com `connectFromField`/`connectToField` trocados — é
    exatamente por isso que a direção não pode ser normalizada aqui.
    """

    def aresta(owner: str, owned: str, tipo: str, pct: float, qual: str):
        return {
            "_id": det_id("own", owner, owned),
            "owner_id": owner,
            "owner_type": tipo,
            "owned_id": owned,
            "percentage": round(pct, 2),
            "qualificacao": qual,
            "since": NOW - timedelta(days=rng.randint(60, 20 * 365)),
        }

    # --- 1. sociedade entre pessoas físicas: o caso comum ---
    for i in range(n_companies):
        empresa = det_id("company", i)
        n_socios = rng.choices([1, 2, 3, 4], weights=[42, 34, 18, 6])[0]
        restante = 100.0
        for k in range(n_socios):
            pi = socio_de(i, k, n_people)
            pct = restante if k == n_socios - 1 else round(restante * rng.uniform(0.2, 0.8), 2)
            restante = max(0.0, restante - pct)
            qual, _ = _peso(rng, QUALIFICACOES)
            yield aresta(det_id("person", pi), empresa, "individual", pct, qual)

    # --- 2. participação entre pessoas jurídicas: controladora -> controlada ---
    for pai, filho in pares:
        yield aresta(
            det_id("company", pai),
            det_id("company", filho),
            "corporate",
            rng.uniform(51, 100),
            "socio-administrador",
        )

    # --- 3. pontes: a mesma pessoa física sócia em dois grupos distintos ---
    #
    # É o achado que justifica a consulta. Sem esta aresta, dois grupos de
    # vitrine são duas ilhas e o analista não tem nada a descobrir subindo a
    # cadeia — a resposta já estava no cadastro da empresa consultada.
    for pi, empresas in pontes or []:
        for e in empresas:
            yield aresta(
                det_id("person", pi), det_id("company", e),
                "individual", rng.uniform(5, 24), "socio",
            )


# Profundidades societárias distintas cobertas pelos grupos de vitrine. Tem que
# bater com `GRAPH_MAX_DEPTH_CAP` no backend: o slider da tela vai de 1 a 6, e
# cada posição precisa de um grupo em que ela seja o fundo exato da árvore.
GRUPO_PROFUNDIDADES = 6


def larguras_por_nivel(profundidade: int) -> list[int]:
    """Quantas empresas em cada nível abaixo da holding.

    Cresce com o nível para a árvore parecer conglomerado, e é calibrada para o
    grupo raso não sair magro demais na tela: profundidade 1 abre com 6
    controladas diretas, profundidade 6 termina com 38 empresas no total.
    """
    largura_base = max(2, 7 - profundidade)
    return [largura_base + 2 * n for n in range(profundidade)]


def monta_topologia(rng: random.Random, n: int, n_grupos: int, max_chain: int, n_vitrine: int):
    """Sorteia grupos econômicos, a cadeia longa e os casos de vitrine.

    ## Por que cada grupo de vitrine tem uma profundidade diferente

    A versão anterior criava grupos rasos — holding e subsidiárias, um nível só —
    e isso quebrava o controle de profundidade da tela: nível 1 e nível 6
    devolviam exatamente as mesmas empresas, porque não havia o que revelar. O
    apresentador clicava e nada mudava.

    Com todos os grupos em quatro níveis o problema voltou pela outra ponta: a
    partir do nível 3 a árvore acabava, e subir para 5 ou 6 devolvia exatamente as
    mesmas empresas. O slider mexia e a tela não.

    Agora cada grupo de vitrine tem uma profundidade própria, de 1 a
    `GRUPO_PROFUNDIDADES`, e o solicitante fica **na base**: no nível 1 ele vê só
    quem o controla diretamente, e o grupo inteiro só aparece quando a
    profundidade escolhida alcança a do grupo. Os grupos não compartilham
    empresa nenhuma — `reserva()` só entrega id ainda não usado.

    ## Setor por grupo

    Cada grupo de vitrine recebe **um** setor, e todas as suas empresas puxam CNAE
    daquele setor. O grupo aparenta diversificação — códigos CNAE diferentes, razões
    sociais diferentes — e é concentrado no mesmo negócio. É o que a análise
    semântica encontra sozinha, e é risco de concentração de verdade numa mesa de
    crédito.
    """
    cursor = n - 1
    pares: list[tuple[int, int]] = []   # (controladora, controlada)
    membros_por_grupo: list[list[int]] = []
    setor_por_empresa: dict[int, str] = {}
    setores = sorted({c[2] for c in CNAES if c[2] != "holding"})

    def reserva(k: int) -> list[int]:
        nonlocal cursor
        out = [cursor - i for i in range(k)]
        cursor -= k
        return out

    # --- grupos de vitrine: um por profundidade, setor único ---
    #
    # Cada grupo de vitrine tem uma profundidade societária **própria**, de 1 a
    # `GRUPO_PROFUNDIDADES`, e nenhuma empresa em comum com outro grupo
    # (`reserva()` só entrega ids ainda não usados). O motivo é a tela: com todos
    # os grupos em quatro níveis, o controle de profundidade parava de mudar a
    # resposta a partir do 3 — o apresentador subia para 5 e via exatamente as
    # mesmas empresas. Agora o slider distingue os grupos entre si, e o grupo de
    # seis níveis é o que prova até onde o `$graphLookup` vai.
    #
    # A largura cresce com o nível para a árvore parecer conglomerado e não
    # cordão: as folhas são sempre a maior camada.
    vitrine_grupos = []
    for v in range(n_vitrine):
        profundidade = (v % GRUPO_PROFUNDIDADES) + 1
        larguras = larguras_por_nivel(profundidade)

        niveis: list[list[int]] = [reserva(1)]         # nível 0: a holding de topo
        for largura in larguras:
            niveis.append(reserva(largura))

        for n, filhos in enumerate(niveis[1:], start=1):
            pais = niveis[n - 1]
            for i_f, filho in enumerate(filhos):
                pares.append((pais[i_f % len(pais)], filho))

        # Participação cruzada: uma controlada de um ramo também recebe
        # participação de outro ramo. Existe em grupo real, e é o que impede que
        # a cadeia seja uma árvore perfeita — o `$graphLookup` visita o nó por
        # dois caminhos e a deduplicação de arestas passa a importar de verdade.
        #
        # O alvo é escolhido para **não** repetir o pai que o laço acima já deu:
        # `_id` da aresta é determinístico sobre (owner, owned), e um par repetido
        # não é "duas participações", é colisão de chave na carga limpa.
        cruzadas: list[tuple[int, int]] = []
        for n in range(1, len(niveis)):
            pais, filhos = niveis[n - 1], niveis[n]
            if len(pais) < 2 or not filhos:
                continue
            filho = filhos[-1]
            pai_real = pais[(len(filhos) - 1) % len(pais)]
            outro = next((p for p in pais if p != pai_real), None)
            if outro is not None:
                cruzadas.append((outro, filho))
        pares.extend(cruzadas)

        membros = [c for nivel in niveis for c in nivel]
        setor = setores[v % len(setores)]
        for m in membros:
            setor_por_empresa[m] = setor
        membros_por_grupo.append(membros)

        base = niveis[-1]
        # Inadimplente um nível acima do solicitante e noutro ramo: o problema
        # não está em quem pediu. Num grupo de um nível só, "acima" é a holding.
        acima = niveis[-2] if len(niveis) >= 2 else niveis[0]
        vitrine_grupos.append(
            {
                "membros": membros,
                "raiz": niveis[0][0],
                "niveis": profundidade,
                # Solicitante na base da árvore: é dele que subir a cadeia importa.
                "solicitante": base[0],
                "inadimplente": acima[-1],
                "setor": setor,
                "ponte_alvo": base[1] if len(base) > 1 else base[0],
                "cruzadas": cruzadas,
            }
        )

    # --- grupos comuns: rasos, como a maioria ---
    for _ in range(max(0, n_grupos - n_vitrine)):
        n_subs = rng.randint(3, 8)
        raiz = reserva(1)[0]
        subs = reserva(n_subs)
        for sub in subs:
            pares.append((raiz, sub))
        membros_por_grupo.append([raiz, *subs])
        if rng.random() < 0.35 and cursor > max_chain + 20:
            netas = reserva(2)
            for neta in netas:
                pares.append((subs[0], neta))
            membros_por_grupo[-1] += netas

    cadeia = reserva(max_chain)
    for pai, filho in zip(cadeia, cadeia[1:]):
        pares.append((pai, filho))

    # Deduplicação defensiva. `_id` da aresta é determinístico sobre (owner,
    # owned), então um par repetido não é "duas participações" — é uma colisão de
    # chave que derruba a carga limpa no meio. Barato garantir aqui.
    vistos: set[tuple[int, int]] = set()
    unicos: list[tuple[int, int]] = []
    for par in pares:
        if par not in vistos:
            vistos.add(par)
            unicos.append(par)
    pares = unicos

    holdings = {p[0] for p in pares}
    vitrine: dict[int, str] = {}
    for g in vitrine_grupos:
        vitrine[g["solicitante"]] = "solicitante"
        vitrine[g["inadimplente"]] = "inadimplente"
        vitrine[g["raiz"]] = "grande"
        # As demais do grupo também recebem crédito: um grafo em que só três nós
        # têm número e o resto mostra zero não é uma carteira, é um diagrama.
        for m in g["membros"]:
            vitrine.setdefault(m, "grupo")

    # Pontes de sócio PF entre grupos de vitrine consecutivos: a mesma pessoa
    # sócia minoritária em dois conglomerados que o cadastro trata como
    # independentes. Uma a cada dois grupos, para o achado continuar sendo
    # exceção — se todo grupo tivesse ponte, ela não seria notícia.
    pontes: list[tuple[int, list[int]]] = []
    for v in range(0, len(vitrine_grupos) - 1, 2):
        a, b = vitrine_grupos[v], vitrine_grupos[v + 1]
        # Faixa reservada, fora dos 2% de sócios recorrentes que `socio_de`
        # usa: a ponte precisa ser um vínculo notável, não mais um hub.
        pi = 500_000 + v
        pontes.append((pi, [a["ponte_alvo"], b["ponte_alvo"]]))
        a["ponte_person"] = pi
        b["ponte_person"] = pi

    return pares, holdings, vitrine, vitrine_grupos, setor_por_empresa, pontes


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--companies", type=int, default=1_200_000)
    p.add_argument("--groups", type=int, default=40_000)
    p.add_argument("--showcase", type=int, default=40, help="grupos de vitrine, um por profundidade societária")
    p.add_argument("--max-chain", type=int, default=10, help="profundidade da cadeia longa")
    p.add_argument("--credit-rate", type=float, default=0.32, help="fração com crédito no banco")
    p.add_argument("--drop", action="store_true")
    args = p.parse_args()

    # Guarda que faltava. `--groups` menor que `--showcase` fazia
    # `range(max(0, n_grupos - n_vitrine))` virar zero: a base saía com **só** os
    # grupos de vitrine, 1.049 participações entre PJ em vez de 249 mil, e
    # nenhum erro em lugar nenhum. Carregava igual, rodava igual, e o grafo da
    # demo só tinha os casos plantados. Um volume herdado do ambiente
    # (`GROUPS=20`) produziu exatamente isso duas vezes.
    if args.groups < args.showcase:
        raise SystemExit(
            f"--groups ({args.groups:,}) menor que --showcase ({args.showcase}): "
            "a base sairia só com os grupos de vitrine. Aumente --groups."
        )

    rng = random.Random(SEED)
    fake = Faker("pt_BR")
    Faker.seed(SEED)
    db = get_db()

    n_people = db.people.estimated_document_count()
    if n_people == 0:
        raise SystemExit("Rode generate_synthetic_data.py antes: os sócios PF vêm de `people`.")

    if args.drop:
        db.companies.drop()
        db.ownership.drop()
        db.credit_exposure.drop()
        print("coleções societárias apagadas")

    # Carga limpa usa `insert_many`; reexecução usa upsert. A escolha muda a
    # vazão em várias vezes, e é ela que o benchmark de carga mede.
    limpo = db.companies.estimated_document_count() == 0
    escrever = bulk_insert if limpo else bulk_replace
    print("modo de escrita:", "insert_many (carga limpa)" if limpo else "upsert (reexecução)")

    pares, holdings, vitrine, vitrine_grupos, setor_por_empresa, pontes = monta_topologia(
        rng, args.companies, args.groups, args.max_chain, args.showcase
    )
    print(
        f"topologia: {len(pares):,} participações entre PJ, {len(holdings):,} controladoras, "
        f"{len(vitrine_grupos)} grupos de vitrine, profundidades 1..{GRUPO_PROFUNDIDADES}, "
        f"{len(pontes)} pontes de sócio PF entre grupos"
    )

    def escreve(coll, docs, rotulo):
        """Devolve (documentos, segundos). Upsert não reporta tempo — na
        reexecução a vazão não é o que interessa, a idempotência é."""
        if limpo:
            return bulk_insert(coll, docs, rotulo)
        return bulk_replace(coll, docs, rotulo), 0.0

    t_total = time.perf_counter()
    n_emp, dt_emp = escreve(
        db.companies,
        gen_companies(rng, fake, args.companies, holdings, setor_por_empresa),
        "companies",
    )
    n_own, dt_own = escreve(
        db.ownership,
        gen_ownership(rng, args.companies, n_people, pares, pontes),
        "ownership",
    )
    n_cred, dt_cred = escreve(
        db.credit_exposure,
        gen_credit(rng, args.companies, args.credit_rate, vitrine),
        "credit_exposure",
    )

    # Ground truth dos grupos: a tela precisa de um caso bom à mão, sem varrer a
    # base ao vivo na frente do cliente.
    db.economic_groups.drop()
    if vitrine_grupos:
        db.economic_groups.insert_many(
            [
                {
                    "_id": f"group_{i:04d}",
                    "holding_id": det_id("company", g["raiz"]),
                    "member_ids": [det_id("company", m) for m in g["membros"]],
                    "size": len(g["membros"]),
                    "applicant_id": det_id("company", g["solicitante"]),
                    "distressed_id": det_id("company", g["inadimplente"]),
                    "sector": g["setor"],
                    "levels": g["niveis"],
                    "showcase": True,
                    "bridge_person_id": (
                        det_id("person", g["ponte_person"]) if "ponte_person" in g else None
                    ),
                    "cross_holdings": [
                        {"owner_id": det_id("company", a), "owned_id": det_id("company", b)}
                        for a, b in g["cruzadas"]
                    ],
                }
                for i, g in enumerate(vitrine_grupos)
            ]
        )
        db.economic_groups.create_index("holding_id")
    print(f"ground truth: {db.economic_groups.estimated_document_count()} grupos de vitrine")

    # O relatório da carga é gravado **antes** de criar os índices. A criação
    # é longa e já caiu por timeout uma vez, levando junto o número que tinha
    # acabado de ser medido — e medir de novo custa a carga inteira.
    total_docs = n_emp + n_own + n_cred
    dt_escrita = dt_emp + dt_own + dt_cred
    if limpo and dt_escrita > 0:
        from pathlib import Path as _P

        relatorio = {
            # Hora real da medição, não o `NOW` fixo do gerador: um relatório de
            # desempenho datado da semente engana quem for lê-lo depois.
            "measured_at": datetime.now(timezone.utc).isoformat(),
            "mode": "insert_many",
            "note": (
                "medido de uma estação de trabalho contra um cluster remoto; a "
                "latência do cliente está incluída e domina. Carga de produção roda "
                "na mesma região do cluster."
            ),
            "collections": {
                "companies": {"docs": n_emp, "seconds": round(dt_emp, 1), "docs_per_sec": round(n_emp / dt_emp)},
                "ownership": {"docs": n_own, "seconds": round(dt_own, 1), "docs_per_sec": round(n_own / dt_own)},
                "credit_exposure": {"docs": n_cred, "seconds": round(dt_cred, 1), "docs_per_sec": round(n_cred / dt_cred)},
            },
            "total": {
                "docs": total_docs,
                "seconds": round(dt_escrita, 1),
                "docs_per_sec": round(total_docs / dt_escrita),
                "docs_per_hour": round(total_docs / dt_escrita * 3600),
            },
            "wall_seconds": round(time.perf_counter() - t_total, 1),
        }
        _P("queries/load-results.json").write_text(json.dumps(relatorio, indent=2))
        t = relatorio["total"]
        print(
            f"\ncarga: {t['docs']:,} documentos em {t['seconds']:.0f}s "
            f"= {t['docs_per_sec']:,} docs/s ({t['docs_per_hour']/1e6:.1f} M/hora)"
        )
        print("  medido do cliente, latência de rede inclusa — ver queries/benchmarks.md")

    print("\níndices")
    for coll, chave, opts, porque in (
        (db.companies, "cnpj", {"unique": True}, "consulta pontual por chave de negócio"),
        (db.companies, "razao_social", {}, "apoio à busca por nome"),
        (db.companies, "is_holding", {"sparse": True}, "seleção de casos na demo"),
        (db.ownership, "owner_id", {}, "connectFromField ao descer a cadeia"),
        (db.ownership, "owned_id", {}, "connectToField ao subir a cadeia"),
        (db.ownership, "owner_type", {}, "separar sócio PF de PJ"),
        (db.credit_exposure, "company_id", {"unique": True}, "exposição por empresa"),
    ):
        nome = coll.create_index(chave, **opts)
        print(f"  {coll.name}.{nome} — {porque}")

    print(f"\ncompanies      {db.companies.estimated_document_count():>10,}")
    print(f"ownership      {db.ownership.estimated_document_count():>10,}")
    print(f"credit_exposure{db.credit_exposure.estimated_document_count():>10,}")


if __name__ == "__main__":
    main()
