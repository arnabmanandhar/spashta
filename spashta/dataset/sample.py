"""
Choosing what to label.

stratified_sample() picks about 400 articles spread across language and
topic, and within each stratum across outlets, so no outlet or topic
dominates the labels. Stories that several independent outlets covered
are drawn more often (multi_weight), because corroboration needs positive
examples and a random sample of a news corpus has few. The first `pilot`
articles (spread across strata) form the pilot batch for testing the
annotation guide; `double` articles, also spread across strata, are the
ones the second annotator labels for agreement.

story_clusters() groups the snapshot into events with the same rule the
reports use (same kind of event, a shared named subject, within three
days), blocked on shared names so it scales to a few weeks of news.

candidate_pairs() proposes article pairs for same-event labels: likely
positives from the clusters, plus hard negatives (same kind of event in
the same days but no shared subject, or the same subject but a different
event) and random same-week pairs, so the matcher is tested on the cases
that actually confuse it. Annotators don't see why a pair was proposed.
"""

from __future__ import annotations

import math
import random
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from .. import story, xling
from .snapshot import to_article
from .topics import topic_of

WINDOW = story.WINDOW
MAX_POSTING = 400  # names shared by more articles than this are too common to block on


def _when(r: dict) -> datetime:
    d = datetime.fromisoformat(r.get("published") or r["fetched_at"])
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


class _UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, a: int, b: int) -> None:
        self.parent[self.find(a)] = self.find(b)


def signatures(records: list[dict]) -> tuple[list[tuple[set, set, set]], set[str]]:
    arts = [to_article(r) for r in records]
    names: set[str] = set()
    for a in arts:
        if a.lang == "en":
            names |= xling.name_keys(a.text)
    return [story.signature(a) for a in arts], names


def story_clusters(records: list[dict]) -> tuple[list[int], dict[int, int]]:
    """Cluster id per record, and the number of distinct outlet groups in
    each cluster."""
    sigs, names = signatures(records)
    when = [_when(r) for r in records]
    postings: dict[str, list[int]] = defaultdict(list)
    for i, (_events, named, keys) in enumerate(sigs):
        for k in (keys & names) | {"n:" + n for n in named}:
            postings[k].append(i)
    uf = _UnionFind(len(records))
    for ids in postings.values():
        if len(ids) < 2 or len(ids) > MAX_POSTING:
            continue
        ids = sorted(ids, key=lambda i: when[i])
        for x, i in enumerate(ids):
            for j in ids[x + 1:]:
                if when[j] - when[i] > WINDOW:
                    break
                if sigs[i][0] & sigs[j][0]:  # same kind of event, and (by blocking) a shared subject
                    uf.union(i, j)
    cluster = [uf.find(i) for i in range(len(records))]
    groups: dict[int, set] = defaultdict(set)
    for i, c in enumerate(cluster):
        groups[c].add(records[i].get("outlet_group") or records[i]["source"])
    return cluster, {c: len(g) for c, g in groups.items()}


def _allocate(sizes: dict, n: int, minimum: int) -> dict:
    """Share n across strata in proportion to sqrt(size) (so small topics
    still get labels), at least `minimum` each, never more than available."""
    alloc = {k: min(s, minimum) for k, s in sizes.items()}
    left = n - sum(alloc.values())
    while left > 0:
        room = {k: sizes[k] - alloc[k] for k in sizes if sizes[k] > alloc[k]}
        if not room:
            break
        total = sum(math.sqrt(sizes[k]) for k in room)
        shares = {k: left * math.sqrt(sizes[k]) / total for k in room}
        step = {k: min(room[k], math.floor(v)) for k, v in shares.items()}
        if not any(step.values()):  # hand out the remainder one by one, largest share first
            for k in sorted(room, key=lambda k: -shares[k])[:left]:
                step[k] = 1
        for k, v in step.items():
            alloc[k] += v
        left = n - sum(alloc.values())
    return alloc


def _spread(chosen: list[dict], k: int, rng: random.Random) -> set[str]:
    """k ids spread across strata (round robin), for the pilot and the
    double-annotated subset."""
    by: dict[tuple, list[dict]] = defaultdict(list)
    for r in chosen:
        by[(r["lang"], r["topic"])].append(r)
    queues = [rng.sample(v, len(v)) for _, v in sorted(by.items())]
    out: list[str] = []
    while len(out) < k and any(queues):
        for q in queues:
            if q and len(out) < k:
                out.append(q.pop()["id"])
    return set(out)


def stratified_sample(records: list[dict], n: int = 400, multi_weight: float = 3.0, seed: int = 13,
                      minimum: int = 8, pilot: int = 30, double: int = 100) -> list[dict]:
    rng = random.Random(seed)
    cluster, outlets_in = story_clusters(records)
    rows = []
    for r, c in zip(records, cluster):
        topic = topic_of(r["url"], r["title"], r["body"])
        rows.append({"id": r["id"], "url": r["url"], "source": r["source"], "outlet_group": r.get("outlet_group", ""),
                     "lang": r["lang"], "topic": topic, "published": r.get("published"), "cluster": c,
                     "cluster_outlets": outlets_in[c], "title": r["title"]})
    strata: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        strata[(row["lang"], row["topic"])].append(row)
    alloc = _allocate({k: len(v) for k, v in strata.items()}, n, minimum)
    chosen: list[dict] = []
    for key in sorted(strata):
        # Weighted random order (Efraimidis-Spirakis): multi-outlet stories come up more often.
        by_outlet: dict[str, list[dict]] = defaultdict(list)
        for row in strata[key]:
            w = multi_weight if row["cluster_outlets"] >= 2 else 1.0
            row["_key"] = rng.random() ** (1 / w)
            by_outlet[row["source"]].append(row)
        queues = [sorted(v, key=lambda r: -r["_key"]) for _, v in sorted(by_outlet.items())]
        rng.shuffle(queues)
        picked: list[dict] = []
        while len(picked) < alloc[key] and any(queues):  # round robin over outlets
            for q in queues:
                if q and len(picked) < alloc[key]:
                    picked.append(q.pop(0))
        chosen.extend(picked)
    for row in rows:
        row.pop("_key", None)
    pilot_ids = _spread(chosen, pilot, rng)
    double_ids = _spread([r for r in chosen if r["id"] not in pilot_ids], double, rng)
    for row in chosen:
        row["batch"] = "pilot" if row["id"] in pilot_ids else "main"
        row["double"] = row["id"] in double_ids or row["id"] in pilot_ids
    rng.shuffle(chosen)
    chosen.sort(key=lambda r: r["batch"] != "pilot")  # pilot first, then the rest in random order
    return chosen


def summary(sample: list[dict]) -> dict:
    return {"articles": len(sample), "by_lang": dict(Counter(r["lang"] for r in sample)),
            "by_topic": dict(Counter(r["topic"] for r in sample)),
            "by_outlet": dict(sorted(Counter(r["source"] for r in sample).items())),
            "multi_outlet_stories": sum(r["cluster_outlets"] >= 2 for r in sample),
            "pilot": sum(r["batch"] == "pilot" for r in sample), "double_annotated": sum(r["double"] for r in sample)}


def candidate_pairs(records: list[dict], sample: list[dict], per_article: int = 1, seed: int = 13,
                    limit: Optional[int] = None) -> list[dict]:
    """Same-event candidate pairs anchored on sampled articles: for each,
    up to per_article likely positives (cluster mates from another outlet
    group, preferring the other language), one hard negative and one
    random same-week article. Order is shuffled; the reason is kept for
    analysis but not shown to annotators."""
    rng = random.Random(seed)
    sigs, names = signatures(records)
    idx = {r["id"]: i for i, r in enumerate(records)}
    when = [_when(r) for r in records]
    cluster, _ = story_clusters(records)
    members: dict[int, list[int]] = defaultdict(list)
    for i, c in enumerate(cluster):
        members[c].append(i)
    group = [r.get("outlet_group") or r["source"] for r in records]
    by_day: dict[str, list[int]] = defaultdict(list)
    for i, d in enumerate(when):
        by_day[d.date().isoformat()].append(i)

    def near(i: int) -> list[int]:
        days = [(when[i] + timedelta(days=k)).date().isoformat() for k in range(-3, 4)]
        return [j for d in days for j in by_day.get(d, []) if group[j] != group[i]]

    pairs, seen = [], set()

    def add(i: int, j: int, reason: str) -> None:
        key = tuple(sorted((records[i]["id"], records[j]["id"])))
        if key not in seen:
            seen.add(key)
            pairs.append({"pair_id": f"{key[0]}-{key[1]}", "a": records[i]["id"], "b": records[j]["id"], "reason": reason})

    for s in sample:
        i = idx.get(s["id"])
        if i is None:
            continue
        mates = [j for j in members[cluster[i]] if group[j] != group[i]]
        mates.sort(key=lambda j: (records[j]["lang"] == records[i]["lang"], rng.random()))
        for j in mates[:per_article]:
            add(i, j, "cluster")
        pool = near(i)
        ev, named, keys = sigs[i]
        hard = [j for j in pool if j not in mates and ((sigs[j][0] & ev and not (sigs[j][2] & keys & names))
                                                      or ((sigs[j][2] & keys & names) and not sigs[j][0] & ev))]
        if hard:
            add(i, rng.choice(hard), "hard-negative")
        others = [j for j in pool if j not in mates]
        if others:
            add(i, rng.choice(others), "random")
    rng.shuffle(pairs)
    return pairs[:limit] if limit else pairs
