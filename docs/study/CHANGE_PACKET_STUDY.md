# Change-packet falsification study (T8)

**Status: prepared, not run.** No participants have been recruited. Until the
study is run and scored, the change packet is an **unvalidated prototype**.
An unrun, partial or failed study is never reported as a success, and agent
or maintainer self-tests are not participants.

## What the study tests

Hypothesis: for the question "what changed for this issuer since my previous
brief, and what can I verify?", the change packet (`python marketbot.py
packet`) makes people faster **without** more wrong conclusions than reading
the same saved sources by hand.

The study is designed so it can disprove this. The workflow is narrowed or
rejected in any of these cases:

- fewer than four of six people complete the task correctly;
- speed improves only because people skip caveats;
- most participants would rather have coverage diagnostics (T8 card, "Job C").

## Participants

Six consenting adults, recruited through a separately authorised channel
(this repository does not recruit or contact anyone):

- three people who regularly inspect issuers;
- two research or data engineers;
- one clinical or regulatory researcher.

Record consent before any task. Use pseudonymous IDs (P1–P6). Keep filled
recording sheets **outside** this repository; they contain study data, not
code.

## Materials

Two matched synthetic cases live in `fixtures/study/`. Every issuer, price,
filing and report in them is fabricated. Each case has the same structure:

- an amendment that corrects one figure and leaves another unchanged;
- two news reports repeating the original figure (one disclosure origin);
- a notice whose time is known only to the day;
- a report that conflicts with that notice;
- one degraded source in the current run;
- a descriptive price change that is not caused by any of the above.

| Case | Issuer (fictitious) | Corrected figure | Notice and conflict | Degraded source |
|---|---|---|---|---|
| A | ALFA, Alfa Minerals | Q2 revenue 20 → 18 USD million; EPS unchanged | Mill restart vs "restart delayed" | SEC |
| B | BRAV, Bravo Therapeutics | Enrollment 120 → 112 patients; cash unchanged | Meeting scheduled 2026-10-12 vs "approval granted" | News |

Build a fresh folder for each participant and task:

```powershell
python change_packet_study.py build case_a study-runs/P1-A --manual   # manual condition
python change_packet_study.py build case_b study-runs/P1-B            # packet condition
```

The builder refuses a non-empty folder and **never copies the answer key**.
The answer keys (`fixtures/study/case_*/answer_key.json`) are for the
facilitator only. The repository is public, so run each session from a
prepared folder and a prepared environment, not from a repository checkout
the participant can browse.

## Counterbalanced order

Each person does one case per condition and never sees a case twice. Across
six people, each case appears three times in each condition, and each
condition goes first three times.

| Slot | First task | Second task |
|---|---|---|
| P1 | A, manual | B, packet |
| P2 | B, manual | A, packet |
| P3 | A, packet | B, manual |
| P4 | B, packet | A, manual |
| P5 | A, manual | B, packet |
| P6 | B, packet | A, manual |

Spread the roles across slots where recruitment allows (for example, issuer
researchers in P1, P3 and P5), and record the role on every row.

## Procedure (per task, 15 minutes)

1. Give the participant the folder's `README.txt`. In the packet condition
   the README shows the packet command; the viewer and `inspect`/`diff`
   stay available in both conditions.
2. Ask the four questions:
   1. What changed for the issuer?
   2. What is actually supported, and by which source?
   3. How many underlying origins support the changed figure?
   4. What cannot be concluded?
3. Stop at 15 minutes. Record the time, answers, help requests, and whether
   the participant could retrace one citation to its file and pointer.

Record installation or setup friction separately (`install_friction_minutes`);
it is not part of task time.

## Scoring

Score answers against the case's answer key.

- **`all_four_correct`**: yes only if every question's key points are
  present. Omitting a key "cannot conclude" point is not correct.
- **`critical_false_conclusion`**: yes if the participant asserts any item in
  the answer key's `critical_errors`. Examples: the superseded figure as
  current, two reports counted as independent confirmations, an approval as
  established, a price change as caused by the news, "nothing else happened"
  despite the degraded source.
- **`critical_attributable_to_interface`**: the facilitator's judgement that
  the packet's wording or layout led to the error. Explain it in `notes`.

Fill in `docs/study/recording_sheet.csv` (a copy stored outside the
repository), one row per task, then:

```powershell
python change_packet_study.py score path\to\filled_sheet.csv
```

## Decision gates

These are the thresholds from the methodology review. They are design
thresholds, not results.

| Gate | Pass condition |
|---|---|
| Unaided completion | At least 4 of 6 packet-condition tasks answered fully correctly, without help, within 15 minutes |
| Speed | Median time of correctly answered packet tasks is at least 20% lower than for correctly answered manual tasks |
| Safety | Zero critical false conclusions attributable to the interface |

The scorer reports `pending` until six consenting participants have a packet
task recorded. If any gate fails, the overall result is `fail`.

Report the descriptive counts alongside the gates: missed caveats, help
requests and retraced citations. Publish disconfirming observations. Do not
redefine success after seeing the results; a change to the gates needs a new
study.

## Recording the outcome

Record the outcome in the next handoff as `pass`, `fail` or `pending`, with:

- the scorer's output;
- counts by role;
- notable disconfirming observations.

A failed or pending study blocks any claim that the workflow is useful. A
failure should narrow the workflow, and may shift the primary job to coverage
diagnostics.
