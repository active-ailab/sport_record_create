# Sport record generator

用于生成与 `sport_record_save.c` 记录格式一致的测试运动记录。

## 当前脚本

- `generate_outdoor_hike_gpx.py`：生成带 GPS 和心率扩展的 GPX 文件。
- `generate_native_hike_record.py`：生成原生记录目录，包括 `summary.data`、`detail.data`、`gps.data`、图表、事件和分段文件。
- `update_native_record_indexes.py`：重建 `configs/rec.manager` 与 `configs/total.info`（单条记录，数值从路线实测计算）。
- `verify_native_record.py`：逐文件解码校验记录格式（summary protobuf / detail TLV / gps / event / lap / 图表 / 索引）。
- `build_import_package.py`：把记录组装成可直接导入 watch 的完整包（rec.manager + total.info + `<track_id>/`），并输出 ZIP 到 `/home/hgw/shaolu/sport_record_hike_20260807_1500_1600.zip`。
- `web_app.py`：启动本地网页，可选择手表（带 GPS）或手环（无 GPS）项目，配置运动参数并下载 ZIP 记录包；登山预置南京紫金山公开地图步道建议，亦可用高德规划路线。
- `sport_record_generator_standalone.html`：完全离线单文件网页，双击即可使用。

## 使用

在仓库根目录执行：

```sh
python3 tools/sport_record_generator/generate_outdoor_hike_gpx.py
python3 tools/sport_record_generator/generate_native_hike_record.py
python3 tools/sport_record_generator/update_native_record_indexes.py
python3 tools/sport_record_generator/verify_native_record.py
python3 tools/sport_record_generator/build_import_package.py
```

后续新增运动类型时，复用 `generate_native_hike_record.py` 中的摘要、detail 节点、GPS、心率和索引写入逻辑，替换运动类型、时间、路线及运动指标即可。

## 网页模式

```sh
python3 tools/sport_record_generator/web_app.py
```

打开 `http://127.0.0.1:8765/`。网页支持选择手表或手环项目。手表包包含 GPS 数据与 GPX；手环包不包含 GPS 数据、GPS 详情节点、坐标表单或 GPX，距离与步数依据时长和运动类型估算。登山默认推荐南京紫金山公开地图步道环线建议，地图数据来自 OpenStreetMap；这条线路用于生成测试记录，出行前应核实现场路线与开放状态。也可填写高德 Key 规划步行/骑行路线。高德 Key 和安全码会保存在当前浏览器的本地存储中，仅用于规划，不会写入记录包。生成结果会以 ZIP 下载，不会直接覆盖本地文件。

如果不想运行任何脚本，直接双击 `sport_record_generator_standalone.html` 即可。它在浏览器内完成二进制文件和 ZIP 打包。
