# Turning the demo into a number

The technical demo answers *can MongoDB do this?* This page answers the question
that comes right after it, from someone who did not watch the demo: **what is it
worth?**

It is deliberately short, and deliberately honest about which half of the
arithmetic is ours and which half is the bank's.

## The rule

**We supply what is measured. The bank supplies what is assumed.**

Everything in the screen's "What this case is worth" panel follows that split, and
the panel says so on screen. That is not modesty — it is the same discipline that
makes `queries/benchmarks.md` credible. A single invented number in this page
would give an architect permission to doubt every measured one.

We do **not** estimate loss avoided. Turning exposure into realised loss needs a
conversion rate that varies by product, by institution and by fraud typology.
Guessing it would undo everything else.

## What the screen measures

Flag a ring and open **What this case is worth**. Every number in the first block
comes from `transactions` on the live cluster:

| Measured | Where it comes from |
|---|---|
| volume that moved through the network | sum of `amount` over transactions touching the ring's accounts, both directions |
| number of operations | count of those transactions |
| accounts involved | `accounts` belonging to the flagged people |
| observation window | span between the first and last of those transactions |
| average ticket | mean `amount` |

On the default dataset a 30-member ring typically shows on the order of **half a
million reais across a few hundred operations in a six-month window**. Your run
will differ — the panel shows what your cluster actually holds.

That number is the answer to *"why should I care about these thirty accounts?"*

## What the bank supplies

Two inputs, in `.env`:

```bash
ANALYST_HOURS_PER_CASE=4     # how long one investigation takes today
ANALYST_COST_PER_HOUR=120    # fully loaded cost of an analyst hour
CURRENCY=R$
```

Ask for both during discovery. If the customer does not know them, that is itself
a finding worth raising — a fraud operation that cannot cost a case cannot measure
its own efficiency.

The panel then does one piece of arithmetic, and only one:

- **today**, a case is opened per account or per alert: `accounts × cost per case`;
- **here**, one case covers the ring the traversal found: `1 × cost per case`.

That is not a projection. It is the customer's own number multiplied by a count
the demo measured.

## The three arguments, in the order that lands

**1. Exposure you cannot see today.** The ring moved *X* reais and none of its
members would have been opened as one case. This is the number that gets attention,
and it is measured.

**2. Case consolidation.** The traversal collapses N alerts into one investigation.
Multiply by the bank's own cost per case. Frame it as analyst capacity released,
not headcount removed — the second framing kills deals with fraud teams.

**3. Operational surface avoided.** No second database to run, back up, monitor,
secure and synchronise. `COMPETITIVE.md` carries the detail, including the four
qualifying questions that decide whether this argument applies at all.

## Where the argument stops

Say these before anyone asks — the same rule as `LIMITATIONS.md`:

- **We do not estimate loss avoided,** for the reason above.
- **Detection quality is not benchmarked here.** The rings are injected ground
  truth, so precision and recall on real data are unproven. This demonstrates the
  investigation, not the detection model.
- **The consolidation ratio depends on how the bank triages today.** If they already
  group alerts by device, the gain is smaller. Ask.
- **Cost of the platform is not in this page.** Cluster sizing is a separate
  conversation; the demo cluster is a shared M20 and proves nothing about
  production sizing.

## Discovery questions that make this page usable

1. How many fraud alerts per month reach a human, and how many become cases?
2. How long does one case take, end to end, and what is an analyst hour worth?
3. Are alerts grouped today, or is it one case per account?
4. What is the current exposure per confirmed mule network — do you measure it?
5. What does the bank spend today on the systems this would replace or avoid?

Questions 1 to 3 fill the panel. Questions 4 and 5 turn it into a business case
the customer owns rather than one we handed them.
