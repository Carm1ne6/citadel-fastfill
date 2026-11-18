"""Check the venue conventions of NOTES.md on a recording: the rebuilt books never cross, every Kraken checksum matches,
and each venue's trade side is the taker's: a taker buy prints at or above the mid of the book before the trade, a
taker sell at or below it. Bitstamp's book channel can arrive before a trade it already reflects, so for Bitstamp the
side is also checked against its order ids: the taker is the newer order, the one with the larger id.

    python -X utf8 check_venues.py data/run_final
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from citadel.venues import BUY, SELL, iter_records, make_venue  # noqa: E402

VENUES = ("coinbase", "kraken", "bitstamp", "gemini")


def bitstamp_newer_side(raw):
    """Side of the newer order of a Bitstamp live_trades message (larger order id = placed later = the taker)."""
    d = json.loads(raw).get("data") or {}
    try:
        return BUY if int(d["buy_order_id"]) > int(d["sell_order_id"]) else SELL
    except (KeyError, TypeError, ValueError):
        return None


def check_venue(name, path):
    """Replay one venue file. Returns a dict of counts (see main() for the meaning)."""
    v = make_venue(name)
    s = {"venue": name, "messages": 0, "book_messages": 0, "crossed": 0, "trades": 0, "trades_checked": 0,
         "taker_side_ok": 0, "buys": 0, "buys_ok": 0, "sells": 0, "sells_ok": 0, "resets": 0,
         "id_checked": 0, "id_ok": 0}
    for rec in iter_records(path):
        if v.live:
            b, _ = v.bids.best()
            a, _ = v.asks.best()
            mid = 0.5 * (a + b) if (a is not None and b is not None) else None
        else:
            mid = None
        changed, trades = v.on_record(rec)
        s["messages"] += 1
        if changed and v.live:
            s["book_messages"] += 1
            b, _ = v.bids.best()
            a, _ = v.asks.best()
            if b is not None and a is not None and b >= a:
                s["crossed"] += 1
        for side, px, _size, _key in trades:
            s["trades"] += 1
            if name == "bitstamp":
                newer = bitstamp_newer_side(rec["raw"])
                if newer is not None:
                    s["id_checked"] += 1
                    s["id_ok"] += newer == side
            if mid is None:
                continue
            s["trades_checked"] += 1
            ok = (px >= mid - 1e-9) if side == BUY else (px <= mid + 1e-9)
            s["taker_side_ok"] += ok
            k = "buys" if side == BUY else "sells"
            s[k] += 1
            s[k + "_ok"] += ok
    s["resets"] = v.n_resets
    s["checksums"] = getattr(v, "n_crc", None)
    s["checksums_bad"] = getattr(v, "n_crc_bad", None)
    return s


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help"):
        print("usage: citadel-fastfill check-venues [DATA_FOLDER]   (default data/probe)\n\n"
              + __doc__.split("\n\n")[0])
        return
    folder = sys.argv[1] if len(sys.argv) > 1 else "data/probe"
    if not os.path.isdir(folder):
        raise SystemExit(f"{folder}: no such recording folder")
    print(f"{folder}: book never crossed, Kraken checksums, taker side vs the mid of the book before the trade")
    for name in VENUES:
        path = os.path.join(folder, f"{name}.jsonl.gz")
        if not os.path.exists(path):
            continue
        s = check_venue(name, path)
        n = s["trades_checked"]
        extra = "" if s["checksums"] is None else f", checksums {s['checksums']:,} ({s['checksums_bad']} bad)"
        if s["id_checked"]:
            extra += f", side = newer order {s['id_ok']:,} of {s['id_checked']:,}"
        print(f"  {name:<9}{s['messages']:>8,} msgs, crossed {s['crossed']} of {s['book_messages']:,} book states"
              f"{extra}, (re)connects {s['resets']}")
        print(f"  {'':<9}{s['trades']:>8,} trades: taker side vs mid {_pct(s['taker_side_ok'], n)}"
              f" (buys at or above {_pct(s['buys_ok'], s['buys'])} of {s['buys']:,}, sells at or below "
              f"{_pct(s['sells_ok'], s['sells'])} of {s['sells']:,})")


def _pct(a, b):
    return f"{100 * a / b:5.1f}%" if b else "    -"


if __name__ == "__main__":
    main()
