# 周边高清扫图

用于小卡、明信片、拍立得、周边 PB（写真集）内页等的扫图还原。从实拍照片中提取、拉正画面，减弱反光、实物材质和拍摄色偏的干扰，整理为清晰、平整的 PNG 电子图稿，支持自定义尺寸与后续调色。

**v0.2.0 预览版** · [下载安装包](https://github.com/Huihuohuo/restore-collectible-artwork/releases/tag/v0.2.0) · [版本说明](docs/releases/v0.2.0.md)

主要面向平面周边，也适用于海报、封面等其他平面印刷画面。五种常见版型是快捷预设，其他尺寸使用自定义参数。PB 按单页画面处理；书页弯曲或书脊遮挡需先评估，不能直接视为平面照片。

```text
确认对象和目标宽高 → 提取拉正 → AI 外观还原 → 比例与内容检查
→ 先展示、交付初版 PNG → 用户可直接采用，或选择调色 → 另存调整成品
```

这是给 AI 助手使用的 skill，包含处理说明和本地工具。外观还原依赖运行平台的图像编辑能力；本地脚本不调用模型或网络。生成式还原可能改变细节，不等于恢复真实源文件或原始图层。

## 调用前先确认什么

AI 必须先取得对象类型、目标宽高和单位。用户没说齐，先问缺失项；已经说清楚就继续。不能从照片或“小卡”这个名称猜尺寸，也不再自动默认标准三寸。有边框时，明确尺寸是整件物品还是要提取的画面窗口。

例如：

> 使用 $restore-collectible-artwork 处理这张明信片。提取整张卡片，目标宽 148 mm、高 100 mm。保持原构图和颜色关系，先给我还原初版。

> 使用 $restore-collectible-artwork 处理这张 Instax Mini，只取内部画面，宽 46 mm、高 62 mm。先给初版，我看完再决定是否调色。

五个常见预设如下；用户明确选用表中尺寸，可以直接记录为已确认参数。

| ID | 范围 | 宽×高依据 |
| --- | --- | --- |
| photocard-standard | 标准三寸整卡 | 54×85 mm |
| photocard-large | 大三寸整卡 | 63×89 mm |
| instax-mini | Mini 内部画面 | 46×62 mm |
| instax-sq | SQUARE 内部画面 | 62×62 mm |
| instax-wide | Wide 内部画面 | 99×62 mm |
| custom | 用户确认的整件物品或画面窗口 | 用户提供 |

这些不是所有商业物品的通用规格。单位支持 mm、cm、in、px，分辨率与实物尺寸独立。具体规则见 [预设与自定义尺寸](skills/restore-collectible-artwork/references/formats.md)。

## 安装与依赖

下载发行版中的 `restore-collectible-artwork-skill.zip`，解压后将完整的 `restore-collectible-artwork` 文件夹放入目标助手支持的技能目录；也可直接使用仓库中的 `skills/restore-collectible-artwork`。Codex 可用项目的 `.agents/skills/` 或个人的 `~/.agents/skills/`。升级时保留旧版，再替换技能文件夹。显示名称为「周边高清扫图」，内部调用 ID 沿用 `restore-collectible-artwork`。

本地脚本需要 Python 3.10+、NumPy 和 Pillow。可在隔离环境安装：

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r skills/restore-collectible-artwork/scripts/requirements.txt
```

Windows 可用 `.venv\Scripts\activate`。图像编辑与 HTML 展示能力由宿主提供，不能仅凭支持 SKILL.md 就认定所有功能都可用。

## 处理与初版交付

以下命令从仓库根目录运行。每张图的 `geometry.json` 必须记录已确认的对象、尺寸及本张照片的四角。例如：

```json
{
  "format_id": "custom",
  "object_type": "明信片",
  "dimensions": {"width": 148, "height": 100, "unit": "mm"},
  "target": "whole_object",
  "source_size": [1600, 1200],
  "quad": [[100, 100], [1500, 100], [1500, 1046], [100, 1046]],
  "long_edge_px": 1700,
  "corner_radius_px": 0
}
```

上述四角只展示配置结构，必须换成本次源照片的实际坐标。按 [几何说明](skills/restore-collectible-artwork/references/geometry.md) 建立参照：

```sh
python skills/restore-collectible-artwork/scripts/prepare_artwork.py prepare \
  --input source.jpg --geometry geometry.json --out-dir work/prepared
```

让具有源图编辑能力的助手按准备报告 `generation` 选择输入和输出方式，再按 [还原指令](skills/restore-collectible-artwork/references/restoration-prompt.md) 编辑，保存为 `edited.png`。完整矩形照片保留全部背景，画面铺满不透明画布；圆角等轮廓只在物品外部透明。画内白底、黑底、渐变及暗场都保留，不做人像抠图。本地 Python 不完成 AI 外观还原。

创建 `job.json`（路径相对该配置文件）：

```json
{
  "source": "source.jpg",
  "geometry": "geometry.json",
  "reference": "work/prepared/reference.png",
  "reference_report": "work/prepared/report.json",
  "crop": "auto"
}
```

```sh
python skills/restore-collectible-artwork/scripts/process-artwork-local.py \
  --config job.json --new-generated edited.png --out-dir work/result
```

完成实际颜色、内容及内部构图复核后，先展示并交付 `work/result/digital-artwork.png`。用户可以直接保存初版；不必进入调色。只把实际存在的物品外部区域处理为透明；完整矩形填满画布时，PNG 全不透明是正确结果。程序会拦截生成结果中的画内透明缺损，不以重新覆盖 alpha 掩盖误抠。拍立得只交内部照片，白框另行合成。更多连续处理及可选工具衔接见 [提速流程](skills/restore-collectible-artwork/references/fast-path.md)。

## 初版之后：可选调色

亮度、对比度、饱和度、色温默认 0；进阶项为色相。每次从不可变初版计算，保留尺寸、构图及 alpha，不再次生成图片。

生成离线页面：

```sh
python skills/restore-collectible-artwork/scripts/adjust_artwork.py preview \
  --master work/result/digital-artwork.png --out-dir work/color-preview
```

打开 `work/color-preview/index.html`，可以下载初版、调整滑杆、查看前后对比、重置、导出当前 PNG、保存/载入调色参数。页面无需联网，适用于支持 CompressionStream/DecompressionStream 的现代浏览器。

如果宿主无法展示页面，使用参数调整：

```sh
python skills/restore-collectible-artwork/scripts/adjust_artwork.py apply \
  --master work/result/digital-artwork.png --temperature 8 --saturation -5 \
  --output work/artwork-adjusted.png
```

输出派生 PNG 和同名参数 JSON；已有文件不覆盖。独立白框不进入调色输入。细节见 [调色说明](skills/restore-collectible-artwork/references/color-adjustments.md)。

## 验证与打包

```sh
python -m unittest discover -s tests -v
python scripts/package_skill.py --output dist/restore-collectible-artwork-skill-local.zip
```

输出目录/文件必须是新的。ZIP 包含技能说明、脚本、参考文档与离线调色页面模板，不含个人原图、收藏册网站或模型。Node 只用于验证浏览器与 Python 的调色结果，不是技能运行依赖。

历史验证见 [记录](docs/validation.md)。首次照片四角仍需逐张确认；自定义尺寸不等于所有曲面、遮挡或极端比例都能自动处理。几何和颜色数值检查不能代替实际内容验收。
