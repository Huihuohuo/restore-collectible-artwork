# 连续本地处理与可选工具衔接

以下路径相对**本技能目录**。`process-artwork-local.py` 在一个进程内加载辅助脚本，连续运行主体定位、等比归一、配准、检查和导出。默认省略额外诊断 PNG 和变换场文件，但保留相同的匹配算法、阈值、输出像素和检查。

## 第一次处理新照片

1. 先取得对象类型和目标宽高/单位，再查看原图、选择常见预设或 custom 并确认本图四角。可用 [诊断工具](outline-diagnostics.md) 辅助寻找边线和圆角；prepare 本身只执行核对后的几何参数。
2. 在 geometry 中显式选 `generation_layout`，再用 `prepare_artwork.py prepare` 建立参照。工具不支持透明输出或能力未知时用 `full_canvas`（圆角也适用），省略留白或设为 0。按准备报告推荐的输入与输出方式编辑，保存结果。
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

输出未调色母版 `digital-artwork.png` 和 `run.json`，并保留归一和配准报告。加 `--diagnostics` 可输出叠加图和变换场。输出文件夹必须不存在；任一检查失败不导出最终 PNG。

完成实际内容与颜色复核后，先展示并交付这张初版。用户可直接采用；需要调色再进入 [可选调色](color-adjustments.md)。本地检查成功本身不等于内容已通过视觉复核。

## 旧生成图复跑与人工裁切

配置可增加 `generated` 路径并使用 `crop: "auto"`。也可在实际查看主体后设 `crop: [left, top, right, bottom]`，四个边界为整数，右/下边界不含在裁切内：

```sh
python scripts/process-artwork-local.py \
  --config /path/to/replay.json --out-dir /path/to/new-result
```

人工裁切透明物品时**不要传 `--new-generated`**，因为该选项按输出方式重新定位。将本次编辑结果写入配置的 `generated` 字段即可；使用新图时移除仅针对旧生成图/旧成品的指纹及 `baseline`，保留源图与参照的检查。完整矩形始终检查并使用整张返回画布。

可选 `baseline` 仅用于同一生成输入的回归，要求 RGBA 完全相同；不同生成结果不适用。`--new-generated` 自动忽略旧生成图与旧成品的像素指纹，继续保留源图/版型/参照检查。`--rebuild-reference` 只用已知四角重建参照，不包含首次定位。

输出方式先读 job 配置的 `generation_layout`，再读 geometry；旧配置都未声明时才按参照 alpha 回退，与画面颜色无关：

- `full_canvas`：参照可以有圆角，返回图必须完全不透明、比例符合要求，使用整张画布后恢复参照 alpha。生成前明确要求画面铺满且无环境/边距；已有结果要先目视确认此条件，比例相符本身不能证明裁切正确。部分画内背景被误抠时拒绝，不自动改走抠图路径。
- `transparent_object`：参照带外部透明轮廓，定位单个居中的正视主体。多主体、错误比例、含大块不透明外部环境时拒绝。归一前还会检查画内透明缺损，不能只凭最终 alpha 正确就通过。

配置可显式设置 `generation_layout: "transparent_object"` 复现旧的“完整矩形加透明留白”生成结果；不要用它绕过背景误删检查。若该分支收到不透明图片，报错会直接给出正式 `full_canvas` 配置和人工裁切的复跑写法。脚本不会仅凭比例自动把任意不透明图片当成完整设计。两种路径都保留原比例、配准和边缘限制，最终 alpha 沿用参照。

例如，圆角参照对应的旧生成图经目视确认整幅就是设计，可在 job 中设 `generation_layout: "full_canvas"`、`crop: "auto"`，直接使用 `--new-generated edited.png`；不再需要绕道手工全幅坐标。生成图若仍含桌面、白色外边框或其他环境，要先处理裁切问题。

## 可选：本机图像工具 start / finish

当宿主会把图像编辑结果保存到可读的本机 PNG 目录，且能连续编排工具时，可使用：

```sh
python scripts/artwork-job.py start \
  --config /path/to/job.json --prompt /path/to/prompt.txt \
  --edit-input /path/to/prepared/edit-input.png \
  --tool-dir /path/to/tool-png-directory --job-dir /path/to/new-job
```

start 按选定输出方式返回 `generation_layout` 和 `transparent_background`，把对应要求附在提示词后，保存实际指令 `tool-prompt.txt`。宿主使用返回的完整 `prompt`。全幅路径若传入带透明留白的旧输入会在调用前拒绝：矩形可用 `prepared/reference.png`；圆角应在新目录按 `full_canvas` 准备无透明留白的 `edit-input.png`。宿主调用一次编辑工具，并记录请求前和返回后的 Unix 秒时间戳，再执行：

```sh
python scripts/artwork-job.py finish \
  --job-dir /path/to/new-job \
  --tool-started-unix REQUEST_UNIX_SECONDS \
  --tool-ended-unix RETURN_UNIX_SECONDS
```

finish 只接受相较 start 快照**恰好一张新 PNG**，自动处理并生成对照图与分步计时。它不猜测“最新文件”；零张或多张都会停止。该目录不能有其他同时进行的图像任务，也不适用于只返回 URL、覆盖同名文件或不能访问输出目录的宿主。

这是隔离的本机单任务衔接，不是并发服务。脚本自身不调用任何图像工具；调用、保存、成本及权限仍由宿主管理。宿主若提供明确的结果路径，也可直接用本地处理入口。不要为适配此可选接口改写宿主的工具返回方式。

参见[codex-tool-flow.js](codex-tool-flow.js) 的可编辑编排示例。它是特定 Codex 工具环境的配方，不是可直接用 Node 运行的脚本，也不是所有 Codex 版本的稳定接口。
