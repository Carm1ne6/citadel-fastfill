# Prompts for Claude Code

Open Claude Code in the repository folder (`git clone https://github.com/Carm1ne6/citadel-fastfill && cd citadel-fastfill && claude`)
and paste one of these. Each prompt says what to run and how to report it; Claude Code also reads `CLAUDE.md` and the
`citadel-fastfill` skill, and the slash commands (`/replay`, `/live`, `/explain`, `/sensitivity`, `/sec-check`,
`/add-venue`) do the short versions.

| prompt | what you get |
|---|---|
| [01_setup_and_replay.md](01_setup_and_replay.md) | install, test, record 10 minutes of public order books, replay them, the report next to the SEC's numbers |
| [02_explain_the_sec_order.md](02_explain_the_sec_order.md) | the SEC order, paragraph by paragraph, against the code that implements it, with live examples |
| [03_sensitivity.md](03_sensitivity.md) | the latency, round-lot and threshold sensitivity, and which conclusions depend on our choices |
| [04_add_binance_okx.md](04_add_binance_okx.md) | Binance and OKX added as fifth and sixth venues, checked and tested |
| [05_markout_my_fills.md](05_markout_my_fills.md) | a markout study of your own fills: adverse selection, measured with our markout version of the patent's profit-analyzer tab |

Everything runs on public market data. Nothing places orders, and nothing needs an API key.
