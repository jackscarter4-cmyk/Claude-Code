# Draft-quality judge rubric (EVAL GATE #2)

Each outbound draft is judged **solo** against this rubric (never pairwise —
position-bias mitigation). Gate: mean ≥ 4.0 across all drafts, no draft
below 3. Three drafts per rehearsal are additionally human spot-checked
(see DEMO_SCRIPT.md prep).

Score each dimension 1–5; the draft's score is the **mean of the four
dimensions**, rounded to one decimal.

## 1. Grounded
- 5 — Every fact, price, capacity, and date is either from
  `ground_truth/packages.yaml` or echoed from the guest's own message. Where
  the KB has no answer, the draft says the literal escape phrase
  ("I'll confirm and follow up") instead of guessing.
- 3 — No invented facts, but hedges vaguely where it should either answer
  from the KB or use the escape phrase.
- 1 — States any price, capacity, date, or policy not traceable to the KB.

## 2. Tone match (Macari voice: family-owned, warm, hospitality-forward)
- 5 — Warm and personal without being saccharine; sounds like a person at a
  family winery, not a ticketing system. Signs "The Macari Events Team" (or
  the club equivalent).
- 3 — Polite but generic hospitality boilerplate.
- 1 — Cold, robotic, defensive, or overfamiliar.

## 3. Concrete next step
- 5 — Proposes exactly one clear, low-friction next action (a call window,
  a site visit, a reply with two details) that a guest could do today.
- 3 — Invites a reply but with no specific action or time.
- 1 — Dead-ends ("let us know if you have questions") or asks the guest to
  do the routing work.

## 4. Brevity
- 5 — Under ~150 words, answers first, no filler paragraphs, no restating
  the guest's whole message back at them.
- 3 — Right content, one paragraph too long.
- 1 — Wall of text or padded with marketing copy.

## Judge procedure
1. Read the source message, then the draft, then score dimensions in order.
2. Output strict JSON: `{"grounded": n, "tone": n, "next_step": n, "brevity": n, "note": "<one sentence>"}`.
3. Never compare to another draft; never revise a score after seeing others.

## Offline mode note
When run without an API key the harness scores this rubric with a
deterministic heuristic (validator result → grounded; sign-off + no
boilerplate markers → tone; call-to-action pattern → next step; word
count → brevity). The eval report labels which judge produced the scores.
