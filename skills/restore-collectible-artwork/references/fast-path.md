# 连续本地处理与可选工具衔接

以下路径相对**本技能目录**。`process-artwork-local.py` 在一个进程内加载辅助脚本，连续运行主体定位、等比归一、配准、检查和导出。默认省略额外诊断 PNG 和变换场文件，但保留相同的匹配算法、阈值、输出像素和检查。

## 第一次处理新照片

1. 查看原图，确定版型和本图四角，用 `prepare_artwork.py prepare` 建立参照。它不自动识别源照片四角。
2. 以 `edit-input.png` 做源图引导的外观编辑，保存结果。
3. 创建以下配置；路径都相对**配置文件所在目录**，不相对当前工作目录。

```json
{
  "source": "source.jpg",
  "geometry": "geometry.json",
  "reference": "prepared/reference.png",
  "reference_report": "prepared/report.json",
  "crop": "auto"
}
```

```sh
python scripts/process-artwork-local.py \
  --config /path/to/job.json --new-generated /path/to/edited.png \
  --out-dir /path/to/new-result
```

第一次新生成不需要旧生成图、旧成品或旧样本的路径。所有已提供的源文件都检查是否在运行中被修改。可以在 `expected_sha256` 中提供 `source`、`reference`、`reference_report`、`geometry` 的预期 SHA-256，拒绝误用旧参数。

输出 `digital-artwork.png` 和 `run.json`，并保留归一和配准报告。加 `--diagnostics` 可输出叠加图和变换场。输出文件夹必须不存在；任一检查失败不导出最终 PNG。

## 旧生成图复跑与人工裁切

配置可增加 `generated` 路径并使用 `crop: "auto"`。也可在实际查看主体后设 `crop: [left, top, right, bottom]`，四个边界为整数，右/下边界不含在裁切内：

```sh
python scripts/process-artwork-local.py \
  --config /path/to/replay.json --out-dir /path/to/new-result
```

人工裁切时**不要传 `--new-generated`**，因为该选项强制自动主体检测。将本次编辑结果写入配置的 `generated` 字段即可；使用新图时移除仅针对旧生成图/旧成品的指纹及 `baseline`，保留源图与参照的检查。

可选 `baseline` 仅用于同一生成输入的回归，要求 RGBA 完全相同；不同生成结果不适用。`--new-generated` 自动忽略旧生成图与旧成品的像素指纹，继续保留源图/版型/参照检查。`--rebuild-reference` 只用已知四角重建参照，不包含首次定位。

自动主体检测要求单个、居中的正视主体；小卡需要透明外部，Instax 允许完整不透明矩形。多主体、错误比例、含大块不透明背景等情况停止自动处理。它不会强制拉伸凑比例，最终 alpha 沿用参照，内部白色保持不透明。

## 可选：本机图像工具 start / finish

当宿主会把图像编辑结果保存到可读的本机 PNG 目录，且能连续编排工具时，可使用：

```sh
python scripts/artwork-job.py start \
  --config /path/to/job.json --prompt /path/to/prompt.txt \
  --edit-input /path/to/prepared/edit-input.png \
  --tool-dir /path/to/tool-png-directory --job-dir /path/to/new-job
```

start 返回提示词、编辑输入和 `transparent_background` 要求。宿主调用一次编辑工具，并记录请求前和返回后的 Unix 秒时间戳，再执行：

```sh
python scripts/artwork-job.py finish \
  --job-dir /path/to/new-job \
  --tool-started-unix REQUEST_UNIX_SECONDS \
  --tool-ended-unix RETURN_UNIX_SECONDS
```

finish 只接受相较 start 快照**恰好一张新 PNG**，自动处理并生成对照图与分步计时。它不猜测“最新文件”；零张或多张都会停止。该目录不能有其他同时进行的图像任务，也不适用于只返回 URL、覆盖同名文件或不能访问输出目录的宿主。

这是隔离的本机单任务衔接，不是并发服务。脚本自身不调用任何图像工具；调用、保存、成本及权限仍由宿主管理。宿主若提供明确的结果路径，也可直接用本地处理入口。不要为适配此可选接口改写宿主的工具返回方式。

参见[codex-tool-flow.js](codex-tool-flow.js) 的可编辑编排示例。它是特定 Codex 工具环境的配方，不是可直接用 Node 运行的脚本，也不是所有 Codex 版本的稳定接口。
