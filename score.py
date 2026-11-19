"""Field-level accuracy of each reader against the answer key (what is written on each ticket)."""
from __future__ import annotations

import json
from collections import Counter

from fields import same
from read import FIELDS, READERS, cache_path
from wells import DATA


def load():
    truth = json.loads((DATA / "world" / "truth.json").read_text())
    key = {t["ticket_no"]: t for t in truth["tickets"]}
    manifest = json.loads((DATA / "tickets" / "manifest.json").read_text())
    reads = {r: {m["image"]: json.loads(cache_path(r, m["image"]).read_text()) for m in manifest} for r in READERS}
    return truth, key, manifest, reads


def misses(reader: str):
    truth, key, manifest, reads = load()
    out = []
    for m in manifest:
        t = key[m["ticket_no"]]
        r = reads[reader][m["image"]]
        for f in FIELDS:
            if not same(f, t.get(f), r.get(f)):
                out.append((m["image"], f, t.get(f), r.get(f)))
    return out, len(manifest) * len(FIELDS)


if __name__ == "__main__":
    for r, model in READERS.items():
        ms, n = misses(r)
        print(f"{model}: {n - len(ms)}/{n} fields right ({(n - len(ms)) / n:.1%})", Counter(f for _, f, _, _ in ms))
        for x in ms:
            print("   ", x)
