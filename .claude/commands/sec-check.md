---
description: Put a run's numbers next to the SEC order's, paragraph by paragraph, and say why each differs
argument-hint: "[--run out/run1]"
allowed-tools: Bash(citadel-fastfill:*), Read, Grep
---
Compare a replay with the numbers of the SEC order (Release No. 33-10280).

Arguments: `$ARGUMENTS`

1. The run is `--run <folder>` if given, else the newest folder under `out/` with a `summary.json` (if there is none,
   offer `/replay`).
2. Run `citadel-fastfill sec-check --run <run>` and `citadel-fastfill sec-check --run <run> --json`. A non-zero exit
   means the summary does not quote `params.SEC_NUMBERS` unchanged: report that first.
3. Present a table: metric, SEC number with its paragraph or footnote, this run. Then explain the gaps with
   RULES.md section 10 (read it): crypto books are full of odd lots, public taker orders are not retail orders,
   SmartProvide fires when the feed it reads has moved away from the SIP, the trade-out fill rate depends on our
   50 ms delay, the toxicity model's limits.
4. Close with what the order required Citadel Securities to pay (Section V, item C): $5,200,000 disgorgement +
   $1,465,268 prejudgment interest + $16,000,000 civil money penalty = $22,665,268, neither admitting nor denying the
   findings.
