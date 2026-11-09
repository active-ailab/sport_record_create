---
name: screenless-sport-record-generator
description: 为 ZeppOS 无屏手环创建各类运动及活动识别记录，按项目源码与用户要求定制时长、同步状态、心率、摘要、分组和明细，合并共享索引；信息缺失先询问，无答复使用可验证默认值。用于运动记录造数、活动识别造数、定制数据与记录合并；不代替真实识别或 App 同步验收。
---

# 无屏手环运动记录自动生成

交付可复用脚本、独立的 sport_record 目录、manifest 校验清单和 tar.gz。默认新记录为未同步；“合并”表示多场独立运动共用索引，不擅自改为复合运动。设备写入、固件修改、飞书发布不因使用本 skill 自动获得授权。

## 工作入口

先复用会话已确认参数。缺少项目、工程路径、实际配置、同步状态或定制要求时，按 [参数询问与全运动定制](references/customization.md) 合并询问；等待期间先查源码，约60秒无答复后明确列出默认值继续。不要重复追问已回答项。默认值见 [defaults.json](references/defaults.json)，它们是可验证的回退值，不是其它项目的配置依据。

默认：fuji、`/home/chenrongjun/fuji`、apollo330_fuji_defconfig、自由训练、手动、未同步、3600秒、120bpm；开始时间为用户时区下刚结束的一段时间。当前目录/会话已确定工程时优先使用该工程。是否同步需区分“索引状态”与“实际向App传输”，默认仅生成未同步本地文件。

## 全运动和定制入口

工作流接受任意源码已声明运动，包括活动识别产生的运动。先查工程枚举和group/保存路径，根据用户要求生成公共字段与专用数据；遇到未预置类型应按源码适配，不以三种固定样本为能力边界。通用格式、专用字段、设备与App验证要分开报告。

```bash
python3 <skill目录>/scripts/generate_custom_record.py --source <源码根目录> --list-sports
python3 <skill目录>/scripts/generate_custom_record.py --spec <定制JSON> --output <新目录>
```

详见 [定制参数与示例](references/customization.md)。通用入口支持1..86400秒、全源码运动枚举、manual/auto/app/activity、已同步/未同步、恒定或逐秒心率、数值摘要字段和按时间安排的真实结构体节点。数组、复杂时间窗口、双字节节点、复合运动或未实现的专项字段按实际源码扩展并验证，不降级为只改sport_type。枚举可选择不表示该项目已启用所有运动或App支持全部内容。

活动识别不是独立sport_type：当前源码内部start_type=17，摘要subtype映射为1。须核对当前项目开关和有效开始/结束窗口，不能把普通记录改一个类型就称为真实识别过程验证。

生成前按 [源码格式与限制](references/format-and-sync.md) 核对枚举、写入函数和有效宏，读取相关AGENTS.md。主机序列化和nanopb编码不代替真实START/STOP保存、设备ABI或App验证。保留实际参数resolved_spec.json、校验清单和源码指纹。

## 已验证的固定回归样本

`generate_record.py` 保留固定3600秒的 `strength`、`skipping`、`treadmill` 回归入口，传入 `--source`、`--config`、`--start`、`--output`。该旧入口仅生成sync=1；通用定制用上面的新入口。

当前合成配置：

| 类型 | 内容 |
|---|---|
| strength | 52；手动；12 组×12 次，每组训练 120 s 后休息 180 s（含最后一组后的休息）；动作 0 未识别，0 kg；144 次、240 kcal |
| skipping | 21；手动；连续 120 次/分钟、7200 次、0 次断绳、600 kcal；每秒频率及 START/STOP |
| treadmill | 2；App 发起；6 km、360 kcal；按 TRIM 的 App 老四项形态只含时间戳和心率；当前源码普通记录同步会跳过它，不能用于该链路验收 |

上述固定回归样本均含每秒 120 bpm 心率和周期时间锚点，时间为人工指定，卡路里等为测试值。没有生成全局 effect.data、total.info 或 recov_hr.data。若需要量化、恢复心率、App 业务字段完整性，需另补合法数据并验证相应消费者，不能用占位字节糊过。

## 合并样本

```bash
python3 <skill目录>/scripts/merge_records.py \
  --input <力量训练生成目录> \
  --input <跳绳生成目录> \
  --output <新的合并目录>
```

脚本仅处理带 manifest 和一致源码/配置指纹的合成样本；保留每条 slot 的同步状态，重建 count 并逐字节核对复制内容。拒绝重复 ID、旧错误摘要头、50 条溢出和已有输出，不暗中覆盖或淘汰。合并后仍可作为新的输入包。

用户真实导出目录不能直接交给这个合并脚本。若用户要求在原设备索引上追加，先备份原索引及目录，验证所有有效 slot、实际 ABI、count、空位和目录关系，再单独设计合入操作。包内 rec.manager 只代表所选样本，不能直接覆盖还需保留其它历史记录的索引。

## 必须保留的验证

- 检查 summary 完整文件头为 **00 80**，不是 00 00；只做跳过两字节后的 protobuf 解码会漏检。
- 字段按项目实际写入单位生成。此源码 manager/child 时长写秒，detail 相对时间和分组用时写毫秒；child 头文件注释与实现不一致。
- 独立解析全部 TLV，确认请求时长的时间覆盖、锚点、每秒数据和分组/计数自洽；不能只检查文件存在或大小。
- 使用 GCC 告警、ASan/UBSan 运行生成器；脚本的 Python 断言不能通过 `-O` 禁用。失败不能报告通过。
- 检查 manifest 哈希、索引/目录 ID、请求的 sync 状态（默认1）和压缩包内容。修改脚本后运行相关类型，并验证旧类型产物未意外改变。

报告区分“本地生成与校验通过”“设备加载”“协议传输”“App 解析/落库/展示”。未运行的阶段明确说明；不以 sync=2 代替 App 落库证据。不输出业务源码覆盖率，因为生成器没有测试保存或同步业务实现。

## 遇到无法同步

先读 [格式与同步排查](references/format-and-sync.md) 的排查部分。保留原始导出数据；优先检查完整头、索引及 child/summary 零字节、请求时间水位，再看固件与 App 日志。不能凭本地快照将 App parser 或文件系统故障定为唯一根因。

设备命令/挂盘/写盘走可用的 watchlink-v3；完整链路分析用 active-sport-data-e2e-analysis；需要新建真实业务 UTEST/主机测试时用 active-embedded-native-testing。本 skill 自带脚本本身不连接设备。

脚本实际运行的范围及结果见 [验证记录](references/validation.md)，适配新源码或修改脚本后应重新验证。
