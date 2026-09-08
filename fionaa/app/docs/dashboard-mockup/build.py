#!/usr/bin/env python3
"""Injects run-data.json into the job-dashboard.html template, producing
job-dashboard.rendered.html -- the real, self-contained page actually
published as the Artifact.

Kept separate from job-dashboard.html so the *template* (no real data) is
what lives in git; the rendered, real-data output is gitignored. Run
fetch_run.py first to (re)generate run-data.json.

Usage:
    .venv/bin/python build.py [--in run-data.json] [--template job-dashboard.html] [--out job-dashboard.rendered.html]
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

PLACEHOLDER_RE = re.compile(
    r'(<script id="run-data" type="application/json">).*?(</script>)', re.S
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--in", dest="data_path", default="run-data.json")
    parser.add_argument("--template", default="job-dashboard.html")
    parser.add_argument("--out", default="job-dashboard.rendered.html")
    args = parser.parse_args()

    template = Path(args.template).read_text(encoding="utf-8")
    data = json.loads(Path(args.data_path).read_text(encoding="utf-8"))

    rendered, n = PLACEHOLDER_RE.subn(
        lambda m: m.group(1) + json.dumps(data) + m.group(2), template, count=1
    )
    if n == 0:
        raise SystemExit(f"No run-data placeholder found in {args.template}")

    Path(args.out).write_text(rendered, encoding="utf-8")
    print(f"wrote {args.out} ({len(rendered)} bytes)")


if __name__ == "__main__":
    main()
