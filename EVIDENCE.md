# Evidence — Lumen Lupa

*Prepared for the Instawards LATAM review, November 2026.*

Lumen Lupa estimates how much of a Stellar market's reported volume is organic and how much is manufactured by a
few accounts trading with each other. It reads Stellar's public dataset (Hubble), groups accounts into entities,
flags round-trip trading and value cycles, separates arbitrage, and is built to register each private report by
hash in a Soroban contract.

This repository is the **open part** of the project (open core, Apache-2.0): the normalized data model, the Hubble
adapter with cost caps, the labeled wash-trading simulator, the Soroban registry contract and the real-data
review tool. The detector (its
signals, thresholds and calibration) and the study-market data stay in a private repository. The review team can
get read access to it on request.

## Verify it yourself (about 5 minutes)

1. **CI:** the latest run on `main` is green: https://github.com/jose2501106-IA/lumen-lupa-kit/actions
2. **Tests on a clean machine** (Python 3.12 and [uv](https://docs.astral.sh/uv/)):

   ```bash
   git clone https://github.com/jose2501106-IA/lumen-lupa-kit && cd lumen-lupa-kit
   uv sync --locked
   uv run pytest -q
   ```

   Expected: `295 passed` (measured on November 7, 2026, on the first published version).
3. **Registry contract** (Rust 1.97.0): `contratos/atestaciones/` has 22 contract tests
   (`cargo test`). CI builds the WASM with `cargo build --release --target wasm32v1-none` and prints its sha256:
   `7d5763a6e77e1840ac77643242aa0cf863919ffa9c7fad44e8c422f5e08dd6b4`. The deployable WASM, built with
   `stellar contract build`, has a different hash (see the contract README).
4. **Simulator:** ten labeled scenarios, five of them negative (three traps, honest traders and a mechanical
   bot) that a correct detector must not flag. The ground truth ships with the data, so any detector can be scored.
5. **Real-data review, fixed in advance:** [`REVIEW_PROTOCOL.md`](REVIEW_PROTOCOL.md) (version 1.0, published on
   November 7, 2026, before the sprint) says how the detector's alerts on real Stellar data are checked: a
   calibration week and an out-of-time evaluation week, a freeze with published hashes, a seed taken from a future
   Stellar ledger, a sample drawn by volume, a four-question rubric, a second reviewer and the pass rule. The tool
   that runs it is `python -m lumen_lupa.revision`; the README runs its three commands on a synthetic example, and
   the expected outputs are in `tests/fixtures/revision/`.

## Evidence from the private repository (as of November 7, 2026)

| What | Evidence |
| --- | --- |
| Engineering history | 12 pull requests merged since October 4, 2026 (8 into `main` and 4 into the Instawards integration branch on November 7), each with CI; 401 tests passing on that branch |
| Real Stellar data | 1,795,784 trades from 3 markets over 14 days (September 21 – October 4, 2026), pulled from Hubble with a dry run first and a bytes-billed cap on every query; the whole study fits BigQuery's free sandbox |
| Data checks | The taker/maker rule held on every row checked: all pool trades of the 3 markets in the window and all their order-book trades on one full day |
| Detector demo (synthetic data) | On the labeled simulator, re-run by a separate automated verification step: flagged-trade precision 1.00 and recall 0.94 across 3 seeds (0.85 by flagged volume); 0 false positives in the 5 negative scenarios. By signal, against its own label, S1 precision is 0.78 and S5 recall 0.20, because cycles among accounts with a shared funder are flagged as round trips |
| Registry contract | Prototype with publish, query, revoke and admin rotation; 22 contract tests. On-chain it keeps only hashes, the methodology version, the time window, timestamps and the publisher's address: no token names, analyzed accounts or figures |

**Read the demo numbers with care:** the same team designed the signals and the simulator, so these figures show
that the detector does what the simulator encodes and catch regressions. Quality on real data is measured in the
sprint, with the review fixed in advance in `REVIEW_PROTOCOL.md`.

## What the Instaward sprint adds

Calibration on real Stellar data, reviewed exactly as `REVIEW_PROTOCOL.md` says (its frozen hashes and its seed go
to `COMMITMENTS.md` in this repository), market-integrity report #1 for the USDC/XLM control market, registry v1 (an
on-chain methodology registry and a verification call that any app or contract can use) deployed on testnet after
the December 16, 2026 reset, and kit v1.0 with a bilingual guide. The Statement of Work has the full scope and the
success metrics.

## Rules this project follows

- Read-only on the network, except `scripts/atestaciones.sh desplegar` and `publicar`, which the owner runs by hand
  with his own Stellar CLI identity to deploy the registry and publish attestations. No test generates on-chain
  activity (tests use `--dry-run` or a stand-in `stellar` command).
- Keys never pass through the code or the AI tooling used to build it; the builder deploys with his own Stellar CLI
  identity.
- No accusations: reports are private, use probability language and name no token or account in public.
