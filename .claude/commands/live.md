---
description: Run the FastFill / SmartProvide rules on live public BTC/USD order books for a while and summarize
argument-hint: "[seconds, default 180] [live options such as --rules FASTFILL,TRADE_OUT --venues coinbase,kraken]"
allowed-tools: Bash(citadel-fastfill:*), Bash(pip install -e:*)
---
Watch the rebuild work on the live public feeds of Coinbase, Kraken, Bitstamp and Gemini. Only public market data
is read; nothing is sent to any venue besides the subscribe messages.

Arguments: `$ARGUMENTS`

1. If `citadel-fastfill --help` fails, run `pip install -e ".[test]"` from the repository folder.
2. The run length is the first argument if it is a number of seconds, else 180 (no order is handled in the first 60 s,
   the warm-up). Pass the other arguments through.
3. Run `citadel-fastfill live --seconds <n> <the other arguments>` (with a timeout a little longer than the run).
4. Report what happened: event counts by rule, a few typical event lines (one FastFill with its gap, one trade out
   with its result, one SmartProvide display if any), and the final summary block. Note that the toxicity analyzer
   screens nothing until 300 outcomes are known, so a short run has a principal interest in every order.
