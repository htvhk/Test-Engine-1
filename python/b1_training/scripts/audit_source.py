from __future__ import annotations
import argparse, ast, json, re
from pathlib import Path
import sys

# Make each CLI entry point runnable directly, independent of notebook PYTHONPATH state.
SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from te1_b1.io_utils import strict_dump

FORBIDDEN = [r"BEGIN (RSA|OPENSSH|EC) PRIVATE KEY", r"sk-or-v1-[A-Za-z0-9_-]{20,}"]


def main():
    p=argparse.ArgumentParser(); p.add_argument('--source-root',type=Path,required=True); p.add_argument('--report',type=Path,required=True); a=p.parse_args()
    findings=[]; python_files=sorted(a.source_root.rglob('*.py'))
    for path in python_files:
        text=path.read_text(encoding='utf-8'); ast.parse(text,filename=str(path))
        for pattern in FORBIDDEN:
            if re.search(pattern,text): findings.append({'path':str(path),'pattern':pattern})
    value={'status':'PASS' if not findings else 'FAIL','python_files':len(python_files),'findings':findings}
    strict_dump(value,a.report)
    if findings: raise SystemExit(2)
    print(json.dumps(value,indent=2))
if __name__=='__main__': main()
