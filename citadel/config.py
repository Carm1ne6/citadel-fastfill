"""One configuration file for every parameter of citadel/params.py (template: fastfill.example.toml).

    citadel-fastfill replay --data data/run1 --config my.toml
    python -X utf8 run.py --data data/run1 --config my.toml        (live.py and export_film.py take it too)

The file is read with the standard library's tomllib (the tomli package on Python 3.10) and turned into KEY=VALUE
items for params.with_overrides, the parser of --set: a file and the same --set items give the same Params.
Precedence, lowest first: the defaults of params.py, the file, the flags --preset / --latency-ms / --round-lot /
--venues, the --set items. Keys sit at the top level or inside tables ([sip], [toxicity], ...); a table whose name is
not a parameter is only a heading. A parameter left out keeps its default.
"""
from dataclasses import fields

try:
    import tomllib
except ModuleNotFoundError:          # Python 3.10
    import tomli as tomllib

from .params import Params, with_overrides

# flags of run.py / live.py / export_film.py that set a parameter: the file's value becomes the flag's default, so
# the flag still wins when it is given on the command line
FLAG_PARAMS = {"preset": "preset", "latency_ms": "sip_latency_ms", "round_lot": "round_lot_btc", "venues": "venues"}
CHOICES = {"preset": ("sip", "latency"), "sp_reeval_no_pi": ("route", "internalize")}
VENUE_LISTS = ("venues", "fastfill_feeds", "smartprovide_feeds")
VENUE_NAMES = ("basis_ref", "sp_post_venue")


def _scalar(x):
    if isinstance(x, bool):
        return "true" if x else "false"
    if isinstance(x, float):
        return repr(x)
    return str(x)


def _item(key, value):
    """One TOML value as the KEY=VALUE text --set takes."""
    if isinstance(value, dict):                                   # venue_ticks = {coinbase = 0.01, ...}
        return f"{key}=" + ",".join(f"{n}:{_scalar(x)}" for n, x in value.items())
    if isinstance(value, (list, tuple)):
        parts = []
        for x in value:
            if isinstance(x, (list, tuple)) and len(x) == 2:     # venue_ticks = [["coinbase", 0.01], ...]
                parts.append(f"{x[0]}:{_scalar(x[1])}")
            else:
                parts.append(_scalar(x))
        return f"{key}=" + ",".join(parts)
    return f"{key}={_scalar(value)}"


def _flatten(data, path):
    names = {f.name for f in fields(Params)}
    flat = {}

    def put(key, value, where):
        if key not in names:
            raise SystemExit(f"{path}: unknown parameter {where}{key!r}; every parameter is listed in "
                             f"fastfill.example.toml and citadel/params.py")
        if key in flat:
            raise SystemExit(f"{path}: {key} is set twice")
        flat[key] = value

    for k, v in data.items():
        if k in names:
            put(k, v, "")
        elif isinstance(v, dict):
            for k2, v2 in v.items():
                put(k2, v2, f"[{k}] ")
        else:
            put(k, v, "")
    return flat


def validate(p, path="config"):
    """Choices and venue names: a typo stops the run instead of silently changing a rule."""
    from .venues import VENUE_CLASSES
    known = set(VENUE_CLASSES)
    for k, allowed in CHOICES.items():
        if getattr(p, k) not in allowed:
            raise SystemExit(f"{path}: {k} = {getattr(p, k)!r}; must be one of {', '.join(allowed)}")
    bad = []
    for k in VENUE_LISTS:
        bad += [(k, v) for v in getattr(p, k) if v not in known]
    for k in VENUE_NAMES:
        if getattr(p, k) not in known:
            bad.append((k, getattr(p, k)))
    bad += [("venue_ticks", n) for n, _ in p.venue_ticks if n not in known]
    if bad:
        k, v = bad[0]
        raise SystemExit(f"{path}: {k}: unknown venue {v!r}; the parsers in citadel/venues.py are "
                         f"{', '.join(sorted(known))} (CLAUDE.md, 'Add an exchange')")


def load(path):
    """The parameters a TOML file sets, as KEY=VALUE items for params.with_overrides, in file order."""
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except OSError as exc:
        raise SystemExit(f"cannot read config {path}: {exc}")
    except tomllib.TOMLDecodeError as exc:
        raise SystemExit(f"{path}: not valid TOML: {exc}")
    items = [_item(k, v) for k, v in _flatten(data, path).items()]
    p = Params()
    for it in items:
        try:
            p = with_overrides(p, [it])
        except (TypeError, ValueError) as exc:
            raise SystemExit(f"{path}: {it}: {exc}")
    validate(p, path)
    return items


def params_from(path=None, items=()):
    """Params from the defaults, a config file (optional) and KEY=VALUE items, in that order."""
    return with_overrides(Params(), (load(path) if path else []) + list(items or ()))


def add_config_argument(ap):
    ap.add_argument("--config", default=None, metavar="FILE",
                    help="TOML file of parameters (template: fastfill.example.toml); the flags and --set win over it")


def parse_args(ap, argv=None):
    """ap.parse_args(argv), plus --config FILE: the file's values become the defaults of the matching flags
    (FLAG_PARAMS) and go before the --set items, so the command line wins over the file."""
    a = ap.parse_args(argv)
    path = getattr(a, "config", None)
    if not path:
        return a
    items = load(path)
    p = with_overrides(Params(), items)
    defaults = {}
    rest = []
    for it in items:
        key = it.partition("=")[0]
        dest = next((d for d, k in FLAG_PARAMS.items() if k == key and hasattr(a, d)), None)
        if dest is None:
            rest.append(it)
        else:
            val = getattr(p, key)
            defaults[dest] = ",".join(val) if isinstance(val, tuple) else val
    if defaults:
        ap.set_defaults(**defaults)
        a = ap.parse_args(argv)
    a.set = rest + list(getattr(a, "set", None) or [])
    return a
