"""Precision/recall@N for flat vs structural on the same candidate table.

    python eval/evaluate.py <bookmap> [--gold eval/glossary.txt] [--ns 25 50 100]

Matching is by normalised surface (lowercase, hyphen/space folded) against
the candidate key. A gold term the extractor never produced as a candidate
counts as a miss for both modes, which is a recall ceiling worth knowing.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from termex.__main__ import load_table          # noqa: E402
from termex.candidates import norm              # noqa: E402
from termex.score import WEIGHTS, rank, score_table  # noqa: E402


def hits_at(scored, mode, gold, n):
    top = {r.cand.key for r in rank(scored, mode)[:n]}
    return sum(1 for g in gold if g in top)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("bookmap", type=Path)
    ap.add_argument("--gold", type=Path, default=Path("eval/dita-ot-glossary.txt"))
    ap.add_argument("--ns", type=int, nargs="+", default=[25, 50, 100, 200])
    ap.add_argument("--out", type=Path, default=Path("out"))
    ap.add_argument("--product", default=None)
    ap.add_argument("--exclude", action="append", default=[])
    ap.add_argument("--ablate", action="store_true",
                    help="also report structural P@N with each signal zeroed")
    args = ap.parse_args()

    gold = {norm(l): l for l in args.gold.read_text(encoding="utf-8").splitlines() if l.strip()}
    table = load_table(args.bookmap, args.out, product=args.product,
                       exclude=tuple(args.exclude))
    scored = score_table(table)
    # A gold term matches a candidate by key, or by any surface form the
    # candidate recorded ("constraint modules" folds under "constraint module").
    by_surface = {norm(sf): c.key for c in table.cands.values() for sf in c.surfaces}
    key_of = {g: (g if g in table.cands else by_surface.get(g)) for g in gold}
    gold = {key_of[g] or g: label for g, label in gold.items()}
    ranks = {m: {r.cand.key: i for i, r in enumerate(rank(scored, m), 1)} for m in ("flat", "structural")}
    in_table = [g for g in gold if g in table.cands]
    print(f"gold terms: {len(gold)}; present as candidates: {len(in_table)} "
          f"(recall ceiling {len(in_table)/len(gold):.2f}); candidates: {len(table.cands)}\n")

    print("| N | flat P@N | flat R@N | structural P@N | structural R@N |")
    print("|---|---|---|---|---|")
    for n in args.ns:
        cells = []
        for m in ("flat", "structural"):
            hits = sum(1 for g in gold if ranks[m].get(g, 10**9) <= n)
            cells += [f"{hits/n:.2f}", f"{hits/len(gold):.2f}"]
        print(f"| {n} | " + " | ".join(cells) + " |")

    if args.ablate:
        print("\nAblation: structural P@N with one signal removed (weight 0)")
        print("| variant | " + " | ".join(f"P@{n}" for n in args.ns) + " |")
        print("|---|" + "---|" * len(args.ns))
        variants = [("full", {})] + [(f"- {k}", {k: 0.0}) for k in WEIGHTS]
        for name, override in variants:
            sc = score_table(table, override)
            cells = [f"{hits_at(sc, 'structural', gold, n)/n:.2f}" for n in args.ns]
            print(f"| {name} | " + " | ".join(cells) + " |")

    print("\n| gold term | flat rank | structural rank |")
    print("|---|---|---|")
    for g in sorted(gold, key=lambda g: ranks["structural"].get(g, 10**9)):
        f, s = ranks["flat"].get(g), ranks["structural"].get(g)
        print(f"| {gold[g]} | {f if f else '-'} | {s if s else '-'} |")


if __name__ == "__main__":
    main()
