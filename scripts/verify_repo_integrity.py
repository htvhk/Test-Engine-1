#!/usr/bin/env python3
from pathlib import Path
import hashlib, json, sys
ROOT=Path(__file__).resolve().parents[1]
BASE=json.loads((ROOT/'CANONICAL_BASELINE.json').read_text(encoding='utf-8'))

def sha256(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024), b''): h.update(chunk)
    return h.hexdigest()
fail=[]
for rel, expected in BASE['tracked_file_hashes'].items():
    p=ROOT/rel
    if not p.is_file(): fail.append(f'missing: {rel}'); continue
    got=sha256(p)
    if got != expected: fail.append(f'hash: {rel}: {got} != {expected}')
for rel, expected in BASE['network_sha256'].items():
    p=ROOT/rel
    if not p.is_file(): fail.append(f'missing network: {rel}'); continue
    got=sha256(p)
    if got != expected: fail.append(f'network hash: {rel}: {got} != {expected}')
for rel, rows in BASE['reference_rows'].items():
    p=ROOT/rel
    if not p.is_file(): fail.append(f'missing reference: {rel}'); continue
    n=sum(1 for _ in p.open('r',encoding='utf-8'))
    if n != rows: fail.append(f'reference rows: {rel}: {n} != {rows}')
if fail:
    print('TE1 CANONICAL REPOSITORY INTEGRITY: FAIL', file=sys.stderr)
    for x in fail: print(' -',x,file=sys.stderr)
    raise SystemExit(1)
print('TE1 CANONICAL REPOSITORY INTEGRITY: PASS')
print('tracked files verified:', len(BASE['tracked_file_hashes']))
