# Real-data review: result

Computed with `python -m lumen_lupa.revision puntaje`, following REVIEW_PROTOCOL.md version 1.0.

- Week: 2026-09-28 to 2026-10-04
- Alert list sha256: `b63f43c338c985487fc4d5ea38a11765f61f3f66abbc68100094a1a99bfc1bc2`
- Seed: `0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef`
- Sample size (n): 25
- Status: complete (`completo`)

## Result by signal

| Signal | Mode | Alerts | Reviewed | `consistente` | `falso_positivo` | `indeterminado` | False-positive bound | Agreement | Passes |
| --- | --- | ---: | ---: | --- | --- | --- | --- | --- | --- |
| S1 | census | 10 | 10 | 7 (59.39% of weight) | 2 (39.02% of weight) | 1 (1.59% of weight) | n/a (census) | 4 of 4 (100.00%), kappa undefined | **no**, low-confidence: false positives hold 39.02% of the reviewed weight (at most 20%) |
| S5 | sample | 40 | 25 | 18 of 25 (72.00%) | 3 of 25 (12.00%) | 4 of 25 (16.00%) | 24.8% | 8 of 9 (88.89%), kappa 0.625 | **yes** |

Labels (section 7): `consistente`, consistent with the pattern; `falso_positivo`, false positive; `indeterminado`, indeterminate. In a census each label shows its count and its share of the reviewed weight; in a sample, its count out of n.

Pass rule (section 9). Census: false positives hold no more than 20% of the reviewed weight, and indeterminate alerts no more than a third of it. Sample of 25: no more than 4 of the 25 are false positives, and no more than 8 are indeterminate. The false-positive bound is the one-sided 90% Clopper–Pearson upper bound for the share of false positives (sample only).

## Agreement between reviewers (section 8)

> We report agreement on the label (share of matches and Cohen's kappa), computed on the answers as first given, before any disagreement is settled.

The second reviewer answered the 13 of the 35 reviewed alerts that the seed assigns (at least a third of each signal, rounded up). Label agreement on the answers as first given: 12 of 13 (92.31%); Cohen's kappa: 0.639.

> If agreement is below 70%, every reviewed alert gets a second set of answers before scoring.

> Disagreements are settled question by question against the rubric. Each one is listed with its resolution, and the score uses the settled answers.

| Alert | Signal | Questions that differ | First reviewer | Second reviewer | Settled | Resolution |
| --- | --- | --- | --- | --- | --- | --- |
| S5-A-006 | S5 | q2 | `indeterminado` | `consistente` | `consistente` | Q2 yes: the dossier shows 92% of the value returning to the entity where the cycle started, above the 90% bar. |
| S5-B-003 | S5 | q4 | `falso_positivo` | `falso_positivo` | `falso_positivo` | Q4 no: the payments to third parties have no public label or document, and Q4 needs one. |

The scores above use the settled answers: the first reviewer's answers, replaced by the settled answers for each disagreement.

## Deliverable 1

**Met.** At least one signal passes: S5.

## What passing means (section 9)

> With 25 alerts drawn by weight, a signal whose flagged volume is 10% wrong passes about 90% of the time; one that is 30% wrong passes about 9% of the time. Four false positives out of 25 means that, with 90% confidence, less than 30% of the flagged volume is wrong (one-sided Clopper–Pearson bound: 29.5%). A census has no sampling error, so its limit is the 20% target itself; a sample needs a margin, which is why its limit is 4 of 25.

Recomputed for n = 25: P(at most 4 false positives | 10% wrong) = 0.902; P(at most 4 | 30% wrong) = 0.090; one-sided 90% Clopper–Pearson bound for 4 of 25: 29.5%.

> **Deliverable 1 is met if at least one signal passes.** A signal that does not pass is reported with its numbers and marked low-confidence, and its volume is not subtracted in report #1.
