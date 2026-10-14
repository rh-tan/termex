"""Stage 1+2: bookmap -> topic substrate -> prose projection.

The point of this module is to *keep* the markup distinctions long enough
for scoring to use them. Nothing here is a DITA processor: no keyscope,
conref, DITAVAL or specialization handling. See the thesis note, section 8.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------- roles ----
# Which element becomes which kind of block. Anything not listed is treated
# as a transparent container (we recurse into it) unless it is in DROP.
PROSE = {"p", "li", "dd", "shortdesc", "note", "cmd", "info", "stepresult",
         "stepxmp", "sli", "lq", "desc", "choice", "chdesc", "abstract"}
HEADING = {"title"}
LABEL = {"dt", "choption"}
CODE = {"codeblock", "pre", "screen", "msgblock"}
TABLE = {"entry", "stentry"}
DROP = {"prolog", "related-links", "link", "linklist", "image", "indexterm",
        "draft-comment", "data", "metadata", "navtitle", "fn", "object"}
# Inline elements whose text is code-shaped, not prose. Dropped from the
# prose stream so spaCy never sees `processing-role="resource-only"`.
INLINE_CODE = {"codeph", "userinput", "systemoutput", "msgph", "shortcut",
               "parmname", "varname", "apiname", "option", "cmdname", "msgnum",
               "filepath"}

ROLE = {}
for tags, role in ((PROSE, "prose"), (HEADING, "heading"), (LABEL, "label"),
                   (CODE, "code"), (TABLE, "table")):
    ROLE.update({t: role for t in tags})

_WS = re.compile(r"\s+")

# Minimal conditional-processing stand-in. The Oxygen guide profiles heavily
# on product=; with no filter, sibling variants like
# <uicontrol product="editor">Project</uicontrol><uicontrol product="editorEclipse">
# Project Explorer</uicontrol> both survive and fuse into "ProjectProject Explorer".
# Keeping one deliverable is closer to what a reader sees. Not a DITAVAL engine.
# None means no filtering; set via load_book(product=...) / the --product flag.
PRODUCT: str | None = None


def _excluded(el: ET.Element) -> bool:
    prod = el.get("product")
    return PRODUCT is not None and bool(prod) and PRODUCT not in prod.split()


@dataclass
class Block:
    element: str          # DITA element name, e.g. "p", "cmd", "title"
    role: str             # prose | heading | label | code | table
    text: str
    start: int = -1       # offsets into Topic.prose_text; -1 if not prose
    end: int = -1


@dataclass
class Topic:
    key: str              # path relative to the bookmap directory
    dita_type: str        # concept | task | reference | topic | ...
    title: str
    shortdesc: str
    blocks: list[Block] = field(default_factory=list)
    prose_text: str = ""

    def for_parse(self) -> str:
        """Same length as prose_text, but every block separator becomes
        ". " so spaCy is forced to close a sentence at each block edge.
        Without this, punctuation-free list items fuse into one chunk."""
        return self.prose_text.replace("\n\n", ". ")

    def heading_texts(self) -> list[str]:
        return [b.text for b in self.blocks if b.role == "heading"]


# ---------------------------------------------------------------- maps -----
def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _href_path(el: ET.Element, base: Path) -> Path | None:
    """Local file an href points at, or None for external/HTML/absent."""
    href = el.get("href")
    if not href or el.get("scope") == "external":
        return None
    href = href.split("#", 1)[0]          # strip fragment anchors
    if not href or href.startswith(("http:", "https:", "mailto:")):
        return None
    fmt = el.get("format")
    if fmt not in (None, "dita", "ditamap") and not href.endswith((".dita", ".ditamap")):
        return None
    return (base / href).resolve()


def _walk_map(el: ET.Element, base: Path, resource_only: bool, st: dict) -> None:
    """Depth-first over one map. `st` carries seen_maps, keys, key_hrefs and
    the ordered topic list across the whole map tree."""
    tag = _local(el.tag)
    if _excluded(el):
        return
    ro = resource_only or el.get("processing-role") == "resource-only" or tag == "keydef"
    target = _href_path(el, base)

    # Key definitions: keyword text (for <ph keyref>) and href (for
    # <topicref keyref>). First definition wins, as in DITA. Collected even
    # inside resource-only maps, which is where they usually live.
    if el.get("keys"):
        kw = el.find("./topicmeta/keywords/keyword")
        if kw is not None and kw.text and kw.text.strip():
            for k in el.get("keys").split():
                st["keys"].setdefault(k, _WS.sub(" ", kw.text).strip())
        if target is not None:
            for k in el.get("keys").split():
                st["key_hrefs"].setdefault(k, target)

    if target is not None and (target.suffix == ".ditamap" or el.get("format") == "ditamap"):
        target = target.resolve()
        if target not in st["seen_maps"] and target.exists():
            st["seen_maps"].add(target)
            sub = ET.parse(target).getroot()
            _walk_map(sub, target.parent, ro, st)
    elif not ro:
        if target is not None and target.suffix == ".dita":
            st["topics"].append(target)
        elif el.get("keyref") and not el.get("href"):
            st["topics"].append(("key", el.get("keyref")))

    for child in el:
        _walk_map(child, base, ro, st)


def resolve_bookmap(map_path: Path) -> tuple[list[Path], dict[str, str]]:
    """Return (ordered unique topic paths, key -> keyword text) for a map tree.

    Submap hrefs resolve relative to the map that contains them; a seen-set
    guards against cycles; topics dedupe by resolved absolute path. Topicrefs
    that use keyref instead of href are resolved after the walk, because the
    keydefs may sit in a resource-only map anywhere in the tree.
    """
    map_path = map_path.resolve()
    st = {"seen_maps": {map_path}, "keys": {}, "key_hrefs": {}, "topics": []}
    _walk_map(ET.parse(map_path).getroot(), map_path.parent, False, st)

    out, seen = [], set()
    for t in st["topics"]:
        if isinstance(t, tuple):
            t = st["key_hrefs"].get(t[1])
            if t is None or t.suffix != ".dita":
                continue
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out, st["keys"]


# --------------------------------------------------------------- topics ----
def _inline_text(el: ET.Element, keys: dict[str, str], out: list[str]) -> None:
    """Collect text of `el` and its *inline* descendants. Block-level and
    dropped children are skipped here; the caller emits them separately."""
    tag = _local(el.tag)
    if tag in INLINE_CODE or tag in DROP or _excluded(el):
        if el.tail:
            out.append(el.tail)
        return
    if el.text:
        out.append(el.text)
    elif el.get("keyref") and not len(el):
        # <ph keyref="product"/> and friends: substitute keyword text
        out.append(keys.get(el.get("keyref"), ""))
    for child in el:
        ctag = _local(child.tag)
        if ctag in ROLE or ctag in DROP:
            if child.tail:
                out.append(child.tail)
            continue
        _inline_text(child, keys, out)
    if el.tail:
        out.append(el.tail)


def _walk(el: ET.Element, keys: dict[str, str], blocks: list[Block]) -> None:
    tag = _local(el.tag)
    if tag in DROP or _excluded(el):
        return
    if tag in ROLE:
        parts: list[str] = []
        if el.text:
            parts.append(el.text)
        elif el.get("keyref") and not len(el):
            parts.append(keys.get(el.get("keyref"), ""))
        nested = []
        for child in el:
            ctag = _local(child.tag)
            if ctag in ROLE or ctag in DROP or ctag in _CONTAINERS_WITH_BLOCKS:
                nested.append(child)
                if child.tail:
                    parts.append(child.tail)
            else:
                _inline_text(child, keys, parts)
        text = _WS.sub(" ", "".join(parts)).strip()
        if text:
            blocks.append(Block(tag, ROLE[tag], text))
        for child in nested:
            _walk(child, keys, blocks)
        return
    for child in el:
        _walk(child, keys, blocks)


# Containers that may appear *inside* a prose block (e.g. <p><dl>...</dl></p>)
# and must be walked as blocks rather than flattened into the parent's text.
_CONTAINERS_WITH_BLOCKS = {"ul", "ol", "sl", "dl", "dlentry", "table", "simpletable",
                           "fig", "note", "section", "example", "steps", "step",
                           "substeps", "substep", "choices", "choicetable", "div",
                           "steps-unordered", "lq", "codeblock", "pre"}


def parse_topic(path: Path, root_dir: Path, keys: dict[str, str]) -> Topic | None:
    if not path.exists():          # e.g. topics generated at build time
        return None
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return None
    dita_type = _local(root.tag)
    title_el = root.find("./title")
    title_parts: list[str] = []
    if title_el is not None:
        _inline_text(title_el, keys, title_parts)
    title = _WS.sub(" ", "".join(title_parts)).strip()

    blocks: list[Block] = []
    # Title first, as a heading block, so title-dominance can see it.
    if title:
        blocks.append(Block("title", "heading", title))
    for child in root:
        if _local(child.tag) == "title":
            continue
        _walk(child, keys, blocks)

    shortdesc = next((b.text for b in blocks if b.element == "shortdesc"), "")

    # ------------------------------------------------ projection ----
    # Only prose-role blocks enter the stream. Joined with "\n\n" so the
    # separator has a fixed length that for_parse() can swap for ". ".
    pos = 0
    pieces = []
    for b in blocks:
        if b.role != "prose":
            continue
        if pieces:
            pos += 2
        b.start, b.end = pos, pos + len(b.text)
        pieces.append(b.text)
        pos = b.end
    try:
        key = str(path.relative_to(root_dir)).replace("\\", "/")
    except ValueError:
        key = path.name
    return Topic(key, dita_type, title, shortdesc, blocks, "\n\n".join(pieces))


def load_book(bookmap: Path, include_types: set[str] | None = None,
              exclude_types: set[str] = frozenset({"glossentry", "glossgroup"}),
              product: str | None = None,
              exclude_keys: tuple[str, ...] = ()) -> list[Topic]:
    """Resolve a bookmap and parse every reachable topic.

    glossentry topics are excluded by default: they *are* the gold set in
    eval, and their glossdef sentences would hand the structural scorer the
    answer sheet.
    """
    global PRODUCT
    PRODUCT = product
    bookmap = bookmap.resolve()
    paths, keys = resolve_bookmap(bookmap)
    topics = []
    for p in paths:
        t = parse_topic(p, bookmap.parent, keys)
        if t is None or t.dita_type in exclude_types:
            continue
        if any(sub in t.key for sub in exclude_keys):
            continue
        if include_types and t.dita_type not in include_types:
            continue
        topics.append(t)
    return topics
