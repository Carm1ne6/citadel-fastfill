"""Cut the parser fixtures in tests/fixtures/ from the 24 s probe recording (data/probe), so the parser tests run on
real venue messages without the recordings (data/ is not in git).

    python -X utf8 tests/make_fixtures.py

Kept as received, in order, from each venue's first messages: Kraken's snapshot and its first updates (each with the
venue's CRC32 checksum), Bitstamp's full-book messages and trades, Coinbase's l2update and match messages, Gemini's
updates and trades. Two messages are cut to keep the files small, and only their depth changes: Coinbase's
full-book snapshot and Gemini's first l2_updates (the full book) keep their best 200 levels per side.
"""
import gzip
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from citadel.venues import iter_records  # noqa: E402

SRC = os.path.join(ROOT, "data", "probe")
DST = os.path.join(ROOT, "tests", "fixtures")
KEEP_LEVELS = 200


def cut_coinbase(msg):
    msg["bids"] = msg["bids"][:KEEP_LEVELS]
    msg["asks"] = msg["asks"][:KEEP_LEVELS]
    return msg


def cut_gemini(msg):
    buys = sorted((c for c in msg["changes"] if c[0] == "buy"), key=lambda c: -float(c[1]))[:KEEP_LEVELS]
    sells = sorted((c for c in msg["changes"] if c[0] == "sell"), key=lambda c: float(c[1]))[:KEEP_LEVELS]
    msg["changes"] = buys + sells
    return msg


def take(venue, n_first, extra=None):
    """The first n_first records of a venue, plus up to `extra` = (kind test, count) later records."""
    out = []
    later = []
    want, n_extra = extra if extra else (None, 0)
    for i, rec in enumerate(iter_records(os.path.join(SRC, f"{venue}.jsonl.gz"))):
        if i < n_first:
            out.append(rec)
        elif want and len(later) < n_extra and rec.get("raw") and want(json.loads(rec["raw"])):
            later.append(rec)
        elif i >= n_first and len(later) >= n_extra:
            break
    return out + later


def main():
    os.makedirs(DST, exist_ok=True)
    sets = {
        "coinbase": take("coinbase", 160),
        "kraken": take("kraken", 400, (lambda m: m.get("channel") == "trade", 6)),
        "bitstamp": take("bitstamp", 40, (lambda m: m.get("event") == "trade", 6)),
        "gemini": take("gemini", 120, (lambda m: m.get("type") == "trade", 6)),
    }
    for venue, recs in sets.items():
        path = os.path.join(DST, f"{venue}.jsonl.gz")
        with gzip.open(path, "wt", encoding="utf-8") as fh:
            for rec in recs:
                raw = rec.get("raw")
                if raw is not None:
                    msg = json.loads(raw)
                    if venue == "coinbase" and isinstance(msg, dict) and msg.get("type") == "snapshot":
                        rec = dict(rec, raw=json.dumps(cut_coinbase(msg), separators=(",", ":")))
                    elif venue == "gemini" and isinstance(msg, dict) and msg.get("type") == "l2_updates" \
                            and len(raw) > 20000:
                        rec = dict(rec, raw=json.dumps(cut_gemini(msg), separators=(",", ":")))
                fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
        print(f"{path}: {len(recs)} records, {os.path.getsize(path):,} bytes")


if __name__ == "__main__":
    main()
