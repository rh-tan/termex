"""Stage 3: spaCy noun chunks -> filtered, folded candidate table.

Every candidate keeps the *evidence* of where it occurred (prose vs heading,
shortdesc, definitional subject) so that score.py can choose to use it or
ignore it. Extraction is mode-agnostic; only scoring differs.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .substrate import Topic

# Leading tokens to strip from a chunk. spaCy already tags most quantifiers
# as DET; this list catches the ones tagged ADJ and doc-typical fillers.
QUANT = {"some", "all", "many", "several", "each", "any", "every", "no",
         "other", "such", "more", "most", "various", "few", "both", "another",
         "certain", "own", "same", "following", "above", "below", "next",
         "previous", "first", "second", "last", "specific", "particular"}
STRIP_POS = {"DET", "PRON", "NUM", "PART", "PUNCT", "ADV", "SPACE"}
KEEP_POS = {"ADJ", "NOUN", "PROPN"}
MAX_TOKENS = 5

_NORM = re.compile(r"[\s\-_/]+")


def norm(text: str) -> str:
    return _NORM.sub(" ", text.lower()).strip()


@dataclass
class Candidate:
    key: str                              # normalised surface key (post-folding)
    tokens: list[str]                     # lowercase tokens of the key
    surfaces: Counter = field(default_factory=Counter)
    freq: int = 0                         # prose occurrences
    topics: set[str] = field(default_factory=set)
    heading_hits: int = 0                 # occurrences inside any heading block
    topic_title_hits: int = 0             # ... of which inside a topic <title>
    title_exact: float = 0.0              # topic title == candidate, type-weighted
    shortdesc_hits: int = 0
    def_conf: int = 0                     # 0 none, 1 copular def, 2 copular def in shortdesc
    def_sentence: str = ""

    @property
    def surface(self) -> str:
        return self.surfaces.most_common(1)[0][0]


@dataclass
class CandidateTable:
    cands: dict[str, Candidate]
    unigrams: Counter                     # token -> count over prose stream
    bigrams: Counter                      # (tok, tok) -> count over prose stream
    n_tokens: int
    n_topics: int


# Topic title credit by type. Concept titles are the canonical signal; the
# Oxygen guide writes most conceptual content in generic <topic>, so those
# still earn credit, just less. Task/troubleshooting titles are usually
# gerund phrases ("Editing a map") and earn nothing.
TITLE_WEIGHT = {"concept": 1.0, "reference": 0.75, "topic": 0.5}


def _trim(chunk):
    toks = list(chunk)
    while toks and (toks[0].pos_ in STRIP_POS or toks[0].lower_ in QUANT):
        toks.pop(0)
    while toks and toks[-1].pos_ in {"PUNCT", "PART", "SPACE"}:
        toks.pop()
    return toks


def _accept(toks) -> bool:
    """Adjective/noun/proper-noun run ending in a noun. spaCy splits
    "plug-in" into three tokens, so an inner hyphen is allowed and the token
    after it is exempt from the POS test (the "in" of "plug-in" is ADP)."""
    if not toks or len(toks) > MAX_TOKENS:
        return False
    hyphen_at = {i for i, t in enumerate(toks) if t.text == "-" and 0 < i < len(toks) - 1}
    if not hyphen_at and toks[-1].pos_ not in {"NOUN", "PROPN"}:
        return False
    if hyphen_at and (len(toks) - 1) not in {i + 1 for i in hyphen_at}             and toks[-1].pos_ not in {"NOUN", "PROPN"}:
        return False
    for i, t in enumerate(toks):
        if i in hyphen_at:
            continue
        if i - 1 in hyphen_at:
            if not t.text[0].isalnum():
                return False
            continue
        if t.pos_ not in KEEP_POS or t.is_stop or not t.text[0].isalnum():
            return False
        if t.lower_ in {"and", "or"} or "/" in t.text:
            return False
    return True


def _surface(toks) -> str:
    return "".join(t.text_with_ws for t in toks).strip()


def _is_def_subject(toks) -> bool:
    """True when the chunk's head is the nsubj of a copular 'be' whose
    predicate is nominal (attr). 'The root map is a DITA map that ...'"""
    head = toks[-1]
    if head.dep_ != "nsubj":
        return False
    verb = head.head
    # Root-level copula only: "which topics are part of" is a relative
    # clause, not a definition.
    if verb.lemma_ != "be" or verb.dep_ != "ROOT":
        return False
    # Genus-like predicate: a nominal with an article ("is a Y", "is the
    # term for") or a plural ("are documents that ..."). Rejects "The
    # default value is groove".
    attrs = [c for c in verb.children if c.dep_ == "attr" and c.pos_ in {"NOUN", "PROPN"}]
    if not any(a.tag_ in {"NNS", "NNPS"} or any(g.dep_ == "det" for g in a.children)
               for a in attrs):
        return False
    # The candidate must *own* the subject: its subtree minus a leading
    # article must equal the chunk. Rejects "the table at the right ... is",
    # "your document is", "this attribute is".
    subj = [t for t in head.subtree if not t.is_punct]
    if subj and subj[0].lower_ in {"a", "an", "the"}:
        subj = subj[1:]
    return [t.i for t in subj] == [t.i for t in toks]


def _block_at(topic: Topic, pos: int):
    for b in topic.blocks:
        if b.role == "prose" and b.start <= pos < b.end:
            return b
    return None


def extract(topics: list[Topic], nlp) -> CandidateTable:
    raw: dict[str, Candidate] = {}
    lemma_key_of: dict[str, str] = {}     # surface key -> head-lemma key
    unigrams, bigrams = Counter(), Counter()
    n_tokens = 0

    def get(skey: str, toks) -> Candidate:
        c = raw.get(skey)
        if c is None:
            c = raw[skey] = Candidate(skey, skey.split())
            lemma_key_of[skey] = norm(" ".join(
                [t.lower_ for t in toks[:-1] if t.text != "-"] + [toks[-1].lemma_.lower()]))
        return c

    # --- prose stream -------------------------------------------------
    prose_docs = nlp.pipe(((t.for_parse(), t) for t in topics), as_tuples=True,
                          batch_size=32)
    for doc, topic in prose_docs:
        words = [t.lower_ for t in doc if t.is_alpha]
        unigrams.update(words)
        bigrams.update(zip(words, words[1:]))
        n_tokens += len(words)
        for chunk in doc.noun_chunks:
            toks = _trim(chunk)
            if not _accept(toks):
                continue
            skey = norm(_surface(toks))
            c = get(skey, toks)
            c.surfaces[_surface(toks)] += 1
            c.freq += 1
            c.topics.add(topic.key)
            block = _block_at(topic, toks[0].idx)
            in_sd = block is not None and block.element == "shortdesc"
            if in_sd:
                c.shortdesc_hits += 1
            if _is_def_subject(toks):
                conf = 2 if in_sd else 1
                if conf > c.def_conf:
                    c.def_conf = conf
                    c.def_sentence = toks[0].sent.text.strip()

    # --- heading stream -----------------------------------------------
    heads = [(h, (topic, i == 0)) for topic in topics
             for i, h in enumerate(topic.heading_texts())]
    for doc, (topic, is_topic_title) in nlp.pipe(heads, as_tuples=True, batch_size=256):
        for chunk in doc.noun_chunks:
            toks = _trim(chunk)
            if not _accept(toks):
                continue
            skey = norm(_surface(toks))
            c = get(skey, toks)
            c.surfaces[_surface(toks)] += 1
            c.heading_hits += 1
            if is_topic_title:
                c.topic_title_hits += 1
            if is_topic_title and norm(doc.text) == skey:
                c.title_exact += TITLE_WEIGHT.get(topic.dita_type, 0.0)

    # --- fold plurals only when the singular already exists -------------
    cands: dict[str, Candidate] = {}
    for skey, c in raw.items():
        lk = lemma_key_of[skey]
        target = lk if lk != skey and lk in raw else skey
        if target not in cands:
            base = raw[target]
            cands[target] = Candidate(target, target.split())
            _merge(cands[target], base)
        if target != skey:
            _merge(cands[target], c)
    return CandidateTable(cands, unigrams, bigrams, n_tokens, len(topics))


def _merge(into: Candidate, other: Candidate) -> None:
    into.surfaces.update(other.surfaces)
    into.freq += other.freq
    into.topics |= other.topics
    into.heading_hits += other.heading_hits
    into.topic_title_hits += other.topic_title_hits
    into.title_exact += other.title_exact
    into.shortdesc_hits += other.shortdesc_hits
    if other.def_conf > into.def_conf:
        into.def_conf, into.def_sentence = other.def_conf, other.def_sentence
