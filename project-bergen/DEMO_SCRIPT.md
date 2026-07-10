# DEMO_SCRIPT — 10 minutes with Gabriella Macari

Audience: Director of Operations, ex-PR. She knows their platforms work.
The pitch is **complementary**: "your systems are fine; the seams between
them leak." Never criticize Commerce7/Block55 — we sit on top.

## Prep — the morning of the meeting (before you leave)

- [ ] `export ANTHROPIC_API_KEY=...`
- [ ] `python -m audit.pipeline --live` — same-day captures of their pages
- [ ] `python -m watcher.pipeline --live --reset-state` — today's digest with
      real press pulled this morning (something she hasn't seen yet)
- [ ] `make eval` — must be 5/5. **Human spot-check three drafts** while it
      runs: `python -m approval.cli show <id>` on (1) the over-capacity
      wedding, (2) the ce-014 cancellation, (3) the Spanish inquiry. Read
      them aloud. If one sounds off, you fix it before she hears it.
- [ ] Open in tabs: `out/surface_audit.md`, today's `out/digest_*.html`
- [ ] Terminal at repo root, font large.
- [ ] Rehearsed timing: the five acts below total ≤ 10:00.

---

## Act 1 — The free gift (0:00–1:00)

Open `out/surface_audit.md`. Don't narrate the tool; hand her the findings:

> "Before we talk about software — we ran a read-only pass over your public
> pages this morning. Your site quotes **$150 and $185 for the same Private
> Suite** on two live pages. Your FAQ's page title is literally 'FAQ draft'.
> Your privacy policy says 2016. This report is yours either way."

## Act 2 — The 60-second inquiry (1:00–3:00)

Paste a fresh fake wedding inquiry live:

```bash
echo "Wedding for 175 next fall — are you available?
Hi! We loved a wedding we attended at Meadowlark. We're planning ours,
about 175 guests, fall 2027. Where do we start?  — Sam & Alex" \
| python -m responder.pipeline --paste
```

Point at the elapsed seconds. Then the gate:

```bash
python -m approval.cli list
python -m approval.cli show <the-new-id>
```

> "Draft in seconds, grounded in one file of your real offerings — and it
> goes nowhere until a human approves. **There is no send function in this
> codebase.** That's not a setting; the code can't send."

Now the kicker — the conflicted price, live:

```bash
python -m responder.pipeline --only inq-012
python -m approval.cli show <its-id>
```

> "A guest just asked which Suite price is right. The system knows the site
> contradicts itself, so it *refuses to quote either* and says it will
> confirm — Finding 1 from that report, caught at the moment it matters."

## Act 3 — The TripAdvisor review that doesn't happen (3:00–6:00)

```bash
python -m triage.pipeline --only ce-014       # the five-calls scenario, replayed
python -m approval.cli show <its-id>          # member draft + internal note
python -m triage.escalate --advance-hours 12  # 50% of SLA — it re-surfaces
python -m triage.escalate --advance-hours 12  # 100% — it pings again
python -m triage.escalate --status
tail -5 out/triage_log.jsonl
```

> "This is your real story: cancellation processed nowhere, billed again,
> five calls. Here it surfaces same-day, flags the public-review risk,
> drafts the apology and the internal action — and if nobody acts, it
> re-surfaces at 50% of SLA and again at 100%. **Nothing auto-closes,
> ever.** The only way this item dies is a named human closing it, logged.
> This is the TripAdvisor review not happening."

## Act 4 — The Gabriella moment (6:00–8:00)

Open today's digest (built from real press + reviews this morning).

> "You spent years in PR — this is the report you'd have wanted. Top: what
> the press said this week. Middle: what guests said. Bottom — **THE GAP**:
> where the narrative and the experience diverge. Jancis is writing about
> your soil; your one-stars are about billing follow-through. The wine earns
> the coverage; the seams spend it. And if nothing new happened, this
> digest is one line — it will never re-report old news to you."

## Act 5 — The numbers, and the close (8:00–10:00)

- One Meadowlark wedding: **$30–60K**. One inquiry lost to a slow shared
  inbox pays for this for years. *(Estimate: their published wedding market;
  all other figures in the demo are from their public pages.)*
- Nothing sends without staff approval — the gate is code, you saw it.
- Graded, not vibed: `make eval` — five gates, including "no invented
  price, ever" enforced by a parser, not a promise.
- **Two-week pilot**: fixtures swapped for a read-only feed of real
  inquiries + club events; your team approves drafts in the queue; we
  measure response-time delta and exceptions caught. No integration risk —
  it touches nothing in write mode.

> "Your platforms are fine. We're proposing the layer that makes sure
> nothing falls between them."

---

### Timing check (rehearse twice)
| Act | Budget | Cue to move on |
|-----|--------|----------------|
| 1 audit | 1:00 | she reaches for the report |
| 2 responder | 2:00 | conflicted-price refusal shown |
| 3 triage | 3:00 | log lines on screen |
| 4 digest | 2:00 | THE GAP read aloud |
| 5 close | 2:00 | pilot scope stated |
