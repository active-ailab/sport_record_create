#!/usr/bin/env python3
"""Build a synthetic 1h record using source-extracted C types and project nanopb."""
import argparse, datetime, hashlib, json, re, struct, subprocess, tempfile, tarfile
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument('--source', type=Path, required=True, help='ZeppOS source root')
ap.add_argument('--config', type=Path, default=Path('configs/apollo5/apollo330_fuji_defconfig'), help='Config path relative to source root')
ap.add_argument('--start', required=True, help='Start ISO-8601 time with explicit timezone, e.g. 2026-11-09T13:00:00+08:00')
ap.add_argument('--output', type=Path, required=True)
ap.add_argument('--sport', choices=['treadmill', 'strength', 'skipping'], default='treadmill')
a = ap.parse_args()
root = a.source.resolve()
a.config = (root / a.config).resolve().relative_to(root)
out = a.output.resolve()
if not __debug__:
    raise SystemExit('Python assertions must be enabled; do not use python -O')
if out.exists() or out.with_suffix('.tar.gz').exists():
    raise SystemExit('Output must not exist: ' + str(out))
# Explicit sample time in client timezone; no dependency on host timezone.
strength = a.sport == 'strength'
skipping = a.sport == 'skipping'
sport_type, start_type = (21, 0) if skipping else ((52, 0) if strength else (2, 6))
distance, calories = (0, 600) if skipping else ((0, 240) if strength else (6000, 360))
main_data = 7200 if skipping else (calories if strength else distance)
start = datetime.datetime.fromisoformat(a.start)
if start.tzinfo is None or start.utcoffset() is None:
    raise SystemExit('--start must include a UTC offset')
if start.microsecond:
    raise SystemExit('--start must use whole seconds')
zone = int(start.utcoffset().total_seconds() / 900)
if start.utcoffset().total_seconds() != zone * 900 or not -128 <= zone <= 127:
    raise SystemExit('Timezone must fit int8 quarter-hour units')
track = int(start.timestamp())
if not 0 < track <= 2147483647 - 3600:
    raise SystemExit('Start time outside supported signed path-ID range')
files = {
    'service': root/'packages/services/sport/include/sport_record_service.h',
    'save': root/'packages/services/sport/include/sport_record_save.h',
    'detail': root/'packages/services/sport/include/sport_record_save_detail.h',
    'config': root/a.config,
    'types': root/'framework/engine/sportEngine/sport_type_auto.h',
    'control': root/'packages/services/sport/include/sport_control_typedef.h',
    'business': root/'packages/services/sport/src/sport_record_save.c',
}
texts = {k: v.read_text() for k,v in files.items()}
def declaration(key, name):
    pattern = r'typedef\s+struct\s*\{[^{}]*\}\s*(?:_PACKED\s+)?' + re.escape(name) + r'\s*;'
    matches = re.findall(pattern, texts[key])
    if len(matches) != 1:
        raise RuntimeError('Ambiguous/missing source declaration: '+name)
    return matches[0]
def config(name):
    m = re.search(r'^'+name+r'=(\d+)$', texts['config'], re.M)
    if not m: raise RuntimeError('Missing config '+name)
    return int(m[1])
assert re.search(r'SPORT_TYPE_SKIPPING_ROPE\s*=\s*21\b',texts['types'])
assert re.search(r'SPORT_TYPE_STRENGTH_TRAINING\s*=\s*52\b',texts['types'])
assert re.search(r'SPORT_TYPE_TREADMILL\s*=\s*2\b',texts['types'])
assert re.search(r'SPORT_START_TYPE_SPORT_SHOW\s*=\s*6\b',texts['control'])
assert 'unixTime - sSportLastUnixTime > 0x8000' in texts['business']
header = '#include <stdint.h>\n#define _PACKED __attribute__((packed))\n'
assert config('MAX_SPORT_RECORD_NUM') == 50, 'This generator currently supports 50 slots only'
assert config('MAX_CHILD_SPORTS_NUM') == 10, 'This generator currently supports 10 children only'
assert 'sportSummaryInfo[1] = 0x80;' in texts['business'], 'Recheck summary header format'
header += f'#define MAX_SPORT_RECORD_NUM {config("MAX_SPORT_RECORD_NUM")}\n'
header += f'#define MAX_CHILD_SPORTS_NUM {config("MAX_CHILD_SPORTS_NUM")}\n'
for key,name in [('service','sport_record_manager_detail_t'),('service','child_sport_info_t'),('service','child_sports_manager_t'),('save','sport_record_manager_t'),('detail','sport_node_timestamp_t'),('detail','sport_node_rt_heart_t'),('detail','sport_node_strength_group_t'),('detail','sport_node_rt_group_2_t'),('detail','sport_node_event_t')]:
    header += declaration(key,name)+'\n'
header += f'#define FIXTURE_ZONE {zone}\n#define FIXTURE_SKIPPING {int(skipping)}\n#define FIXTURE_STRENGTH {int(strength)}\n#define FIXTURE_TYPE {sport_type}\n#define FIXTURE_START {start_type}\n#define FIXTURE_DISTANCE {distance}\n#define FIXTURE_CALORIES {calories}\n#define FIXTURE_MAIN {main_data}\n'
source = r'''
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "types.h"
#include "pb_encode.h"
#include "pb_decode.h"
#include "sport_summary.pb.h"
static void write_all(const char *path, const void *buf, size_t size) {
    FILE *f=fopen(path,"wb"); assert(f);
    assert(fwrite(buf,1,size,f)==size); assert(fclose(f)==0);
}
int main(int argc, char **argv) {
    assert(argc==2);
    const uint32_t track=(uint32_t)strtoul(argv[1],NULL,10);
    const uint16_t endian=1; assert(*(const unsigned char *)&endian==1);
    sport_record_manager_t rec={0}; rec.count=1;
    rec.manager[0].sync=1; rec.manager[0].multi=1;
    rec.manager[0].time_zone=FIXTURE_ZONE; rec.manager[0].sport_type=FIXTURE_TYPE;
    rec.manager[0].time=3600; rec.manager[0].track_id=track;
    rec.manager[0].main_data=FIXTURE_MAIN; rec.manager[0].auto_reg=FIXTURE_START;
    write_all("rec.manager", &rec, sizeof(rec));
    child_sports_manager_t child={0};
    /* Actual _fillChildManager writes duration_net / 1000, despite ms header comments. */
    child.total_time=3600; child.total_distance=FIXTURE_DISTANCE; child.total_consume=FIXTURE_CALORIES;
    child.avg_hr=120; child.child_num=1;
    child.child_info[0].sport_type=FIXTURE_TYPE; child.child_info[0].track_id=track;
    child.child_info[0].sport_time=3600; child.child_info[0].distance=FIXTURE_DISTANCE;
    child.child_info[0].consume=FIXTURE_CALORIES;
    child.child_info[0].number=FIXTURE_SKIPPING ? 7200 : (FIXTURE_STRENGTH ? 144 : 0);
    write_all("child.manager", &child, sizeof(child));
    SportSummaryMessage summary=SportSummaryMessage_init_zero;
    strcpy(summary.version,"2.1");
    summary.record.trackId=track; summary.record.parentId=0; summary.record.zone=FIXTURE_ZONE;
    summary.has_type=true; summary.type.type=FIXTURE_TYPE;
    summary.type.has_subtype=true; summary.type.subtype=FIXTURE_START;
    summary.has_times=true; summary.times.has_totalTime=true; summary.times.totalTime=3600;
    summary.times.has_exerciseTime=true; summary.times.exerciseTime=3600;
    summary.has_distance=!(FIXTURE_STRENGTH || FIXTURE_SKIPPING); summary.distance.has_distance=summary.has_distance;
    summary.distance.distance=FIXTURE_DISTANCE;
    if(FIXTURE_STRENGTH) {
        summary.times.has_restTimeBetweenGroups=true; summary.times.restTimeBetweenGroups=2160;
    }
    if(FIXTURE_SKIPPING) {
        summary.has_number=true; summary.number.has_totalNumberOfJumps=true;
        summary.number.totalNumberOfJumps=7200;
        summary.number.has_numberOfBreak=true; summary.number.numberOfBreak=0;
        summary.number.has_maxNumberOfOnceJumps=true; summary.number.maxNumberOfOnceJumps=7200;
        summary.has_frequency=true; summary.frequency.has_averageFrequency=true;
        summary.frequency.averageFrequency=120;
        summary.frequency.has_highestFrequency=true; summary.frequency.highestFrequency=120;
    }
    summary.has_calories=true; summary.calories.has_cal=true; summary.calories.cal=FIXTURE_CALORIES;
    summary.has_heartRate=true;
    summary.heartRate.has_averageHeartRate=true; summary.heartRate.averageHeartRate=120;
    summary.heartRate.has_maximumHeartRate=true; summary.heartRate.maximumHeartRate=120;
    summary.heartRate.has_minimumHeartRate=true; summary.heartRate.minimumHeartRate=120;
    unsigned char data[4096]={0};
    data[1]=0x80; /* Actual _convertSummaryToProtoBuf file header. */
    pb_ostream_t stream=pb_ostream_from_buffer(data+2,sizeof(data)-2);
    assert(pb_encode(&stream,SportSummaryMessage_fields,&summary));
    write_all("summary.data",data,stream.bytes_written+2);
    SportSummaryMessage decoded=SportSummaryMessage_init_zero;
    pb_istream_t input=pb_istream_from_buffer(data+2,stream.bytes_written);
    assert(pb_decode(&input,SportSummaryMessage_fields,&decoded));
    assert(input.bytes_left==0 && decoded.record.trackId==track && decoded.record.zone==FIXTURE_ZONE);
    assert(decoded.has_times && decoded.times.totalTime==3600 && decoded.times.exerciseTime==3600);
    assert(decoded.has_type && decoded.type.type==FIXTURE_TYPE && decoded.type.subtype==FIXTURE_START);
    assert(decoded.distance.distance==FIXTURE_DISTANCE && decoded.heartRate.averageHeartRate==120);
    if(FIXTURE_STRENGTH) {
        assert(decoded.times.has_restTimeBetweenGroups && decoded.times.restTimeBetweenGroups==2160);
        assert(decoded.calories.cal==240);
    }
    if(FIXTURE_SKIPPING) {
        assert(decoded.has_number && decoded.number.has_totalNumberOfJumps && decoded.number.totalNumberOfJumps==7200);
        assert(decoded.number.numberOfBreak==0 && decoded.number.maxNumberOfOnceJumps==7200);
        assert(decoded.has_frequency && decoded.frequency.averageFrequency==120 && decoded.frequency.highestFrequency==120);
        assert(decoded.calories.cal==600);
    }
    FILE *detail=fopen("detail.data","wb"); assert(detail);
    uint32_t anchor=0, anchors=0;
    for(uint32_t sec=0;sec<=3600;sec++) {
        uint32_t ms=sec*1000;
        if(sec==0 || ms-anchor>0x8000) {
            sport_node_timestamp_t stamp={0};
            stamp.tag=1; stamp.length=sizeof(stamp)-2;
            stamp.magic=0x686d7370; stamp.time=(uint64_t)track*1000+ms;
            assert(fwrite(&stamp,1,sizeof(stamp),detail)==sizeof(stamp));
            anchor=ms; anchors++;
        }
        if(FIXTURE_SKIPPING && sec==0) {
            sport_node_event_t event={0}; event.tag=4; event.length=sizeof(event)-2;
            event.relative_time=(uint16_t)(ms-anchor); event.index=1;
            assert(fwrite(&event,1,sizeof(event),detail)==sizeof(event));
        }
        if(FIXTURE_SKIPPING) {
            sport_node_rt_group_2_t frequency={0}; frequency.tag=6;
            frequency.length=sizeof(frequency)-2; frequency.relative_time=(uint16_t)(ms-anchor);
            frequency.fre=120; frequency.pace=0;
            assert(fwrite(&frequency,1,sizeof(frequency),detail)==sizeof(frequency));
        }
        sport_node_rt_heart_t hr={0}; hr.tag=8; hr.length=sizeof(hr)-2;
        hr.relative_time=(uint16_t)(ms-anchor); hr.hr=120;
        assert(fwrite(&hr,1,sizeof(hr),detail)==sizeof(hr));
        if(FIXTURE_SKIPPING && sec==3600) {
            sport_node_event_t event={0}; event.tag=4; event.length=sizeof(event)-2;
            event.relative_time=(uint16_t)(ms-anchor); event.index=6;
            assert(fwrite(&event,1,sizeof(event),detail)==sizeof(event));
        }
        if(FIXTURE_STRENGTH && sec>=120 && (sec-120)%300==0) {
            sport_node_strength_group_t group={0};
            group.tag=15; group.length=sizeof(group)-2;
            group.relative_time=(uint16_t)(ms-anchor);
            group.index=(uint16_t)((sec-120)/300+1);
            group.avg_hr=120; group.cal=10; group.time=120000;
            group.type=0; group.weight=0; group.times=12; group.unit=0;
            memset(group.reserved,0xff,sizeof(group.reserved));
            assert(fwrite(&group,1,sizeof(group),detail)==sizeof(group));
        }
    }
    assert(fclose(detail)==0);
    printf("{\"rec_size\":%zu,\"slot_size\":%zu,\"child_size\":%zu,\"child_slot_size\":%zu,\"timestamp_size\":%zu,\"hr_size\":%zu,\"anchor_count\":%u,\"summary_protobuf_roundtrip\":true}\n",
        sizeof(rec),sizeof(rec.manager[0]),sizeof(child),sizeof(child.child_info[0]),sizeof(sport_node_timestamp_t),sizeof(sport_node_rt_heart_t),anchors);
    return 0;
}
'''
np=root/'framework/utils/nanopb'
pb=root/'packages/services/sport/summary'
with tempfile.TemporaryDirectory(prefix='sport-one-hour-') as td:
    tmp=Path(td)
    (tmp/'types.h').write_text(header)
    (tmp/'generate.c').write_text(source)
    cmd=['gcc','-std=c11','-g','-O0','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-omit-frame-pointer','-I'+str(tmp),'-I'+str(np),'-I'+str(pb),str(tmp/'generate.c'),str(np/'pb_common.c'),str(np/'pb_encode.c'),str(np/'pb_decode.c'),str(pb/'sport_summary.pb.c'),'-o',str(tmp/'generate')]
    subprocess.run(cmd,check=True)
    result=subprocess.run([str(tmp/'generate'),str(track)],cwd=tmp,check=True,text=True,capture_output=True)
    abi=json.loads(result.stdout)
    # Decode files independently of the generator's native struct writes.
    rec=(tmp/'rec.manager').read_bytes()
    assert len(rec)==4+50*20 and struct.unpack_from('<H',rec)[0]==1
    assert rec[4]==0x11 and struct.unpack_from('<b', rec,5)[0]==zone
    assert struct.unpack_from('<HIII',rec,6)==(sport_type,3600,main_data,track)
    assert not any(rec[24:])
    child=(tmp/'child.manager').read_bytes()
    assert len(child)==504 and struct.unpack_from('<IIIHH',child)==(3600,distance,calories,120,1)
    assert struct.unpack_from('<IIIIII',child,64)==(sport_type,track,3600,distance,calories,7200 if skipping else (144 if strength else 0))
    assert rec[20]==start_type
    assert (tmp/'summary.data').read_bytes()[:2]==b'\x00\x80'
    detail=(tmp/'detail.data').read_bytes()
    pos=0; times=[]; anchors=[]; groups=[]; frequencies=[]; events=[]; current=None
    while pos<len(detail):
        tag,n=detail[pos:pos+2]; payload=detail[pos+2:pos+2+n]
        assert len(payload)==n
        if tag==1:
            assert n==12
            magic,current=struct.unpack('<IQ',payload); assert magic==0x686d7370
            anchors.append(current)
        elif tag==6:
            assert skipping and n==6 and current is not None
            relative,frequency,pace=struct.unpack('<HHH',payload)
            assert relative<=0x8000 and frequency==120 and pace==0
            frequencies.append((current+relative,frequency))
        elif tag==4:
            assert skipping and n==4 and current is not None
            relative,event=struct.unpack('<HH',payload)
            assert relative<=0x8000
            events.append((current+relative,event))
        elif tag==15:
            assert strength and n==34 and current is not None
            relative,index,hr,cal,duration,action,weight,reps,unit=struct.unpack_from('<HHBHIHHHB',payload)
            end_seconds=(current+relative-track*1000)//1000
            assert end_seconds==120+(index-1)*300
            assert (hr,cal,duration,action,weight,reps,unit)==(120,10,120000,0,0,12,0)
            assert payload[18:]==b'\xff'*16
            groups.append({'index':index,'start_seconds':end_seconds-120,'end_seconds':end_seconds,
                'duration_seconds':120,'reps':reps,'action_type':action,'weight_raw':weight,
                'unit':'kg','average_hr':hr,'calories_kcal':cal,'following_rest_seconds':180})
        else:
            assert tag==8 and n==3 and current is not None
            relative,hr=struct.unpack('<HB',payload)
            assert relative<=0x8000 and hr==120
            times.append(current+relative)
        pos+=n+2
    assert pos==len(detail) and times==[track*1000+i*1000 for i in range(3601)]
    assert len(anchors)==abi['anchor_count']
    if skipping:
        assert [t for t,f in frequencies]==times
        assert sum(frequencies[i][1]*(frequencies[i+1][0]-frequencies[i][0])/60000 for i in range(len(frequencies)-1))==7200
        assert events==[(track*1000,1),((track+3600)*1000,6)]
    assert [g['index'] for g in groups]==(list(range(1,13)) if strength else [])
    if strength:
        assert sum(g['duration_seconds']+g['following_rest_seconds'] for g in groups)==3600
        assert sum(g['reps'] for g in groups)==144
    # Publish only after all generation/validation has passed.
    base=out/'sport_record'; parent=base/str(track); leaf=parent/str(track)
    leaf.mkdir(parents=True)
    for name,target in [('rec.manager',base),('child.manager',parent),('detail.data',leaf),('summary.data',leaf)]:
        (target/name).write_bytes((tmp/name).read_bytes())
    (out/'source_types.h').write_text(header)
    (out/'generate.c').write_text(source)
    manifest={
        'synthetic':True,'sport':a.sport,'sport_type':sport_type,'start_type':start_type,'sync':'NOT_SYNCED',
        'track_id':track,'start':start.isoformat(),'end':(start+datetime.timedelta(hours=1)).isoformat(),
        'timezone_quarter_hours':zone,'duration_seconds':3600,'distance_meters':distance,'calories_kcal':calories,'heart_rate_bpm':120,
        'total_jumps':7200 if skipping else None,'frequency_per_minute':120 if skipping else None,'frequency_samples':len(frequencies),'events':events,
        'strength_groups':groups,'group_count':len(groups),'total_reps':sum(g['reps'] for g in groups),'rest_seconds':2160 if strength else 0,
        'heart_rate_samples':len(times),'timestamp_anchors':len(anchors),'abi':abi,
        'compiler':subprocess.check_output(['gcc','--version'],text=True).splitlines()[0],
        'verification':['summary file header 00 80 checked against production writer','project nanopb encode/decode roundtrip','independent manager byte checks','all TLV nodes parsed; timestamps cover exactly 3600 seconds','ASan/UBSan generator completed without diagnostics'],
        'limits':['Host compiler layout checked; device ABI/import not verified.','Synthetic data; no real sensor or App validation.','No effect.data or recov_hr.data; this sample does not test those features.','No device writes or reclaim/sync business tests executed.'],
        'source_sha256':{str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in list(files.values())+[pb/'sport_summary.pb.c',pb/'sport_summary.pb.h',pb/'sport_summary.proto']},
        'files':[]}
    if a.sport == 'treadmill':
        manifest['limits'].append('Current sport_record_sync.c skips App old-four types including treadmill; not a normal record-sync acceptance sample.')
    for p in sorted(base.rglob('*')):
        if p.is_file(): manifest['files'].append({'path':str(p.relative_to(out)),'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
    (out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    description = '力量训练；12 组 × 12 次自重动作，每组训练 120 秒后休息 180 秒；共 144 次；240 kcal；动作类型为 0（未识别）' if strength else '跑步机；6 km；360 kcal'
    if skipping: description='跳绳；普通手动运动；连续 3600 秒，120 次/分钟，共 7200 次，断绳 0 次，最长连跳 7200 次；600 kcal；包含开始/结束事件及每秒频率明细'
    readme_limit = '当前同步代码跳过 App 老四项，跑步机不可用于普通运动记录同步验收。' if a.sport == 'treadmill' else ''
    (out/'README.md').write_text(f'''# 1 小时合成运动记录\n\n{description}；未同步；{start.isoformat()} 开始；持续 3600 秒；每秒心率 120，共 3601 点（含起终点）。\n\n` sport_record/ ` 为记录根目录内容。manager 使用当前项目头文件抽取的原始结构体定义由主机 GCC 序列化；摘要使用项目原始 nanopb 编解码。时间单位以实际写入代码为准：manager 和 child.manager 均写秒，detail 时间为毫秒；child 头文件的毫秒注释与写入实现不一致。\n\n校验结果和文件哈希见 manifest.json。仅本地造数已通过；尚未验证设备 ABI、启动加载、App 展示或回收业务。{readme_limit}没有生成全局 effect.data，也没有恢复心率文件。不要整目录覆盖设备原有 rec.manager：本样本只登记这一条。\n\n重新生成（输出路径必须不存在）：\n\n```sh\npython3 {Path(__file__).resolve()} --source {root} --config {a.config} --sport {a.sport} --start {start.isoformat()} --output NEW_OUTPUT_DIRECTORY\n```\n''')
    if strength:
        import csv
        with (out/'groups.csv').open('w',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=list(groups[0])); writer.writeheader(); writer.writerows(groups)
archive=out.with_suffix('.tar.gz')
with tarfile.open(archive,'w:gz') as tar:
    tar.add(out,arcname=out.name)
print(json.dumps({'output':str(out),'archive':str(archive),'track_id':track,'verification':'PASS','files':manifest['files']},ensure_ascii=False,indent=2))
