# 全运动与活动识别定制

## 参数询问与默认

优先使用用户已给出的参数和当前会话事实，缺失项合并成一次简短询问：项目/源码工程路径/配置、运动及产生方式、是否标记已同步、时长和所需数据。工具可用时用异步输入，等待期间先查源码和构建能力；约60秒无答复后用明确默认值继续。无需为每个缺失数字单独追问，也不把无回答当作设备写入或外部发布授权。用户说“不回答用默认值”之后，应自动回退，不无限等候。

优先级：本轮明确值 > 已确认会话值 > 可验证的当前工程信息 > references/defaults.json。项目/源码/配置必须一致：换了项目不能继续静默沿用 fuji defconfig；找不到新项目时说明并只用已验证默认工程生成本地样本，不标为新项目数据。

默认：fuji、/home/chenrongjun/fuji、configs/apollo5/apollo330_fuji_defconfig、自由训练、3600秒、手动、未同步、120bpm；未指定开始时间为 Asia/Shanghai 当前时间减时长、秒精度。输出使用新的独立目录，不固定D盘路径。使用默认工程前必须验证路径及配置存在；默认工程不存在时只能询问或给出缺依赖说明，不能伪造源码验证。

“是否同步”默认解释为索引 sync 状态：未同步=1，已同步=2。若用户指的是“实际发给App”，另走明确的设备/协议流程，不能因 sync=2 就声称完成同步。手动、自动识别auto、活动识别activity、App发起app是不同 origin，活动识别不属于单独的sport_type。

## 通用定制入口

先读取当前工程枚举，支持按完整枚举名或数字选择任何源码已声明运动；枚举存在与该项目功能启用、专用字段完整、App支持是不同层次。

```bash
python3 <skill目录>/scripts/generate_custom_record.py --source <源码根目录> --list-sports
python3 <skill目录>/scripts/generate_custom_record.py --spec <spec.json> --output <新目录>
```

`--source` / `--config` 命令行覆盖 JSON，未指定读取 defaults.json。`--spec` 可省略以使用默认值。旧 generate_record.py 保留三种固定一小时的回归样本；定制任务使用新入口。

通用脚本支持单项运动、1..86400秒、synced/not_synced、manual/auto/app/activity、恒定或逐秒心率、距离/卡路里/计数/主数据、protobuf 数值字段及最多1000个单字节TLV节点。数据按真实C类型写出，再解析管理结构、摘要头、protobuf、时间线和节点调度；通用自检不等于专用字段语义正确。

### 活动识别示例

```json
{
  "sport": "SPORT_TYPE_FREE_TRAINING",
  "origin": "activity",
  "duration_seconds": 1800,
  "sync": "not_synced",
  "heart_rate": 115,
  "calories_kcal": 150,
  "main_data": 150
}
```

当前源码内部 start_type=17，摘要 type.subtype=1；脚本要求配置 HMI_FEATURE_SUPPORT_ACTIVITY_SPORT_AR=y。基础示例仅模拟无暂停、以STOP结束的活动窗口。先查 `_fillType`、`_fillTimes`、`_sportDetailGetRelativeTimeAt`、活动心率统计和HR裁剪宏。真实识别启动延迟、有效PAUSE、回填、skip end等需要专用时间适配，不能只改 subtype 或给 activity 填一个普通时长就宣布识别链路验证完成。auto 的 arDelayTime 不能直接套用为 activity 的时长减项。

### 自定义摘要和节点示例

```json
{
  "sport": "SPORT_TYPE_SKIPPING_ROPE",
  "duration_seconds": 600,
  "sync": "synced",
  "count": 1200,
  "main_data": 1200,
  "calories_kcal": 100,
  "summary": {
    "number.numberOfBreak": 0,
    "number.maxNumberOfOnceJumps": 1200,
    "frequency.averageFrequency": 120,
    "frequency.highestFrequency": 120
  },
  "nodes": [
    {
      "at_ms": 1000,
      "type": "sport_node_rt_group_2_t",
      "fields": {"tag": 6, "fre": 120, "pace": 0}
    }
  ]
}
```

这里只有一个频率节点，若要求完整频率曲线，应由agent按需求生成每秒节点或扩展循环适配器，不能将单节点样本冒充完整曲线。脚本对nodes使用当前项目真实C结构体，自动写length及relative_time；未知字段/类型编译失败应解决，不能静默忽略。支持1字节tag=2..30，tag4/8由公共事件/心率生成器管理；双字节tag及依赖外部结构的类型需要扩展。summary仅接受二级dotted数值/bool标量并自动设置has位；数组、字符串、复杂嵌套需要专用适配。

heart_rate 为整数时生成恒定心率；为数组时必须有 duration_seconds+1 点。其他输入未显式设置的值会写入 resolved_spec.json；卡路里、距离、计数默认0，仅表示合成值，不代表生理模型。

## 各运动的数据适配规则

所有源码运动类型都可作为通用记录选择；用户要求专用数据时，agent必须定位对应生产/保存逻辑，按实际格式补足脚本或生成JSON节点，并执行独立字段校验，不停在“只支持三种”的答复。

- 力量：各组时间、重复数、重量/单位、动作枚举及休息，校验总计；已有固定样本可作回归。
- 跳绳：计数、频率、断绳、连跳数，频率积分与计数口径一致。
- 跑步/骑行：距离、速度/配速、步频或踏频、GPS和海拔按请求配套，核对TRIM和老四项同步跳过条件。
- 泳池/公开水域：泳姿、趟/分组、池长、划水率与距离关系，不能把每个泳池字段复制到公开水域。
- 滑雪/登山/潜水/球类/其他：从项目group和实际节点定位专项字段；不要虚构枚举、物理单位或App依赖。
- 复合运动：另适配multi=2、父摘要和child索引，不能用多个单项合并包代替。

先选择用户要求的最小有效数据集；若仅要求“造一条X”，可用公共格式+该类型的基础值，并在报告写明专用字段未填的边界。若用户明确要求GPS/分组/趟/REP等，必须完成这些字段生成与读回校验才交付；缺少项目依赖或消费者协议时报告具体缺口，不把通用文件标为专项成功。

## 产物与验证等级

保留 resolved_spec.json、manifest.json、原类型提取、生成C源码和压缩包。报告实际项目/配置/开始时间/origin/内部启动类型/摘要subtype/同步状态/定制项，列明默认回退项。缺省样本不应再带历史固定日期。

分开报告：通用格式验证、专项字段与时序验证、设备ABI与加载、真实识别/同步、App落库和展示。后者未执行不得从本地PASS或sync位推出。
