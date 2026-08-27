# 03 — Interface and flows

The visual design follows a token set shared across the demonstration projects:
dark background, green as the signal for action and success, blue for information,
amber for warning and red for risk. `src/pov-signature.css` carries that signature
and is imported after the application stylesheet.

## Stage mode

One screen. The first viewport holds **one thesis** (a transaction on its own looks
legitimate), **one action** (the traversal depth) and **one piece of evidence** (the
graph with ring nodes in red and the measured response time).

Anything that decides nothing for the presenter is out of the way: the executed
query lives in a `<details>`, and the Search, Vector and alert panels sit beside
the graph, in script order.

## Layout — one screen, no scrolling

```
┌─ topbar 52px: brand · one-line thesis · health ────────────────────────┐
├──────────┬────────────────────────────────────────┬────────────────────┤
│ controls │ metrics: nodes · edges · in ring ·      │ inspector (tabs)   │
│          │          blocked · time · hop · legend  │ ┌────────────────┐ │
│ entry    │ ┌────────────────────────────────────┐  │ │Node│Search│Sem…│ │
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

Three patterns borrowed from graph investigation tools:

- **Neighbour highlighting.** Hovering a node lights up its direct neighbours and
  dims everything else to ~16% opacity. The chain that matters is alone on screen.
  The focused node **keeps its type colour** and gains a blue border — swapping the
  fill would erase the "this is in a ring" information.
- **Progressive disclosure.** No native tooltip: the node detail (hops, degree,
  accounts and when they were opened, risk flags, `_id`) lives in the **Node** tab
  of the inspector, along with the direct neighbours as clickable shortcuts.
- **Re-settling on drag.** The layout uses `forceAtlas2Based` with
  `avoidOverlap: 0.85`. Physics stays **frozen at rest** — the design system
  forbids continuous ambient animation — and comes back during a drag, so nearby
  nodes make room instead of overlapping. It freezes again once the engine settles,
  with a 2.5 s ceiling.

Node size scales with the square root of degree within the subgraph: whoever
concentrates links stands out before any label is read. Ring nodes get a discreet
red halo. Blocked nodes get a dashed border.

Edge width is 2.4 px, 3 px for device edges and 4.5 px on focus. Colour **is** the
information here (blue device, amber address, green destination PIX key), and at
1 px on a dark background the eye cannot separate blue from green at projector
distance.

Framing controls (`+`, `−`, `⤢`) sit over the canvas: after dragging or zooming,
the presenter returns to the framing without reloading.

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
something that used to be impossible to show: injecting the same transaction
**with** and **without** the network flagged.

`POST /api/demo/simulate-transaction` accepts `person_ids` — the network on screen
— and returns `expect_alert`. When nothing is flagged, the screen says in green
that no alert is the **correct** outcome, not a stalled demo.

Scoping to `person_ids` is not a detail: without it, after closing the case the
search for a blocked account found an open case from another network, the
transaction landed there and the alert fired with the on-screen network already
free. The A/B was lying.

The Alerts tab renders both kinds of event: the red `ring_touch` alert and the grey
`checked` line saying how many accounts the listener looked at and in how many
milliseconds. Silence had to have a shape.

## Nothing but an alert steals the tab

Every SSE event used to call `setTab('alerts')`. During a large `update_many` on
`transactions` — the embedding backfill — the interface jumped to Alerts every few
seconds and it was impossible to type into a search box. Now only `ring_touch`
switches tabs; edge maintenance and checks stay in the list without hijacking the
screen.

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

## States

| State | What the screen does |
|---|---|
| Backend down | `✕ backend offline` badge; the canvas says what happened |
| Search/Vector index `BUILDING`/`MISSING` | badge on that feature's panel and a notice explaining; the graph keeps working |
| No `VOYAGE_API_KEY` | same path, with status `NO_EMBEDDING_KEY` |
| Entry point with no links | empty canvas with text, not an infinite spinner |
| Truncated result | notice pointing at `LIMITATIONS.md §4`, with the real edge count found |
| No alerts yet | text saying which action produces one, not a mute void |
| Flagging at a shallow depth | notice saying how many nodes this expansion would actually block |

No state is communicated by colour alone: a ring node has a thicker border and its
`ring_id` in the panel on top of the red; a blocked node has a dashed border on top
of the "Blocked" metric.

## Streaming

Alerts arrive over `EventSource` at `/api/alerts/stream`. The backend sends a
heartbeat every 15 s so the browser does not consider the idle connection dead;
`EventSource` reconnects on its own. The UI keeps the 20 most recent events.

## Demo script

The full version is in [`docs/demo-script.md`](../demo-script.md), including the
pre-demo checklist. Summary of the path through the screen:

1. pick a clean account in the selector — small graph, nothing happening;
2. switch to the account under suspicion, depth 1;
3. go to 2, then 3 — the network appears whole, the red nodes close;
4. uncheck edge types one at a time — without the device edge the network collapses
   from 31 nodes to 3, which is the hypothesis being tested live;
5. go to 4 and 5 — the graph saturates: time to talk about fan-out and the pruning
   toggle;
6. Atlas Search with the misspelled name that exact-equality `$graphLookup` would
   never find, and the in-network/outside tags;
7. Vector Search scoped to the ring — eight ways of saying the same thing;
8. **Flag N nodes for investigation** — ACID transaction, with the case card and
   the dashed nodes on the graph;
9. **Inject transaction into the network** — the alert appears via change stream;
10. **Close case** and inject again — no alert, and the screen says that is correct;
11. close on architecture: one less system to operate, synchronise and secure.
