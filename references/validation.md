# 脚本验证记录

验证日期：2026-11-09；源码：/home/chenrongjun/fuji；配置：configs/apollo5/apollo330_fuji_defconfig；编译器：gcc (Ubuntu 9.4.0-1ubuntu1~20.04.1) 9.4.0。

临时目录内执行，生成产物已清理；没有写入设备或修改原始导出记录。

- treadmill: generate, source nanopb roundtrip, ASan/UBSan and independent byte/TLV validation PASS
- strength: generate, source nanopb roundtrip, ASan/UBSan and independent byte/TLV validation PASS
- skipping: generate, source nanopb roundtrip, ASan/UBSan and independent byte/TLV validation PASS
- merge: count=2, both NOT_SYNCED, remaining slots zero, record copies and archive PASS
- duplicate track ID rejected before creating output PASS
- existing output rejected PASS
- timezone-less timestamp rejected PASS
- negative quarter-hour timezone (-03:30 -> -14) PASS
- modified input rejected against manifest PASS
- 00 00 summary header rejected even with updated manifest PASS

以上验证生成/合并工具，不表示目标手环 ABI、完整保存业务、协议传输或 App 解析/落库/展示通过。

## 通用定制入口追加验证（2026-11-09）

- activity: custom duration/fields, source nanopb, manager, TLV schedule, ASan/UBSan PASS
- swim: custom duration/fields, source nanopb, manager, TLV schedule, ASan/UBSan PASS
- rope: custom duration/fields, source nanopb, manager, TLV schedule, ASan/UBSan PASS
- strength: custom duration/fields, source nanopb, manager, TLV schedule, ASan/UBSan PASS
- merge: mixed not_synced/synced states preserved PASS
- no spec: verified default source/project, free training 3600s NOT_SYNCED PASS
- resolved_spec replay: byte-identical record data PASS
- invalid source sport rejected PASS
- wrong HR series length rejected PASS

泳池样本只验证请求标量字段；未验证泳池专项节点、设备或 App。活动样本只验证无暂停STOP窗口的序列化及启动类型映射，不表示真实识别算法或回填链路通过。
