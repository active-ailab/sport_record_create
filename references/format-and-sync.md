# 源码依据、时间与同步排查

本参考来自 fuji 的一次实际造数和失败排查。函数名、路径、宏值均须对当前任务源码复核，不用历史行号当版本证据。适配不同项目时优先使用 `rg` 查真实赋值和宏，不能只沿用字段名和头文件注释。

## 格式定位

| 项 | 源码入口 |
|---|---|
| 类型 52 力量训练、21 跳绳、2 跑步机 | framework/engine/sportEngine/sport_type_auto.h |
| 起始类型 0 手动、6 SPORT_SHOW | packages/services/sport/include/sport_control_typedef.h |
| sync 0 INVALID、1 NOT_SYNCED、2 HAD_SYNCED；单项 multi=1 | packages/services/sport/include/sport_record_service.h |
| manager、child 结构体 | sport_record_service.h / sport_record_save.h |
| TLV 节点和 packed 布局 | packages/services/sport/include/sport_record_save_detail.h |
| 文件头、时长、主数据及路径实际写法 | packages/services/sport/src/sport_record_save.c |
| protobuf 结构及编码器 | packages/services/sport/summary/sport_summary.proto、.pb.h、.pb.c；framework/utils/nanopb |
| 同步候选筛选和包读取 | packages/services/sport/src/sport_sync/sport_record_sync.c |

单项运动布局：

```text
sport_record/
├── rec.manager
└── <父track_id>/
    ├── child.manager
    └── <子track_id>/
        ├── summary.data
        └── detail.data
```

单项样本父子 ID 相同。文件夹名是 UTC 秒的十进制，摘要 trackId 和 child_info 对应一致。脚本使用源类型抽取而非包含全部业务头文件，避免引入 RTOS 全量依赖；抽取的类型及用于生成的 C 文件留在产物中。主机小端布局自检不代表目标编译器 ABI 验证。

当前 fuji 自然布局：rec.manager=1004 B，slot=20 B，child.manager=504 B，child entry=44 B。脚本独立解析器固定支持此布局，容量改变或布局断言失败时先适配，不为凑长度改真实数据。

## 明细和语义

- `_sportSaveNode()`：常规 `[tag:1][len:1][payload]`，总长 len+2。tag>=0x1F 的双字节协议需另行处理，当前生成器仅使用单字节节点。
- 时间戳 tag=1：magic=0x686D7370、unix_ms。心率 tag=8：relative_ms uint16 + bpm uint8。
- `_sportDetailGetRelativeTime()` 在距锚点 **大于 0x8000 ms** 时写新锚点；不能把一小时毫秒塞入 uint16，也不能把秒当相对毫秒。当前每 33 秒补锚点。
- strength tag=15：组号、平均心率、卡路里、组用时 ms、动作、重量、次数、单位；reserved 填 0xFF。0 kg 自重，unit=0，动作 type=0 是未识别，不能擅自标为深蹲/卧推。
- skipping tag=6（RT_GROUP_2）：relative_ms、fre 次/分钟、pace。源码 `_saveSportFreq()` 和摘要 frequency 均将内部每秒频率乘以 60，因此脚本写120，不能盲信 proto 注释中的 counts/second。
- `_fillSportRecManager()`：力量训练主数据取消耗、跳绳取总次数、跑步机取距离。
- `_fillChildManager()`：total_time 和 sport_time 实际由 duration_net / 1000 写入，尽管头文件注释毫秒；力量训练/跳绳 number 写计数。
- `_convertSummaryToProtoBuf()`：缓冲头显式为 00 80，PB 从 offset 2 编码。曾误写 00 00；PB roundtrip 只覆盖 offset 2 之后，漏掉了头部。这是已证实的生成器缺陷，但没有 App 日志不能断言它是某次同步失败的唯一原因。
- strength 示例的训练1440 s+休息2160 s=运动净时长3600 s，休息属于运动内组间休息，不是 PAUSE；最后一个180秒休息也计入示例。3601 个心率点包括 t=0 和 t=3600 两端，不等于3601秒运动。

## 导出数据无法同步的排查顺序

1. 记录实际固件/App 版本和宏；保存原始文件及哈希。检查 rec.manager sizeof、count、有效 slot、目录 ID、child_num 和对应子目录，不能只看 sync 位。
2. 检查 summary 非零且头为00 80；child.manager 非零且结构有效。`sportRec_getChildSportsManager()` 在 child stat 失败或 size<=0 时返回 false，`_getRecordSize()` 随后返回0；开启 `CFG_SPORT_SKIP_INVALID_RECORD` 时还可能在筛选阶段跳过。
3. `skipAppOldFourSport()` 在当前实现按类型跳过1/2/3/4，并可能置同步位；自动生成的 App 跑步机样本不是普通记录同步验收样本。力量52、跳绳21不命中这个跳过条件，实际项目需再核对。
4. `sportRec_needSync()` 比较 track_id > reqTime，候选选择用 >=；旧时间记录可能被 App 水位挡住。改sync=1不保证重拉，不擅自清 App 数据/改系统时钟/修改所有 trackId 来规避。
5. `sportRec_stopSyncRecord(DETAIL, success)` 的成功来自传输结果，可能置sync=2。这不证明 App protobuf/TLV解析、数据库、上传或详情页成功；还要查其它置位调用者/selftest。
6. `_saveSummaryInfo()` 创建时 O_TRUNC，写失败只日志；`_convertSummaryToProtoBuf()` 旧实现可能仍返回编码长度并注册索引。索引里有记录不证明文件已成功写入。
7. 新记录有 STOP 明细但 child/summary/total.info 为0 B，是保存/导出异常线索，不能先认定为旧合成样本阻断。对比结束保存后设备侧的 stat 与导出后的大小；设备侧就为空查保存，设备侧非零而导出空查刷盘/挂盘流程。不要用伪造摘要“恢复”缺失真实字段。

最小日志关键词：`creat summary file fail`、`save summary info fail`、`sync summary info fail`、`save child manager fail`、`sync child manager fail`、`get child manager error`、`sportRec_needSync`、`data type`/`sync time`、GDSP `stop_transfer`。按同一 trackId 对齐 STOP、写盘、needSync、getSize、传输、App解析与落库。

## 导入边界

本地造数/合并可逆，但包内 rec.manager 只有所选样本。导入已有历史数据的设备应先保留真实索引，再明确合入空 slot 的方式；不得只复制新目录、遗漏索引，也不得整包覆盖而丢失其它 slot。

二进制数据用 PC 文件工具或真实导入通路；设备 echo 写文本、xxd 只读的 shell 不能凭空当二进制写入器。simx86 的 update_vmmc_simx86.sh 必须查看当前实现，已知旧版 sync 模式会清 system 分区；不能照旧命令无差别执行。挂盘时确认设备/模拟器文件系统已停止并按实际通路刷盘卸载，避免生产代码和外部工具同时写同一卷。
