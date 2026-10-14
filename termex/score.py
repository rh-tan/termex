"""Stage 4: signals and the two scoring modes.

Both modes see the same candidate table. `flat` uses only what a plain-text
extractor could know: frequency and phrase cohesion. `structural` adds the
evidence that the DITA markup encoded and two penalties.

The weights are illustrative defaults that satisfy the ordering constraints
in Appendix A of the design note: definition credit (Class A) outweighs any
corroborating signal (Class B), corroboration outweighs statistical support
(Class C), presence in a title or shortdesc earns less than owning a
definition's subject, and no penalty exceeds the definition credit.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from wordfreq import zipf_frequency

from .candidates import Candidate, CandidateTable

# Zipf 4 is the conventional boundary between low- and high-frequency words
# on the Zipf scale (van Heuven et al. 2014).
GENERIC_ZIPF = 4.0

# Structural coefficients: one assignment satisfying the note's ordering
# constraints. Class A (definition) > Class B (title, shortdesc, and the
# C-value inside `flat`) > Class C (frequency, PMI); penalties below A.
WEIGHTS = {"in_title": 1.0, "in_shortdesc": 0.7, "definition": 3.0,
           "genericness": -1.0, "title_dominance": -0.75}


@dataclass
class Scored:
    cand: Candidate
    freq: int
    cvalue: float
    pmi: float
    in_title: float
    in_shortdesc: int
    definition: float
    genericness: float
    title_dominance: float
    flat: float = 0.0
    structural: float = 0.0


def _p95(values: list[float]) -> float:
    pos = sorted(v for v in values if v > 0)
    if not pos:
        return 1.0
    return pos[min(len(pos) - 1, int(round(0.95 * (len(pos) - 1))))]


def _norm(x: float, p95: float) -> float:
    return max(0.0, min(1.0, x / p95)) if p95 > 0 else 0.0


def _parents(table: CandidateTable) -> dict[str, list[Candidate]]:
    """key -> longer candidates that contain it as a contiguous sub-phrase."""
    out: dict[str, list[Candidate]] = {k: [] for k in table.cands}
    for c in table.cands.values():
        n = len(c.tokens)
        for i in range(n):
            for j in range(i + 1, n + 1):
                if j - i == n:
                    continue
                sub = " ".join(c.tokens[i:j])
                if sub in out:
                    out[sub].append(c)
    return out


def _cvalue(c: Candidate, parents: list[Candidate]) -> float:
    # log2(1) == 0: single tokens get no C-value, as in Frantzi et al.
    # Length reward capped at 4 tokens so long headings do not win by length.
    lf = math.log2(min(len(c.tokens), 4))
    if not parents:
        return lf * c.freq
    nested = sum(p.freq for p in parents) / len(parents)
    return lf * (c.freq - nested)


def _min_pmi(c: Candidate, t: CandidateTable) -> float:
    """Weakest adjacent-pair PMI: a phrase is only as cohesive as its
    loosest join. Single tokens have no join and score 0."""
    if len(c.tokens) < 2:
        return 0.0
    n = t.n_tokens
    vals = []
    for a, b in zip(c.tokens, c.tokens[1:]):
        ab = t.bigrams.get((a, b), 0)
        fa, fb = t.unigrams.get(a, 0), t.unigrams.get(b, 0)
        if ab == 0 or fa == 0 or fb == 0:
            vals.append(0.0)
        else:
            vals.append(math.log2((ab / n) / ((fa / n) * (fb / n))))
    return min(vals)


def _genericness(c: Candidate) -> float:
    generic = sum(1 for tok in c.tokens if zipf_frequency(tok, "en") >= GENERIC_ZIPF)
    return generic / len(c.tokens)


def _in_title(c: Candidate) -> float:
    """Three tiers: 1.0 a concept title *is* this phrase; 0.5 a generic
    topic title is; 0.25 the phrase merely appears inside a topic title
    ("DITA Map" in "Creating a DITA Map")."""
    if c.title_exact >= 1.0:
        return 1.0
    if c.title_exact > 0:
        return 0.5
    return 0.25 if c.topic_title_hits else 0.0


def _title_dominance(c: Candidate) -> float:
    total = c.heading_hits + c.freq
    if total == 0:
        return 0.0
    ratio = c.heading_hits / total
    if ratio < 0.5:
        return 0.0
    # Exempt candidates with an independent anchor: a definition, or a
    # concept-grade title (title_exact >= 1.0 means a concept title, or two
    # generic-topic titles, is literally this phrase).
    if c.def_conf >= 1 or c.title_exact >= 1.0:
        return 0.0
    return (ratio - 0.5) / 0.5


def score_table(table: CandidateTable, weights: dict | None = None) -> list[Scored]:
    w = {**WEIGHTS, **(weights or {})}
    parents = _parents(table)
    rows = []
    for c in table.cands.values():
        rows.append(Scored(
            cand=c,
            freq=c.freq,
            cvalue=_cvalue(c, parents[c.key]),
            pmi=_min_pmi(c, table),
            in_title=_in_title(c),
            in_shortdesc=1 if c.shortdesc_hits else 0,
            definition=c.def_conf / 2,        # 0, 0.5 (prose), 1.0 (shortdesc)
            genericness=_genericness(c),
            title_dominance=_title_dominance(c),
        ))
    p_freq = _p95([r.freq for r in rows])
    p_cv = _p95([r.cvalue for r in rows])
    p_pmi = _p95([r.pmi for r in rows])
    for r in rows:
        # What a flat extractor knows: Class B cohesion (C-value) plus Class C
        # statistics (frequency, PMI).
        r.flat = (0.4 * _norm(r.freq, p_freq)
                  + 1.5 * _norm(r.cvalue, p_cv)
                  + 0.6 * _norm(r.pmi, p_pmi))
        # Structural: flat + Class A/B evidence from markup, minus penalties.
        r.structural = (r.flat
                        + w["in_title"] * r.in_title
                        + w["in_shortdesc"] * r.in_shortdesc
                        + w["definition"] * r.definition
                        + w["genericness"] * r.genericness
                        + w["title_dominance"] * r.title_dominance)
        r.flat, r.structural = round(r.flat, 3), round(r.structural, 3)
    return rows


COLUMNS = ["rank", "term", "key", "score", "flat", "structural", "freq", "topics",
           "cvalue", "pmi", "in_title", "in_shortdesc", "definition",
           "genericness", "title_dominance", "heading_hits", "variants",
           "def_sentence"]


def rank(rows: list[Scored], mode: str) -> list[Scored]:
    keyf = (lambda r: r.structural) if mode == "structural" else (lambda r: r.flat)
    return sorted(rows, key=lambda r: (-keyf(r), -r.freq, r.cand.key))


def to_row(i: int, r: Scored, mode: str) -> dict:
    c = r.cand
    return {
        "rank": i, "term": c.surface, "key": c.key,
        "score": r.structural if mode == "structural" else r.flat,
        "flat": r.flat, "structural": r.structural,
        "freq": r.freq, "topics": len(c.topics),
        "cvalue": round(r.cvalue, 2), "pmi": round(r.pmi, 2),
        "in_title": r.in_title, "in_shortdesc": r.in_shortdesc,
        "definition": r.definition, "genericness": round(r.genericness, 2),
        "title_dominance": round(r.title_dominance, 2),
        "heading_hits": c.heading_hits,
        "variants": " | ".join(s for s, _ in c.surfaces.most_common()),
        "def_sentence": c.def_sentence,
    }
