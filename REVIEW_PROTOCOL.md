# Real-data review protocol

*Version 1.0. Pre-registered on November 7, 2026, before the Instawards sprint (November 23 – December 22,
2026). Changes made after the evaluation seed is known (section 5) do not apply to that evaluation.*

> **Resumen en español.** Este protocolo fija, antes de ver resultados, cómo se mide si el detector de Lumen Lupa se
> equivoca con datos reales de Stellar: qué se revisa, cómo se elige la muestra con una semilla que nadie controla,
> cómo se etiqueta cada alerta y qué cuenta como aprobado. La herramienta que elige la muestra y calcula el resultado
> es pública: `python -m lumen_lupa.revision`.

## 1. What this protocol decides

The detector is private. Its results on real data are checked by people, using this protocol and a public tool, so
anyone can see that the check was fixed in advance and was run as written. The protocol decides two things:

1. **Deliverable 1 (detector calibrated on real data) is met** only if at least one signal passes the out-of-time
   review in section 9.
2. **Report #1 subtracts only confirmed alerts.** Every alert in the USDC/XLM control market over the 14 days is
   reviewed with the same rubric (if there are more than 40, the 40 with the largest share of the market's 14-day
   volume; the rest stay unconfirmed). Only alerts labeled `consistente` reduce the organic-volume estimate. The others are listed as
   unconfirmed and are not subtracted.

## 2. Data and split

- **Markets:** the three markets of the study, from September 21 to October 4, 2026 (UTC). Public outputs name only
  the control market (USDC from Circle against native XLM). The other two appear as Market B and Market C.
- **Calibration week:** September 21–27. Rules may change while its alerts are reviewed.
- **Evaluation week:** September 28 – October 4. Its alerts are reviewed only after the rules are frozen
  (section 4). This is an out-of-time check, the standard way credit-risk models are validated.
- During calibration only **general** changes are allowed: a rule that applies to every entity, a threshold, or a
  public label with its source. No rule may name a specific account or entity. An entity can appear in both weeks;
  this restriction is what keeps the evaluation week a fair test.

## 3. Unit of review

- An **alert** is what the detector reports for one signal in one market: an S1 alert (round trip) for one entity,
  or an S5 alert (value cycle) for one group of entities, with its flagged trades.
- The **weight** of an alert is its flagged volume divided by the reported volume of its market in the same week
  (same asset, one side per trade, as in the report). It is the share of that market's volume the alert would
  remove, so errors are measured in points of organic share, which is what the report claims.
- Alerts with no flagged trade volume (for example, an S5 cycle made only of transfers) remove no volume and are
  left out of this review.

## 4. Freeze

Before anyone looks at the evaluation week's alerts, the methodology is frozen as a new version, the frozen version
is run once on the evaluation week, and a commit to this repository adds an entry to `COMMITMENTS.md` with:

- the methodology version and its sha256 (`hash_metodologia`, the value the Soroban registry stores);
- the sha256 of the evaluation week's input data (`hash_datos`);
- the sha256 of the evaluation week's alert list (anonymized alert IDs, signal, market letter and weight), as
  printed by `python -m lumen_lupa.revision huella`.

There is **one** freeze. If the methodology changes after it, this evaluation is reported as failed, and the new
version needs a new evaluation on data it has not seen.

## 5. A seed nobody controls

The random seed is the hash of the **first Stellar mainnet ledger that closes at least 24 hours after the freeze
commit is pushed** (GitHub records the push time). Nobody knows that hash at freeze time and nobody can change it
afterwards. Anyone can look it up by ledger number in a Stellar explorer or in Hubble's `history_ledgers` table. The
ledger number, its close time and its hash are added to `COMMITMENTS.md`, together with the alert list, so anyone can
check the list against its frozen hash and repeat the draw.

## 6. What gets reviewed

For each signal, in the evaluation week:

- **25 alerts or fewer:** every alert is reviewed (census).
- **More than 25:** a sample of 25, drawn with probability proportional to weight and without replacement, the way
  financial audits sample by amount: large alerts are almost always reviewed, and many small correct alerts cannot
  hide one large wrong one. The draw is reproducible with `python -m lumen_lupa.revision muestra`.
  Method (Efraimidis and Spirakis, 2006): for each alert, `u = (int(sha256(seed + ":" + alert_id)) + 0.5) / 2**256`
  and `key = ln(u) / weight`, computed with 50-digit decimal arithmetic; the 25 alerts with the largest keys are
  drawn, and ties are broken by alert ID.

## 7. How each alert is labeled

Each reviewed alert has an evidence file (a dossier) with its accounts and explorer links; how they were grouped
(shared funding or fee source, and that source's history); the public StellarExpert labels of every account and
source; the share of its flagged volume whose counterparty is the same entity; its net position over the flagged
trades; the timing of those trades; and example transactions. The dossier does not show the detector's confidence
level.

The reviewer answers four questions and links the evidence for each answer:

| | Question | Answers |
| --- | --- | --- |
| **Q1** | **Common control.** Do the accounts share a funding or fee source that is not a labeled service (exchange, anchor, wallet provider, faucet), or are they linked by at least two independent signs (shared source, repeated transfers between them, a shared signer)? | `yes` · `no` (the source is a labeled service, or nothing supports a shared owner) · `unclear` |
| **Q2** | **Volume stays inside.** S1: does at least 80% of the flagged volume have the same entity on both sides, or return the entity to its starting position within the window? S5: does at least 90% of the value return to the entity where the cycle started? | `yes` · `no` |
| **Q3** | **Outside counterparties.** Does more than half of the flagged volume trade with accounts outside the entity? | `yes` · `no` |
| **Q4** | **Documented role.** Does a public label or document show a role that explains the activity (exchange or anchor operations, market making with outside takers, arbitrage across markets, payments to third parties)? | `yes` · `no` |

The label follows from the answers, in this order:

1. `falso_positivo` (false positive) if Q1 is `no`, Q3 is `yes` or Q4 is `yes`.
2. `consistente` (consistent with the pattern) if Q1 and Q2 are `yes`, and Q3 and Q4 are `no`.
3. `indeterminado` (indeterminate) in every other case.

The tool derives the label from the four answers, so a label cannot contradict its own evidence.

## 8. Second reviewer

- At least a third of the reviewed alerts (rounded up, so at least 9 of 25), chosen with the same seed, get a second
  independent set of answers from someone who did not write the detector: a member of the Stellar Mexico chapter or
  another Stellar builder. If nobody is available on the evaluation date, a language model answers with only this
  rubric and the dossiers, and the evaluation says so.
- The second reviewer does not see the first answers. We report agreement on the label (share of matches and
  Cohen's kappa), computed on the answers as first given, before any disagreement is settled.
- If agreement is below 70%, every reviewed alert gets a second set of answers before scoring.
- For that rule, agreement is measured over all doubly reviewed alerts together; it is also reported per signal.
- Disagreements are settled question by question against the rubric. Each one is listed with its resolution, and
  the score uses the settled answers.

## 9. Pass rule

For each signal:

- **Census:** the signal passes if false positives hold no more than 20% of the reviewed weight, and indeterminate
  alerts no more than a third of it.
- **Sample of 25:** the signal passes if no more than 4 of the 25 are false positives, and no more than 8 are
  indeterminate.

With 25 alerts drawn by weight, a signal whose flagged volume is 10% wrong passes about 90% of the time; one that is
30% wrong passes about 9% of the time. Four false positives out of 25 means that, with 90% confidence, less than 30%
of the flagged volume is wrong (one-sided Clopper–Pearson bound: 29.5%). A census has no sampling error, so its
limit is the 20% target itself; a sample needs a margin, which is why its limit is 4 of 25.

**Deliverable 1 is met if at least one signal passes.** A signal that does not pass is reported with its numbers and
marked low-confidence, and its volume is not subtracted in report #1.

## 10. After scoring

- **Allowed:** correcting a fact with a public source, for example an exchange label that was missing. The alert is
  answered again, the score is recomputed, and both results are published side by side.
- **Not allowed:** changing rules or thresholds, drawing the sample again, or reviewing extra alerts to change the
  result.

## 11. What is published

- **Public, in `EVIDENCE.md`:** per signal, and per market under its letter, the number of alerts, the alerts
  reviewed, the count and weight share of each label, the pass result, the agreement between reviewers, the seed and
  the frozen hashes. Volumes appear only as percentages; no per-market trade counts.
- **Private, shared with the Chapter Lead or the review team on request:** the dossiers, account addresses and the
  names of Markets B and C.

## 12. Limitations

- The same team built the detector and runs the review. The frozen rules, the external seed, the second reviewer and
  this published protocol reduce that bias; they do not remove it.
- Real data has no ground truth. A `consistente` label means the evidence matches the pattern; it is not proof of
  intent, and reports say "pattern consistent with", never more.
- With few alerts, one label can move the result. Every percentage is published next to its count.
