#!/usr/bin/env python3
from pathlib import Path
import hashlib, json, subprocess, sys
ROOT=Path(__file__).resolve().parents[1]
BASE=json.loads((ROOT/'CANONICAL_BASELINE.json').read_text(encoding='utf-8'))
AUTH=json.loads((ROOT/'EXPERIMENTAL_SOURCE_AUTHORIZATION.json').read_text(encoding='utf-8'))

def sha256(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024), b''): h.update(chunk)
    return h.hexdigest()
fail=[]
if AUTH.get('schema') != 'TE1-POST-B2-SOURCE-AUTHORIZATION-v1':
    fail.append('experimental authorization schema')
if AUTH.get('immutable_baseline_commit') != 'f0ad93ad1940b964c513fc56175c7f907b114be9':
    fail.append('immutable baseline commit authorization')
checkpoint=AUTH.get('authorized_checkpoint', {})
for rev, expected, label in ((checkpoint.get('commit'), checkpoint.get('tree'), 'authorized checkpoint'),):
    result=subprocess.run(['git','-C',str(ROOT),'rev-parse',f'{rev}^{{tree}}'], text=True,
                          capture_output=True, check=False)
    if result.returncode or result.stdout.strip() != expected:
        fail.append(f'{label} identity')
ancestor=subprocess.run(['git','-C',str(ROOT),'merge-base','--is-ancestor',
                         checkpoint.get('commit',''), 'HEAD'], capture_output=True, check=False)
if ancestor.returncode:
    fail.append('HEAD does not descend from authorized checkpoint')
for rel, expected in BASE['tracked_file_hashes'].items():
    p=ROOT/rel
    if not p.is_file(): fail.append(f'missing: {rel}'); continue
    got=sha256(p)
    authorization=AUTH.get('production_files', {}).get(rel)
    if authorization:
        if authorization.get('baseline_sha256') != expected:
            fail.append(f'authorization does not preserve baseline hash: {rel}')
        if got != authorization.get('authorized_sha256'):
            fail.append(f'authorized hash: {rel}: {got} != {authorization.get("authorized_sha256")}')
        blob=subprocess.run(['git','-C',str(ROOT),'hash-object',rel], text=True,
                            capture_output=True, check=False).stdout.strip()
        if blob != authorization.get('authorized_blob'):
            fail.append(f'authorized blob: {rel}: {blob} != {authorization.get("authorized_blob")}')
    elif got != expected: fail.append(f'hash: {rel}: {got} != {expected}')
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
