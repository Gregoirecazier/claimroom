# Claims analysis evaluations

`golden_cases_v2.json` defines the five synthetic claims-analysis checks against the S15 input and gates. Version 1 remains archived. Version 2 includes case-local plate and movement observations, an S14-selected vehicle track, and dated mock coverage; the optional CCTV fixture can be absent. The runner uses the `ClaimsAnalyzer` port and defaults to `FixtureClaimsAnalyzer`, so ordinary runs make no model or provider calls and need no credentials.

From `apps/api`, run the local suite with:

```sh
uv run python -m evals.runner
```

To create a Pydantic Evals experiment in the team's **EU Logfire project**, set that project's write token in `LOGFIRE_TOKEN` and run:

```sh
LOGFIRE_TOKEN=... uv run python -m evals.runner --upload-to-logfire
```

The stable dataset name is `claims-analysis-golden-v2`; each uploaded experiment has its own name and `run_id`, and the case spans contain the named deterministic scores and compact gate/schema/source summaries. The runner does not put intake text, output prose, credentials, or case identifiers into the uploaded data. An upload request without `LOGFIRE_TOKEN` exits with an actionable error. The upload destination is selected from the Logfire token's region, so use the EU project's write token.

## PR 20 insurance probes

The separate `mock-insurance-v2` fixture supplies 20 exact lookup probes, including G1–G3 reference vehicles, the uncertain G2 Opel plate, and an absent alternative. Run them through Pydantic Evals with:

```sh
uv run --locked python -m evals.insurance_runner
LOGFIRE_TOKEN=... uv run --locked python -m evals.insurance_runner --upload-to-logfire
```

The second command sends an experiment named `claims-insurance-probes-v2-<run>` to the EU Logfire project. Its metadata records the fixture version and SHA-256, evaluator version, capability, and run ID. Each case has named checks for status, reason, coverage at the incident date, and expected synthetic insurer/correspondent identity when applicable. Reference answers are excluded from task inputs; case outputs contain statuses and identity hashes rather than plates, policy references, or insurer names. Upload is opt-in and requires `LOGFIRE_TOKEN`.

These probes exercise the exact fixture adapter only. A successful lookup for `RK18 LXP` and no match for `RK18 LYP` cannot settle the visual reading of G2; the `RK18 L?P` probe must remain ambiguous. This experiment does not evaluate video understanding, source attribution, liability, voice, SMS, dossier UX, review, or sending. The [42-row evaluation matrix](../../../docs/demo/evaluation-matrix.md) and [G1–G3 reference answers](../../../docs/demo/corriges-videos-g1-g2-g3.md) remain the source for future agent and end-to-end evaluators. Keep those answers, photo-generation prompts, and media provenance out of agent inputs; G1 variants belong to one scene group when measuring generalization.

## S16 offline inventory

Run the cost-free inventory and both existing deterministic suites:

```sh
uv run --locked python -m evals.matrix_runner --output-dir /tmp/s16-report
```

The command writes a dated JSON report and a Markdown summary. It validates all 42 matrix IDs, the separate reference file, and SHA-256 hashes of nine local G1–G3 media fixtures. Its two passing sub-suites (five analysis cases and 20 exact insurance probes) are reported separately: all 42 agent/UX matrix rows remain `not_tested` until their specific evidence exists. `L02` additionally identifies the missing after-impact clip. The runner has no opt-in live mode, calls no model/provider, uploads nothing to Logfire, and needs no database or account. CI executes this same command. See [S16 offline guide](../../../docs/demo/s16-offline-guide.md) for fixtures, isolation rules and the pending end-to-end recipe.
