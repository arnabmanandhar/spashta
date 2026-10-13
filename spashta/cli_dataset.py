"""
Dataset commands: from a live store to a frozen, labelled, measured corpus.

  spashta dataset freeze DIR [--since D --until D]   hashed snapshot of the store
  spashta dataset verify DIR                         check every hash and the snapshot id
  spashta dataset index DIR                          index a snapshot into --db for evaluation
"""

from __future__ import annotations

import json
import sys

from .embed import get_embedder
from .retrieve import Index
from .store import Store


def _print(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def cmd_freeze(args) -> int:
    from .cli import parse_day
    from .dataset import snapshot
    store = Store(args.db)
    sources = {s.host: {"name": s.name, "lang": s.lang, "kind": s.kind} for s in store.sources()}
    m = snapshot.freeze(store.iter_articles(), args.dir, parse_day(args.since), parse_day(args.until, end=True),
                        args.min_chars, sources, args.note or "")
    _print({k: m[k] for k in ("snapshot_id", "articles", "window", "by_lang", "by_outlet")})
    return 0


def cmd_verify(args) -> int:
    from .dataset import snapshot
    problems = snapshot.verify(args.dir)
    for p in problems:
        print(p, file=sys.stderr)
    m = snapshot.read_manifest(args.dir)
    print(f"snapshot {m['snapshot_id']}: {m['articles']} articles, " + ("OK" if not problems else f"{len(problems)} problems"))
    return 1 if problems else 0


def cmd_index(args) -> int:
    from .dataset import snapshot
    index = Index(Store(args.db), get_embedder(args.embedder))
    articles = snapshot.load(args.dir)
    for i in range(0, len(articles), 200):
        index.add_many(articles[i:i + 200])
        print(f"indexed {min(i + 200, len(articles))}/{len(articles)}", file=sys.stderr)
    _print(index.store.count())
    return 0


def add_dataset_commands(sub) -> None:
    d = sub.add_parser("dataset", help="snapshots, sampling, labels, agreement and release")
    ds = d.add_subparsers(dest="action", required=True)

    f = ds.add_parser("freeze", help="write a hashed snapshot of the store")
    f.add_argument("dir")
    f.add_argument("--since", help="first publication date to include (YYYY-MM-DD)")
    f.add_argument("--until", help="last publication date to include (YYYY-MM-DD)")
    f.add_argument("--min-chars", type=int, default=200, help="drop articles with shorter bodies")
    f.add_argument("--note", help="free text stored in the manifest")
    f.set_defaults(func=cmd_freeze)

    v = ds.add_parser("verify", help="check a snapshot's hashes")
    v.add_argument("dir")
    v.set_defaults(func=cmd_verify)

    x = ds.add_parser("index", help="index a snapshot into --db (use a fresh db for evaluation)")
    x.add_argument("dir")
    x.set_defaults(func=cmd_index)
