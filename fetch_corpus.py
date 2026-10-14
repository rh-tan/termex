"""Fetch an evaluation corpus at the exact commit the results were produced from.

    python fetch_corpus.py dita-ot      # Apache-2.0
    python fetch_corpus.py oasis        # OASIS IPR policy: reuse and derivative works permitted
    python fetch_corpus.py oxygen       # no open licence: fetched, never redistributed

corpus/ is gitignored either way.
"""
import subprocess
import sys
from pathlib import Path

CORPORA = {
    "dita-ot": ("https://github.com/dita-ot/docs",
                "ac13c45fbd04afe9bb0800d8ade51e217386dc75", "dita-ot-docs"),      # 2026-08-21
    "oasis": ("https://github.com/oasis-tcs/dita",
              "51ac57c8a774d01587784ed9f050ff0df4b6aa3d", "oasis-dita"),        # 2026-09-02
    "oxygen": ("https://github.com/oxygenxml/userguide",
               "db722d7af0c705cc3d6969f52bd1299a20ba1770", "oxygen-userguide"),  # 2026-08-06
}

name = sys.argv[1] if len(sys.argv) > 1 else "dita-ot"
repo, commit, folder = CORPORA[name]
dest = Path("corpus") / folder
if not dest.exists():
    subprocess.run(["git", "clone", "--filter=blob:none", repo, str(dest)], check=True)
subprocess.run(["git", "-C", str(dest), "checkout", "--quiet", commit], check=True)
print(f"{name}: {dest} @ {commit[:12]}")
