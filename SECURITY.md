# Security policy

## Reporting a vulnerability

Please do not report security problems in a public issue. Report them privately through
a private GitHub security advisory: in this repository, open **Security → Report a vulnerability**. Include what you found, the
steps to reproduce it and the commit you tested.

In scope: the code and scripts in this repository (the Stellar adapter, the BigQuery runner and its cost caps,
the simulator, `lumen_lupa.revision`, the attestation contract and `scripts/atestaciones.sh`) and its CI.

## Findings about accounts or tokens

This project does not publish findings about real Stellar accounts or tokens as accusations. If you use the kit
and see activity that looks like wash trading or sybil behavior by a real account or token, do not post it in a
public issue or pull request; share it through a private security advisory instead.
