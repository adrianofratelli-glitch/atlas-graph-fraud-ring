# 03 — Interface and flows

The interface is in **Brazilian Portuguese**: it is presented to Brazilian banks,
and CNPJ, razão social, sócio and rating are the words those teams use. The
repository documentation stays in English.

The visual design follows a token set shared across the demonstration projects:
dark background, green as the signal for action and success, blue for information,
amber for warning and red for risk. `src/pov-signature.css` carries that signature
and is imported after the application stylesheet.

## Stage mode

One screen. The first viewport holds **one thesis** (a company on its own looks
small and clean), **one action** (the traversal depth) and **one piece of
evidence** (the group graph with the consolidated exposure and the measured
response time).

Anything that decides nothing for the presenter is out of the way: the executed
query lives in a `<details>`, and the Search, Concentration, Visibility and alert
panels sit beside the graph, in script order.

## Layout — one screen, no scrolling

```
┌─ topbar 52px: brand · one-line thesis · health ────────────────────────┐
├──────────┬────────────────────────────────────────┬────────────────────┤
│ controls │ metrics: nodes · edges · in ring ·      │ inspector (tabs)   │
│          │          blocked · time · hop · legend  │ ┌────────────────┐ │
│ entry    │ ┌────────────────────────────────────┐  │ │Node│Search│Conc│Vis│ │
│ depth    │ │                                    │  │ ├────────────────┤ │
│ edges    │ │   canvas (takes the remaining      │  │ │ node detail    │ │
│ action   │ │           height)   [+] [−] [⤢]    │  │ │ neighbour chips│ │
│          │ └────────────────────────────────────┘  │ └────────────────┘ │
│          │                                         │ ▸ Query executed   │
└──────────┴────────────────────────────────────────┴────────────────────┘
```

`body { overflow: hidden }` and `.app { height: 100dvh }`: the page never scrolls.
Only the control rail and the inspector body scroll internally, when they need to.

An earlier version stacked graph, search, vector and alerts in a 2,600 px column —
the presenter scrolled to find each piece of evidence and lost sight of the graph.
The three pieces of evidence became inspector tabs, next to the graph, which stays
visible the whole time.

## Graph interaction

Four patterns borrowed from graph investigation tools:

- **Neighbour highlighting.** Hovering a node lights up its direct neighbours and
  dims everything else to ~16% opacity. The chain that matters is alone on screen.
  The focused node **keeps its type colour** and gains a blue border — swapping the
  fill would erase the "this is in a ring" information.
- **Progressive disclosure.** No native tooltip: the node detail (hops, degree,
  accounts and when they were opened, risk flags, `_id`) lives in the **Node** tab
  of the inspector, along with the direct neighbours as clickable shortcuts.
- **A one-shot hop sweep.** On first drawing, colour travels outwards from the
  subject company one topological hop at a time. Nodes never move, the sweep ends
  in roughly half a second and any user interaction cancels it. This explains the
  traversal without turning a deterministic ownership chart into a force demo.
  It is deliberately **not** presented as an internal `$graphLookup` execution
  trace.
- **Local re-settling on drag.** Physics is permanently off. A short deterministic
  collision pass moves only nodes that overlap the dropped node; the ownership
  levels and every unrelated position stay stable.

Company size follows the credit limit, not graph degree: this is a credit decision,
so the visual weight follows the money. Companies are boxes and individual owners
are circles. The subject gets a green border, holdings blue, overdue companies red
and companies under review a dashed border.

Edges are intentionally quiet at rest: 1.8 px for corporate ownership and 1.5 px
for individual participation, both at 50% opacity. Their percentage labels appear
only when the relationship is in context — hover, pinned node or a highlight from
the inspector. This removes the permanent layer of numbers without hiding the
information when the analyst asks for it. Focus raises opacity and width; colour
still distinguishes corporate (blue) from individual (amber) participation.

Framing controls (`+`, `−`, `⤢`) sit over the canvas: after dragging or zooming,
the presenter returns to the framing without reloading.

## Dragging moves what you dragged, and nothing else

The first version enabled the physics solver on `dragStart` and froze it once the
engine settled. It reads well as a description and behaves badly in the hand:
moving one node put the whole graph in motion, and the analyst lost the spatial
map they had just built. In a demo that reads as instability.

Physics is now off permanently. The dragged node stays exactly where it was
dropped, and a local pass pushes **only** the nodes it landed on top of, by the
minimum distance, with a short cascade (two levels) and a per-node displacement
cap. Measured on the demo group: dragging one company moves 4 of 35 nodes — the
dragged one and the three that had to get out of the way.

A second pass runs once after the initial layout, and it only moves nodes **on the
X axis**, within each level. Vertical position is the ownership level; pushing a
node up or down to fix an overlap would trade a cosmetic problem for a false
statement about who controls whom.

## Pinning a node, and why hover must not win

The right-hand panel resolved the node as `hovered ?? picked`: hover **always** beat
the click. In practice, clicking a node was useless — moving the mouse towards the
panel swapped the content, and the analyst could never read the node they had
chosen.

The priority is now `picked ?? hovered`. Clicking pins; hover only fills the panel
when nothing is pinned, and keeps isolating neighbours on the canvas either way.
The pinned node gets a thick white border on the graph and a `pinned ✕` chip in the
panel: without a permanent mark, when the analyst looks back at the graph they no
longer know where what they are reading came from.

`pinned` lives in a `useRef` inside `GraphCanvas`, not in the dependencies of the
`useEffect` that creates the `Network`. Putting it in the dependency list would
rebuild the graph on every click, throwing away the layout physics had just
settled.

## The case, without a new tab

The result of the ACID transaction lives in the **left column**, under the button
that fired it. A new tab would be one more screen for the presenter to remember to
open mid-demo, and the effect of the action needs to appear where the action
happened.

The card shows blocked accounts, flagged people, commit time, `snapshot`/`majority`
and the before/after of the first accounts (`active` → blocked). On the canvas,
flagged nodes get a **dashed border**, and a "Blocked" metric appears in the top
bar. Without that, flagging the network changed nothing on screen and the strongest
part of the demo went unnoticed.

The compliance report (COAF / MED / LGPD) sits in a closed `<details>` that only
calls the API when opened. It answers "and what does the bank do with this?", a
question not every audience asks.

If a case is already open over those nodes, the backend refuses with a 409 carrying
the open `case_id`. The screen does not show an error and stop: it pulls that case
forward, with the close button next to it, which is the way out the analyst wants.

## The alert A/B

`Close case` and `Reset demo` are buttons, not just endpoints. They exist for
something that used to be impossible to show: the same mechanism producing the
alert **and** its counterpart, so silence has a shape.

The trigger is the credit review itself. Opening one marks every company in the
group in a single transaction; the change stream on `companies` picks up those
documents, coalesces them by `case_id`, and the Alerts tab receives one event with
the real company count and the exposure now under review. Closing the case emits
`review_closed` by the same path.

That pairing is the argument: the listener reads state instead of firing on its
own, and the presenter can prove it live in about a second each way. The event
carries `stream_ms` — commit to arrival, coalescing window included — because
showing a number smaller than the one actually lived would be demo fiction.

An earlier version had a separate "simulate ownership change" button writing a fake
edge into `ownership` just to wake the listener. It was removed: it needed a
paragraph of explanation to mean anything, and the review transaction is a better
trigger because it is the thing the analyst actually does.

## The business number, without a new screen

Two additions, both inside surfaces that already existed:

- an **At risk** tile in the metrics bar, showing the volume the network on screen
  moved. It appears only when the expansion contains ring members;
- a **What this case is worth** block inside the case card, collapsed by default.

`POST /api/exposure` is a separate call made *after* the graph has drawn. The
aggregation over `transactions` costs about a second, and hanging it on the
traversal would slow down the exact step being demonstrated live.

The measured half and the assumed half are visually separated, and the panel labels
them. The volume, operations and window come from the data; hours and cost per case
come from `.env` and are the bank's numbers. Loss avoided is deliberately not
estimated — see [`docs/business-case.md`](../business-case.md).

## Keeping the rail free of scroll

The case card grew three collapsible blocks — what the transaction changed, what
the case is worth, what the bank must now do — and with all of them expanded the
control rail reached 1,687 px against a 922 px viewport. The presenter would have
had to scroll to reach *Close case*.

Two changes fixed it, and both are measured against the rail's own scroll height:

- the card shows only the headline (accounts blocked, people, commit time,
  guarantee) in a two-column grid; everything else is behind a summary;
- the **Flag** button disappears while a case is open. The backend refuses a second
  case over the same nodes anyway, so the button was occupying rail height to do
  nothing.

Measured after: 922 px with a case open and everything collapsed — exactly the
viewport, no scroll. Expanding a block scrolls that rail, which is deliberate: the
presenter asked for it.

## Nothing but an alert steals the tab

Every SSE event used to call `setTab('alerts')`. During a large `update_many` the
interface jumped to Alerts every few seconds and it was impossible to type into a
search box. Now only `review_opened` switches tabs; `review_closed` stays in the
list without hijacking the screen.

The server-side `$match` closes the same hole from the other end: the listener only
publishes when `credit_status` actually changed, so a bulk load never reaches the
SSE at all.

## Search scope

Searching "Diego" in a 150,000-person database returned ten random Diegos, and the
Diego who was on screen did not appear among them. It was not an ordering problem:
`$search` cuts to the most relevant **before** any annotation, and reordering does
not fix what never arrived.

Both panels gained a scope segment, with different defaults for different reasons:

| Panel | Default | Why |
|---|---|---|
| Atlas Search | `base` | the misspelled twin is **not** in the network; always scoping would kill step 7 |
| Vector Search | `rede` | loose over the database the panel is a curiosity; scoped, it answers "what excuse do these accounts use?" |

In `base` mode, `resolve_entity` runs a second, network-scoped pass and puts it
first. Every result carries `na_rede`, and the list shows the tag — which is what
answers "which of these Diegos is mine?".

The scope reaches the engine through `ring_id`, which is a `token` on the search
index and a filter field on the vector index. No index had to be rebuilt.

## The visibility panel

The second scenario needed no new screen: it is a tab beside the others, and it
holds three things — a list of example users, the scope and book of whoever is
selected, and the verdict for the company currently on the graph.

Two decisions worth defending:

**The user list is a list of buttons, not a dropdown.** The whole point is the
*contrast* between a manager and an advisor, and a dropdown hides the other option
behind a click. Each row is a real `<button>`, so keyboard and screen reader work;
the styling only removes the button look, never the behaviour.

**The verdict follows the company already on screen.** Selecting a user
re-evaluates the CNPJ under analysis, so the boundary is demonstrated on the same
company the customer has been looking at for the last five minutes — not on an
abstract example. In the first showcase group the members are deliberately split
between two advisors under different managers, so the boundary cuts through the
middle of an economic group.

The panel reports the scope size and the elapsed time, because the argument is that
the scope was *derived* — one aggregation, walking down the tree — rather than read
from a stored list.

## Every list points at the graph

The rail panels are not read-only lists. Clicking a row sets a highlight: the nodes
it refers to get an emphasised border, every other node dims, and the canvas centres
on the set. Clicking the same row again releases it, and changing company, depth or
tab clears it — a highlight that outlives the graph it referred to is worse than no
highlight.

Two details that took a second pass:

- **The zoom is clamped.** `fit({nodes})` on a single node zooms to maximum and the
  context disappears: the presenter gets a huge box in the middle of the screen with
  no idea where it sits in the chain. It now centres on the set and holds the scale
  between 0.55 and 0.9.
- **A result outside the graph does nothing.** Search can be opened to the whole
  base, and those hits have no node to point at. Flashing the graph for them would
  be worse than not reacting.

## The visibility boundary is drawn on the graph

Selecting a user in the Visibility tab dims the companies of the group that fall
outside that user's scope. Dimmed, not hidden: the company **is** in the economic
group — what changes is who may see it. Hiding would answer a different question
and quietly misrepresent the data.

The cross-reference needs no new endpoint. The portfolio response already carries
the scope (the user plus everyone below them) and every company node already
carries the advisor who covers it, so the intersection happens on the client. That
is also the honest architecture: the *decision* stays on the server (`can-see`),
the *rendering* of it is a client concern.

## States

| State | What the screen does |
|---|---|
| Backend down | `✕ backend offline` badge; the canvas says what happened |
| Search/Vector index `BUILDING`/`MISSING` | badge on that feature's panel and a notice explaining; the graph keeps working |
| No `VOYAGE_API_KEY` | same path, with status `NO_EMBEDDING_KEY` |
| Entry point with no links | empty canvas with text, not an infinite spinner |
| Truncated result | notice pointing at `LIMITATIONS.md §4`, with the real edge count found |
| No alerts yet | text saying which action produces one, not a mute void |
| Reviewing at a shallow depth | notice saying how many companies this expansion would actually flag |
| Search with no hit inside the group | says so, and offers the checkbox that opens the search to the whole base — an empty list with no explanation reads as a broken index |
| Analytical query saturated (429) | the panel says "saturated, try again", **not** "unavailable" — the backend caps concurrency per class and refuses early to protect the interactive path. Confusing backpressure with an outage sends the presenter debugging the wrong thing |
| Account outside the user's scope | the visibility panel says **not visible** and gives the reason, naming the advisor who owns the account — a refusal has to explain itself, or it reads as a bug |

No state is communicated by colour alone: a company under review has a dashed
border on top of the metric, the subject of the query is labelled in the node
panel, and the visibility verdict is a sentence, not a colour.

## Streaming

Alerts arrive over `EventSource` at `/api/alerts/stream`. The backend sends a
heartbeat every 15 s so the browser does not consider the idle connection dead;
`EventSource` reconnects on its own. The UI keeps the 20 most recent events.

## Demo script

The full version is in [`docs/demo-script.md`](../demo-script.md), including the
pre-demo checklist. Summary of the path through the screen:

1. pick an applicant in the selector — the dropdown says how deep its group runs,
   from 1 to 6 levels — and start at depth 1: the applicant and who controls it
   directly, nothing overdue;
2. raise the depth on the six-level group — every step reveals companies the
   previous one did not, up to 43 at depth 6, and the arrears appear in a branch
   the applicant is not part of. Then ask for depth 6 on the one-level group: same
   answer, same latency, because the traversal ends when the tree ends;
3. the consolidated exposure panel: the number the credit decision needs, which is
   in no single record;
4. Atlas Search with a partner's name spelled differently, scoped to the group;
5. Concentration — eight CNAE codes, and how many distinct businesses actually hide
   behind them;
6. the bridge shareholder: one individual in two groups the registry treats as
   unrelated;
7. **Open review** — ACID transaction over the whole group, with the case card and
   the dashed nodes on the graph;
8. the Alerts tab — the change stream on `companies` already delivered the event
   for that transaction, coalesced into one, with the company count and the
   exposure now under review;
9. **Close case** — the counterpart event arrives by the same path, which is what
   proves the listener reads state instead of firing on its own;
10. switch to **Visibility**: manager, then advisor, then the refusal on the same
    company;
11. close on architecture: one less system to operate, synchronise and secure.
