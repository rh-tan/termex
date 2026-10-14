"""Derive the gold term list from the corpus's own glossentry topics.

    python eval/glossary.py <glossary-dir> [--dl <dl-terminology-topic>...] > eval/<corpus>-glossary.txt

The glossary is what the authors decided was worth defining, which is the
closest thing to ground truth this corpus offers. glossentry topics are
excluded from extraction (see substrate.load_book) so this is not circular.
"""
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

def emit(text: str) -> None:
    text = " ".join(text.split())
    if text and text not in seen:
        seen.add(text)
        print(text)


seen = set()
args = sys.argv[1:]
dl_files = args[args.index("--dl") + 1:] if "--dl" in args else []
# Terminology topics written as <dl> definition lists (the OASIS spec does
# this instead of glossentries): each <dt> is a term.
for f in dl_files:
    for dt in ET.parse(f).getroot().iter("dt"):
        emit("".join(dt.itertext()))
for path in sorted(Path(args[0]).glob("*.dita")):
    root = ET.parse(path).getroot()
    # standalone glossentry topics, or glossentries nested in a glossgroup
    entries = [root] if root.tag == "glossentry" else root.iter("glossentry")
    for e in entries:
        term = e.find("glossterm")
        if term is None:
            continue
        emit("".join(term.itertext()))
