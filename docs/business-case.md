# Turning the demo into a number

The technical demo answers *can MongoDB do this?* This page answers the question
that comes right after it, from someone who did not watch the demo: **what is it
worth?**

It is deliberately short, and deliberately honest about which half of the
arithmetic is ours and which half is the bank's.

## The rule

**We supply what is measured. The bank supplies what is assumed.**

Everything in the screen's "What this decision is worth" panel follows that split,
and the panel says so on screen. That is not modesty — it is the same discipline
that makes `queries/benchmarks.md` credible. A single invented number here would
give an architect permission to doubt every measured one.

We do **not** estimate loss avoided. Turning exposure into realised loss needs a
default-rate assumption that varies by product, by institution and by segment.
Guessing it would undo everything else.

## What the screen measures

Open a group and expand **What this decision is worth**. Every number in the first
block comes from `credit_exposure` on the live cluster:

| Measured | Where it comes from |
|---|---|
| consolidated limit of the group | sum of `limite` over every company the traversal reached |
| drawn | sum of `utilizado` over the same set |
| overdue | sum of `vencido` — the arrears that sit in a sibling, not in the applicant |
| companies with credit | how many of the group's entities have exposure at all |
| entities in the group | what the traversal reached, versus the one record the analyst started from |

The contrast is the argument: the applicant's own record shows a clean rating and
a small limit; the group behind it carries an order of magnitude more, with the
arrears in another branch. Both numbers come out of the same database, in one
query.

## What the bank supplies

Two inputs, in `.env`:

```bash
ANALYST_HOURS_PER_CASE=4     # how long one group analysis takes today
ANALYST_COST_PER_HOUR=120    # fully loaded cost of an analyst hour
CURRENCY=R$
```

Ask for both during discovery. If the customer does not know them, that is itself
a finding worth raising — a credit desk that cannot cost an analysis cannot
measure its own efficiency.

The panel then does one piece of arithmetic, and only one:

- **today**, mapping a group by hand means pulling records one CNPJ at a time:
  `entities × cost per record`;
- **here**, one query returns the group: `1 × cost per analysis`.

That is not a projection. It is the customer's own number multiplied by a count
the demo measured.

## The three arguments, in the order that lands

**1. Exposure you cannot see today.** The applicant is clean; the group is not. The
consolidated figure does not exist in any single record, and no analyst assembles
it reliably by hand under a decision SLA. This is the number that gets attention,
and it is measured.

**2. Visibility that cannot go stale.** The second scenario — manager sees the
advisors below them, an advisor sees only their own book — is derived from the
hierarchy at query time. The common alternative is a materialised list per user,
which is wrong for as long as it takes to recompute after every book transfer.
That is a control and audit argument, not a performance one, and it lands with
risk and compliance rather than with engineering.

**3. Operational surface avoided.** No second database to run, back up, monitor,
secure and synchronise. And for the pattern this POV was built for, the customer's
bottleneck is loading the base, not traversing it — `queries/benchmarks.md` has the
load throughput measured, in the same document as the query latency. The
qualification table in `README.md` decides whether this argument applies at all.

## Where the argument stops

Say these before anyone asks — the same rule as `LIMITATIONS.md`:

- **We do not estimate loss avoided,** for the reason above.
- **Group detection quality is not benchmarked.** The groups are generated ground
  truth, so precision and recall against a real registry are unproven. This
  demonstrates the query, not a data-quality pipeline over public CNPJ data.
- **The consolidation gain depends on how the bank works today.** If they already
  pull a consolidated group view from a data warehouse overnight, the gain is
  freshness, not discovery. Ask which one they have.
- **Cost of the platform is not in this page.** Cluster sizing is a separate
  conversation; the demo cluster is a shared M20 and proves nothing about
  production sizing.

## Discovery questions that make this page usable

1. How does a credit analyst assemble a group's consolidated exposure today, and
   how long does it take?
2. What is an analyst hour worth, fully loaded?
3. Is the group view live, or produced by an overnight batch? What is the decision
   SLA, and how often does the batch age past it?
4. How is account visibility enforced today — a derived query or a materialised
   list per user? What happens between a book transfer and the next recompute?
5. What does the bank spend today on the systems this would replace or avoid?

Questions 1 and 2 fill the panel. Questions 3 to 5 turn it into a business case
the customer owns rather than one we handed them.
