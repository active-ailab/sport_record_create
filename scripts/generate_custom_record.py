#!/usr/bin/env python3
"""Generate source-backed generic single-sport fixtures from a JSON specification."""
import argparse, datetime, hashlib, json, math, re, struct, subprocess, tempfile, tarfile
from pathlib import Path

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--spec',type=Path,help='JSON customization specification')
p.add_argument('--source',type=Path)
p.add_argument('--config',type=Path)
p.add_argument('--output',type=Path)
p.add_argument('--list-sports',action='store_true')
a=p.parse_args()
if not __debug__: raise SystemExit('Do not use python -O')
defaults=json.loads((Path(__file__).resolve().parents[1]/'references/defaults.json').read_text())
spec=json.loads(a.spec.read_text()) if a.spec else {}
allowed={'project','source','config','sport','duration_seconds','start','sync','origin','distance_meters','calories_kcal','heart_rate','count','main_data','summary','nodes','sport_enum','summary_subtype'}
if set(spec)-allowed: raise SystemExit('Unknown spec fields: '+str(set(spec)-allowed))
root=Path(a.source or spec.get('source',defaults['source'])).resolve()
config=(root/(a.config or Path(spec.get('config',defaults['config'])))).resolve()
config.relative_to(root)
inc=root/'packages/services/sport/include'; pb=root/'packages/services/sport/summary'; np=root/'framework/utils/nanopb'
paths=[inc/'sport_record_service.h',inc/'sport_record_save.h',inc/'sport_record_save_detail.h',root/'framework/engine/sportEngine/sport_type_auto.h',root/'packages/services/sport/src/sport_record_save.c',inc/'sport_control_typedef.h',config,pb/'sport_summary.pb.h',pb/'sport_summary.pb.c',pb/'sport_summary.proto']
texts={f.name:f.read_text() for f in paths}
enums={name:int(value) for name,value in re.findall(r'\b(SPORT_TYPE_\w+)\s*=\s*(\d+)\b',texts['sport_type_auto.h'])}
if a.list_sports:
    print(json.dumps(enums,ensure_ascii=False,indent=2));raise SystemExit(0)
if not a.output: raise SystemExit('--output is required')
out=a.output.resolve()
if out.exists() or out.with_suffix('.tar.gz').exists():raise SystemExit('Output/archive already exists')
def integer(value,lo,hi,name):
    if type(value)!=int or not lo<=value<=hi:raise SystemExit('Invalid '+name)
    return value
sport=spec.get('sport',defaults['sport'])
if isinstance(sport,str) and sport in enums: sport=enums[sport]
sport=integer(sport,1,65535,'sport')
names=[n for n,v in enums.items() if v==sport]
if not names:raise SystemExit('Sport ID not found in source enums')
duration=integer(spec.get('duration_seconds',defaults['duration_seconds']),1,86400,'duration_seconds')
origin=spec.get('origin',defaults['origin']); origins={'manual':0,'auto':1,'app':6,'activity':17}
if origin not in origins:raise SystemExit('origin must be manual/auto/app/activity')
start_type=origins[origin]; subtype=1 if origin=='activity' else start_type
if origin=='activity':
    assert re.search(r'SPORT_START_TYPE_ACTIVITY\s*=\s*17\b',texts['sport_control_typedef.h'])
    assert 'sportSummary->type.subtype = SPORT_START_TYPE_AUTO;' in texts['sport_record_save.c']
if origin=='activity' and not re.search(r'^HMI_FEATURE_SUPPORT_ACTIVITY_SPORT_AR=y$',texts[config.name],re.M):
    raise SystemExit('Activity feature not enabled in selected config; inspect actual build config')
state=spec.get('sync',defaults['sync'])
if state not in ('not_synced','synced'):raise SystemExit('sync must be not_synced/synced')
sync=1 if state=='not_synced' else 2
# Keep data shape and sync status independent: synced fixtures retain detail on disk.
start=datetime.datetime.fromisoformat(spec['start']) if 'start' in spec else datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).replace(microsecond=0)-datetime.timedelta(seconds=duration)
if start.tzinfo is None or start.microsecond:raise SystemExit('start requires timezone and whole seconds')
zone=int(start.utcoffset().total_seconds()/900)
if zone*900!=start.utcoffset().total_seconds() or not -128<=zone<=127:raise SystemExit('Timezone must be int8 quarter hours')
track=int(start.timestamp())
if not 0<track<2147483647-duration:raise SystemExit('Start timestamp outside supported range')
distance=integer(spec.get('distance_meters',0),0,10000000,'distance_meters')
calories=integer(spec.get('calories_kcal',0),0,1000000,'calories_kcal')
count=integer(spec.get('count',0),0,10000000,'count')
main=integer(spec.get('main_data',count if sport==21 else (distance if sport in (1,2,3,4) else calories)),0,4294967295,'main_data')
hr=spec.get('heart_rate',defaults['heart_rate'])
hrs=[integer(v,1,255,'heart_rate') for v in hr] if isinstance(hr,list) else [integer(hr,1,255,'heart_rate')]*(duration+1)
if len(hrs)!=duration+1:raise SystemExit('heart_rate series must contain duration_seconds+1 samples')
nodes=spec.get('nodes',[])
if not isinstance(nodes,list) or len(nodes)>1000:raise SystemExit('nodes must be a list of at most 1000 nodes')
def decl(name):
    found=[]
    for key in ('sport_record_service.h','sport_record_save.h','sport_record_save_detail.h'):
        found+=re.findall(r'typedef\s+struct\s*\{[^{}]*\}\s*(?:_PACKED\s+)?'+re.escape(name)+r'\s*;',texts[key])
    if len(found)!=1:raise SystemExit('Cannot uniquely extract source struct '+name)
    return found[0]
header='#include <stdint.h>\n#define _PACKED __attribute__((packed))\n'
for macro in ('MAX_SPORT_RECORD_NUM','MAX_CHILD_SPORTS_NUM'):
    match=re.search(r'^'+macro+r'=(\d+)$',texts[config.name],re.M)
    if not match:raise SystemExit('Missing '+macro)
    header+=f'#define {macro} {match[1]}\n'
assert '#define MAX_SPORT_RECORD_NUM 50\n' in header and '#define MAX_CHILD_SPORTS_NUM 10\n' in header,'Current parser supports 50 slots/10 children'
required=['sport_record_manager_detail_t','child_sport_info_t','child_sports_manager_t','sport_record_manager_t','sport_node_timestamp_t','sport_node_rt_heart_t','sport_node_event_t']
for node in nodes:
    if set(node)!={'at_ms','type','fields'}:raise SystemExit('Each node needs at_ms/type/fields')
    integer(node['at_ms'],0,duration*1000,'node.at_ms')
    if not re.fullmatch(r'sport_node_\w+_t',node['type']):raise SystemExit('Invalid node C type')
    if node['type'] not in required:required.append(node['type'])
header+='\n'.join(decl(n) for n in required)
assert 'sportSummaryInfo[1] = 0x80;' in texts['sport_record_save.c'],'Recheck summary header'
def literal(value):
    if isinstance(value,bool):return 'true' if value else 'false'
    if type(value) not in (int,float) or not math.isfinite(value):raise SystemExit('Only finite numeric/bool scalar fields supported')
    return repr(value)
summary={
 'times.totalTime':duration,'times.exerciseTime':duration,
 'calories.cal':calories,'heartRate.averageHeartRate':sum(hrs)//len(hrs),
 'heartRate.minimumHeartRate':min(hrs),'heartRate.maximumHeartRate':max(hrs)}
if distance:summary['distance.distance']=distance
if sport==21:
    summary.update({'number.totalNumberOfJumps':count})
custom=spec.get('summary',{})
if not isinstance(custom,dict):raise SystemExit('summary must map dotted field paths to scalar values')
for field,value in custom.items():
    if not re.fullmatch(r'[A-Za-z]\w*\.[A-Za-z]\w*',field) or field.split('.')[0] in ('record','type'):
        raise SystemExit('Invalid/protected summary field '+field)
    literal(value);summary[field]=value
if summary['times.totalTime']!=duration or summary['times.exerciseTime']!=duration:
    raise SystemExit('Custom paused windows need a dedicated time adapter; do not change times without matching detail')
if 'times.restTimeBetweenGroups' in summary:integer(summary['times.restTimeBetweenGroups'],0,duration,'restTimeBetweenGroups')
assign=[];checks=[]
for path,value in summary.items():
    parent,leaf=path.split('.')
    assign.extend([f'summary.has_{parent}=true;',f'summary.{parent}.has_{leaf}=true;',f'summary.{path}={literal(value)};'])
    checks.append(f'assert(decoded.{path}==summary.{path});')
# All custom nodes use actual source struct sizes, fields and serialization.
bytime={};expected_tags=[]
for i,node in enumerate(nodes):
    fields=node['fields']
    if not isinstance(fields,dict) or 'tag' not in fields:raise SystemExit('Node fields require tag')
    tag=integer(fields['tag'],2,30,'single-byte node tag')
    if tag in (4,8):raise SystemExit('Event and heart-rate nodes are generated separately')
    expected_tags.append(tag)
    code=[f'{node["type"]} node_{i}={{0}};',f'node_{i}.length=sizeof(node_{i})-2;',f'node_{i}.relative_time=(uint16_t)(ms-anchor);']
    if node['type']=='sport_node_strength_group_t':code.append(f'memset(node_{i}.reserved,0xff,sizeof(node_{i}.reserved));')
    for field,value in fields.items():
        if not re.fullmatch(r'[A-Za-z]\w*',field) or field in ('length','relative_time','reserved'):raise SystemExit('Protected or invalid node field')
        code.append(f'node_{i}.{field}={literal(value)};')
    code.append(f'put(detail,&node_{i},sizeof(node_{i}));')
    bytime.setdefault(node['at_ms'],[]).extend(code)
schedule=sorted(set(range(0,duration*1000+1,1000))|set(bytime))
switch='\n'.join('case '+str(t)+': { '+' '.join(code)+' break; }' for t,code in bytime.items())
c=r'''
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "types.h"
#include "sport_summary.pb.h"
#include "pb_encode.h"
#include "pb_decode.h"
static void put(FILE *f,const void *p,size_t n){assert(fwrite(p,1,n,f)==n);}
static void save(const char *name,const void *p,size_t n){FILE *f=fopen(name,"wb");assert(f);put(f,p,n);assert(fclose(f)==0);}
int main(void){
 uint16_t endian=1;assert(*(unsigned char*)&endian==1);
 sport_record_manager_t rec={0};rec.count=1;
 rec.manager[0].sync=SYNC;rec.manager[0].multi=1;rec.manager[0].time_zone=ZONE;
 rec.manager[0].sport_type=SPORT;rec.manager[0].time=DURATION;rec.manager[0].track_id=TRACK;
 rec.manager[0].main_data=MAIN;rec.manager[0].auto_reg=ORIGIN;
 save("rec.manager",&rec,sizeof(rec));
 child_sports_manager_t child={0};child.child_num=1;child.total_time=DURATION;
 child.total_distance=DISTANCE;child.total_consume=CALORIES;child.avg_hr=AVGHR;
 child.child_info[0].sport_type=SPORT;child.child_info[0].track_id=TRACK;
 child.child_info[0].sport_time=DURATION;child.child_info[0].distance=DISTANCE;
 child.child_info[0].consume=CALORIES;child.child_info[0].number=COUNT;
 save("child.manager",&child,sizeof(child));
 SportSummaryMessage summary=SportSummaryMessage_init_zero;
 strcpy(summary.version,"2.1");summary.record.trackId=TRACK;summary.record.zone=ZONE;
 summary.has_type=true;summary.type.type=SPORT;summary.type.has_subtype=true;summary.type.subtype=SUBTYPE;
 /*ASSIGN*/
 unsigned char buf[8192]={0};buf[1]=0x80;
 pb_ostream_t output=pb_ostream_from_buffer(buf+2,sizeof(buf)-2);
 assert(pb_encode(&output,SportSummaryMessage_fields,&summary));save("summary.data",buf,output.bytes_written+2);
 SportSummaryMessage decoded=SportSummaryMessage_init_zero;
 pb_istream_t input=pb_istream_from_buffer(buf+2,output.bytes_written);
 assert(pb_decode(&input,SportSummaryMessage_fields,&decoded));assert(input.bytes_left==0);
 assert(decoded.record.trackId==TRACK&&decoded.type.type==SPORT&&decoded.type.subtype==SUBTYPE);
 /*CHECKS*/
 const uint8_t hr[]={HRVALUES};const uint32_t schedule[]={SCHEDULE};
 FILE *detail=fopen("detail.data","wb");assert(detail);uint32_t anchor=0;
 for(size_t i=0;i<sizeof(schedule)/sizeof(schedule[0]);i++){
  uint32_t ms=schedule[i];
  if(i==0||ms-anchor>0x8000){sport_node_timestamp_t n={0};n.tag=1;n.length=sizeof(n)-2;n.magic=0x686d7370;n.time=(uint64_t)TRACK*1000+ms;put(detail,&n,sizeof(n));anchor=ms;}
  if(ms==0||ms==DURATION*1000){sport_node_event_t e={0};e.tag=4;e.length=sizeof(e)-2;e.relative_time=(uint16_t)(ms-anchor);e.index=ms==0?1:6;put(detail,&e,sizeof(e));}
  if(ms%1000==0){sport_node_rt_heart_t h={0};h.tag=8;h.length=sizeof(h)-2;h.relative_time=(uint16_t)(ms-anchor);h.hr=hr[ms/1000];put(detail,&h,sizeof(h));}
  switch(ms){/*NODES*/default:break;}
 }
 assert(fclose(detail)==0);return 0;
}
'''
constants={'SYNC':sync,'ZONE':zone,'SPORT':sport,'DURATION':duration,'TRACK':track,'MAIN':main,'ORIGIN':start_type,'SUBTYPE':subtype,'DISTANCE':distance,'CALORIES':calories,'AVGHR':sum(hrs)//len(hrs),'COUNT':count}
header+='\n'+''.join(f'#define {k} {v}\n' for k,v in constants.items())
c=c.replace('/*ASSIGN*/','\n'.join(assign)).replace('/*CHECKS*/','\n'.join(checks)).replace('HRVALUES',','.join(map(str,hrs))).replace('SCHEDULE',','.join(map(str,schedule))).replace('/*NODES*/',switch)
with tempfile.TemporaryDirectory(prefix='custom-sport-') as td:
    tmp=Path(td);(tmp/'types.h').write_text(header);(tmp/'generate.c').write_text(c)
    cmd=['gcc','-std=c11','-O0','-g','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-omit-frame-pointer','-I'+str(tmp),'-I'+str(pb),'-I'+str(np),str(tmp/'generate.c'),str(pb/'sport_summary.pb.c'),str(np/'pb_common.c'),str(np/'pb_encode.c'),str(np/'pb_decode.c'),'-o',str(tmp/'generate')]
    subprocess.run(cmd,check=True);subprocess.run([str(tmp/'generate')],cwd=tmp,check=True)
    raw=(tmp/'rec.manager').read_bytes();assert len(raw)==1004 and raw[4]&15==sync
    assert struct.unpack_from('<HIII',raw,6)==(sport,duration,main,track)
    child=(tmp/'child.manager').read_bytes();assert len(child)==504 and struct.unpack_from('<IIIHH',child)==(duration,distance,calories,sum(hrs)//len(hrs),1)
    assert struct.unpack_from('<IIIIII',child,64)==(sport,track,duration,distance,calories,count)
    assert (tmp/'summary.data').read_bytes()[:2]==b'\0\x80'
    detail=(tmp/'detail.data').read_bytes();pos=0;anchor=None;observed_hr=[];observed_nodes=[];events=[];node_tags=[]
    while pos<len(detail):
        tag,n=detail[pos:pos+2];payload=detail[pos+2:pos+2+n];assert len(payload)==n
        if tag==1:
            magic,anchor=struct.unpack('<IQ',payload);assert magic==0x686d7370
        else:
            assert anchor is not None
            relative=struct.unpack_from('<H',payload)[0];assert relative<=0x8000
            absolute=anchor+relative
            if tag==8:observed_hr.append((absolute,payload[2]))
            elif tag==4:events.append((absolute,struct.unpack_from('<H',payload,2)[0]))
            else:observed_nodes.append((absolute-track*1000,tag))
        pos+=n+2
    assert observed_hr==[(track*1000+i*1000,h) for i,h in enumerate(hrs)]
    assert events==[(track*1000,1),((track+duration)*1000,6)]
    assert observed_nodes==[(x['at_ms'],x['fields']['tag']) for x in sorted(nodes,key=lambda x:x['at_ms'])]
    base=out/'sport_record';parent=base/str(track);leaf=parent/str(track);leaf.mkdir(parents=True)
    for name,target in [('rec.manager',base),('child.manager',parent),('summary.data',leaf),('detail.data',leaf)]: (target/name).write_bytes((tmp/name).read_bytes())
    (out/'source_types.h').write_text(header);(out/'generate.c').write_text(c)
    resolved={**spec,'project':spec.get('project',defaults['project']),'source':str(root),'config':str(config.relative_to(root)),'sport':sport,'sport_enum':names[0],'duration_seconds':duration,'start':start.isoformat(),'sync':state,'origin':origin,'summary_subtype':subtype,'main_data':main,'count':count,'distance_meters':distance,'calories_kcal':calories,'heart_rate':hrs,'nodes':nodes}
    (out/'resolved_spec.json').write_text(json.dumps(resolved,ensure_ascii=False,indent=2)+'\n')
    entries=[{'path':str(f.relative_to(out)),'bytes':f.stat().st_size,'sha256':hashlib.sha256(f.read_bytes()).hexdigest()} for f in sorted(base.rglob('*')) if f.is_file()]
    manifest={'synthetic':True,'track_id':track,'sport_type':sport,'start_type':start_type,'summary_subtype':subtype,'sync':'NOT_SYNCED' if sync==1 else 'HAD_SYNCED','duration_seconds':duration,'coverage':'generic_record_format_with_requested_fields','source_sha256':{str(f.relative_to(root)):hashlib.sha256(f.read_bytes()).hexdigest() for f in paths},'files':entries,'verification':['source nanopb roundtrip','full summary header','manager and child byte checks','TLV times, HR, events and custom node schedule','ASan/UBSan generation'],'limits':['A source enum is not proof of project feature or App support.','Generic scalar/node customization is not a complete sport-specific or Activity recognition pipeline simulation.','Custom node field semantics need independent sport-specific validation.','No device import or App transmission/parse/persistence tested.','No automatic loss of detail when creating synced fixtures; synced status is only a fixture field.']}
    (out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    (out/'README.md').write_text('# 定制合成运动记录\n\n实际参数见 resolved_spec.json，校验及边界见 manifest.json。通用格式造数不等于该运动专用字段或 App 验收通过。此索引仅登记本条，不覆盖真实设备已有索引。\n')
archive=out.with_suffix('.tar.gz')
with tarfile.open(archive,'w:gz') as t:t.add(out,arcname=out.name)
with tarfile.open(archive) as t:
    for e in entries:assert t.extractfile(out.name+'/'+e['path']).read()==(out/e['path']).read_bytes()
print(json.dumps({'output':str(out),'archive':str(archive),'track_id':track,'sport_type':sport,'origin':origin,'sync':state,'verification':'local format PASS; sport semantics/device/App unverified'},ensure_ascii=False,indent=2))
