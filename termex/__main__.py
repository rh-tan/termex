"""CLI: python -m termex <bookmap> [--flat|--structural] [--top N]

Emits a ranked CSV with every score component broken out. The candidate
table is cached next to the output because parsing is the slow part and
both modes score the same table.
"""
from __future__ import annotations

import argparse
import re
import csv
import pickle
import sys
from pathlib import Path

from .candidates import CandidateTable, extract
from .score import COLUMNS, rank, score_table, to_row
from .substrate import load_book


def load_table(bookmap: Path, cache_dir: Path, use_cache: bool = True,
               product: str | None = None,
               exclude: tuple[str, ...] = ()) -> CandidateTable:
    tag = "".join(f".x{re.sub(r'[^A-Za-z0-9]+', '-', e)}" for e in sorted(exclude))
    cache = cache_dir / f"{bookmap.stem}{tag}.candidates.pkl"
    if use_cache and cache.exists() and cache.stat().st_mtime > bookmap.stat().st_mtime:
        with cache.open("rb") as f:
            return pickle.load(f)
    import spacy  # deferred: eval scripts should not pay for it on a cache hit
    nlp = spacy.load("en_core_web_lg", disable=["ner"])
    topics = load_book(bookmap, product=product, exclude_keys=exclude)
    print(f"parsed {len(topics)} topics from {bookmap.name}", file=sys.stderr)
    table = extract(topics, nlp)
    print(f"{len(table.cands)} candidates from {table.n_tokens} prose tokens", file=sys.stderr)
    cache_dir.mkdir(parents=True, exist_ok=True)
    with cache.open("wb") as f:
        pickle.dump(table, f)
    return table


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="termex")
    ap.add_argument("bookmap", type=Path)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--flat", action="store_const", dest="mode", const="flat")
    mode.add_argument("--structural", action="store_const", dest="mode", const="structural")
    ap.add_argument("--top", type=int, default=100)
    ap.add_argument("--out", type=Path, default=Path("out"))
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--exclude", action="append", default=[],
                    help="drop topics whose path contains this substring (repeatable); "
                         "used to keep gold-set definition topics out of the corpus")
    ap.add_argument("--product", default=None,
                    help="keep only elements whose product= includes this value "
                         "(e.g. 'editor' for the Oxygen guide); default: no filtering")
    args = ap.parse_args(argv)
    mode = args.mode or "structural"

    table = load_table(args.bookmap, args.out, use_cache=not args.no_cache,
                       product=args.product, exclude=tuple(args.exclude))
    rows = rank(score_table(table), mode)[: args.top]
    out = args.out / f"{args.bookmap.stem}.{mode}.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for i, r in enumerate(rows, 1):
            w.writerow(to_row(i, r, mode))
    print(f"wrote {out}", file=sys.stderr)
    for i, r in enumerate(rows[:30], 1):
        d = to_row(i, r, mode)
        print(f"{i:3} {d['score']:6.2f}  {d['term']:<40} f={d['freq']:<4} t={d['topics']:<3} "
              f"cv={d['cvalue']:<7} pmi={d['pmi']:<5} ti={d['in_title']} sd={d['in_shortdesc']} "
              f"def={d['definition']} gen={d['genericness']} td={d['title_dominance']}")


if __name__ == "__main__":
    main()
