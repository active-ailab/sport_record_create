#!/usr/bin/env python3
"""Merge generated record packages into a new root, without overwriting sources."""
import argparse
import hashlib
import json
import shutil
import struct
import tarfile
from pathlib import Path

def within(path, parent):
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--input',type=Path,action='append',required=True,help='Generated package root; repeat for each input')
p.add_argument('--output',type=Path,required=True)
a=p.parse_args()
if not __debug__: raise SystemExit('Do not use python -O')
out=a.output.resolve()
if out.exists() or out.with_suffix('.tar.gz').exists(): raise SystemExit('Output/archive already exists')
records=[]; expected={}; source_hashes=None; inputs=[]; seen=set()
for arg in a.input:
    src=arg.resolve()
    if within(out, src) or within(src, out): raise SystemExit('Output must be separate from inputs')
    m=json.loads((src/'manifest.json').read_text())
    if not m.get('synthetic') or not m.get('source_sha256'):
        raise SystemExit('Only validated generator packages are supported; do not merge raw device dumps with this tool')
    if source_hashes is None: source_hashes=m['source_sha256']
    if source_hashes!=m['source_sha256']: raise SystemExit('Source/config fingerprints differ; check compatibility first')
    base=src/'sport_record'
    listed={}
    for entry in m['files']:
        f=(src/entry['path']).resolve()
        if not within(f, base.resolve()) or f.is_symlink(): raise SystemExit('Invalid manifest path')
        data=f.read_bytes()
        if len(data)!=entry['bytes'] or hashlib.sha256(data).hexdigest()!=entry['sha256']:
            raise SystemExit('Input differs from manifest: '+str(f))
        listed[str(f.relative_to(src))]=entry
    actual={str(f.relative_to(src)) for f in base.rglob('*') if f.is_file()}
    if actual!=set(listed): raise SystemExit('Input file list differs from manifest')
    raw=(base/'rec.manager').read_bytes()
    if len(raw)!=1004: raise SystemExit('Unsupported manager ABI')
    slots=[]
    for i in range(50):
        slot=raw[4+i*20:24+i*20]
        if slot[0]&15==0:
            if any(slot): raise SystemExit('Nonzero invalid slot; inspect source')
            continue
        track=struct.unpack_from('<I',slot,12)[0]
        if track==0 or track in seen: raise SystemExit('Duplicate/invalid track ID')
        if slot[0]&15 not in (1,2) or slot[0]>>4!=1: raise SystemExit('Only valid single-sport slots are supported')
        child=(base/str(track)/'child.manager').read_bytes()
        if len(child)!=504 or struct.unpack_from('<H',child,14)[0]!=1:
            raise SystemExit('Invalid single-sport child manager')
        child_id=struct.unpack_from('<I',child,68)[0]
        summary=base/str(track)/str(child_id)/'summary.data'
        if summary.read_bytes()[:2]!=b'\x00\x80': raise SystemExit('Invalid summary header; use corrected fixtures')
        detail=base/str(track)/str(child_id)/'detail.data'
        if not detail.is_file() or detail.stat().st_size==0: raise SystemExit('Missing/empty detail')
        seen.add(track); slots.append(slot)
        records.append({'track_id':track,'sport_type':struct.unpack_from('<H',slot,2)[0],
            'duration_seconds':struct.unpack_from('<I',slot,4)[0],'sync':slot[0]&15,'source':str(src)})
    if len(slots)!=struct.unpack_from('<H',raw)[0]: raise SystemExit('Manager count mismatch')
    if {f.name for f in base.iterdir() if f.is_dir()}!={str(struct.unpack_from('<I',s,12)[0]) for s in slots}:
        raise SystemExit('Unindexed or missing record directory')
    inputs.append((src,slots))
if not records or len(records)>50: raise SystemExit('Require 1..50 total records; no implicit eviction')
base=out/'sport_record';base.mkdir(parents=True)
manager=bytearray(1004);struct.pack_into('<H',manager,0,len(records));cursor=4
for index,(src,slots) in enumerate(inputs):
    for slot in slots:
        track=struct.unpack_from('<I',slot,12)[0]
        manager[cursor:cursor+20]=slot;cursor+=20
        source=src/'sport_record'/str(track); target=base/str(track)
        shutil.copytree(source,target)
        for f in source.rglob('*'):
            if f.is_file():
                copied=target/f.relative_to(source)
                if f.read_bytes()!=copied.read_bytes(): raise SystemExit('Copy mismatch')
    if (src/'groups.csv').exists(): shutil.copyfile(src/'groups.csv',out/f'input_{index+1}_groups.csv')
(base/'rec.manager').write_bytes(manager)
entries=[]
for f in sorted(base.rglob('*')):
    if f.is_file():entries.append({'path':str(f.relative_to(out)),'bytes':f.stat().st_size,'sha256':hashlib.sha256(f.read_bytes()).hexdigest()})
manifest={'synthetic':True,'record_count':len(records),'records':records,'source_sha256':source_hashes,'files':entries,
    'limits':['Separate workouts sharing one manager, not a multi-sport workout.','Only local files checked; no device/App acceptance.','This index only contains the selected inputs; no raw device records merged.']}
(out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
(out/'README.md').write_text(f'# 合并运动记录包\n\n共 {len(records)} 场独立运动；索引保留各条原有同步状态。详见 manifest.json。\n\n仅合并输入样本，不能用此索引覆盖还需保留其它记录的设备索引。未进行设备/App 验证。\n')
archive=out.with_suffix('.tar.gz')
with tarfile.open(archive,'w:gz') as t:t.add(out,arcname=out.name)
with tarfile.open(archive) as t:
    for e in entries:
        if t.extractfile(out.name+'/'+e['path']).read()!=(out/e['path']).read_bytes(): raise SystemExit('Archive mismatch')
print(json.dumps({'output':str(out),'archive':str(archive),'records':records,'verification':'PASS: source hashes, manager count, slot bytes, copies and archive'},ensure_ascii=False,indent=2))
