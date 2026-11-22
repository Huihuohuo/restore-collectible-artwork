# 周边电子原稿还原

**Restore Collectible Artwork** — an agent skill for restoring flat digital-artwork appearance from photos of photocards and Instax prints, with deterministic geometry correction.

把拍下来的小卡、拍立得画面提取并拉正，减弱实物表面与拍摄光影，再校正内部构图，导出 PNG 母版。适合保存、整理和分享收藏物的画面。

```text
实物照片 → 固定比例的几何参照 → 源图引导的外观还原 → 检查与按需对齐 → PNG
```

这是供 AI 助手使用的 skill，包含提示指导和本地处理脚本。外观还原需要宿主的图像编辑能力；脚本负责几何与导出，不包含模型、API 服务或自动上传网站。生成式还原可能改变微细节，并不等于找回真实源文件或原始图层。

## 能做什么

- 按明确版型拉正照片，保留不可变的几何参照。
- 指导图像编辑保留人物、手势、设计颗粒、颜色关系、文字与签名。
- 自动定位符合条件的生成主体，等比归一，按需进行全局或局部对齐。
- 连续完成处理、文件检查和 PNG 导出，减少手工交接。
- 将图像内容与后续相框、亮面/哑光、投影等材质分开。

| 版型 ID | 提取范围 | 比例依据（宽×高） | 默认像素 |
| --- | --- | --- | --- |
| `photocard-standard` | 标准三寸整卡 | 54×85 | 1080×1700 |
| `photocard-large` | 大三寸整卡 | 63×89 | 1260×1780 |
| `instax-mini` | Mini 照片窗口 | 46×62 | 920×1240 |
| `instax-sq` | SQUARE 照片窗口 | 62×62 | 1240×1240 |
| `instax-wide` | Wide 照片窗口 | 99×62 | 1980×1240 |

小卡比例是本项目选择的归一化规格，不代表全部商业产品的统一尺寸。Instax 只输出画面，不包含白框。规格来源见 [版型说明](skills/restore-collectible-artwork/references/formats.md)。

## 在 Codex 中安装

下载本仓库 ZIP 并解压，将整个 `skills/restore-collectible-artwork` 文件夹复制到：

- 当前项目：`.agents/skills/restore-collectible-artwork/`
- 个人技能目录：`~/.agents/skills/restore-collectible-artwork/`

如目标目录已存在，先保留旧版本，再决定替换。安装后新开一个聊天，提供照片并调用：

> 使用 $restore-collectible-artwork 处理这张标准三寸小卡，按 54×85 比例还原电子稿，保持原构图和颜色关系，返回透明背景 PNG。

或：

> 使用 $restore-collectible-artwork 处理这张 Instax Mini，只提取内部照片窗口，保持演出现场的暗场氛围，返回独立照片 PNG。

其他支持 `SKILL.md` 的助手可按各自的技能安装方式使用；图像编辑和本地脚本能力仍由宿主提供，尚未逐一验证其他宿主。

## 本地依赖

Python 3.10+、NumPy、Pillow。建议在隔离环境安装：

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r skills/restore-collectible-artwork/scripts/requirements.txt
```

Windows 的虚拟环境激活命令为 `.venv\Scripts\activate`。后续示例从仓库根目录运行；作为已安装技能时，将脚本路径换成实际安装目录。

### 1. 为本次照片建立参照

按 [几何说明](skills/restore-collectible-artwork/references/geometry.md) 创建 `geometry.json`，包括本张照片的尺寸、四角和版型。示例四角仅演示结构，必须通过本次原图确定。

```sh
python skills/restore-collectible-artwork/scripts/prepare_artwork.py prepare \
  --input source.jpg --geometry geometry.json --out-dir work/prepared
```

### 2. 外观还原

让具有源图编辑能力的助手查看 `work/prepared/edit-input.png`，按 [还原指令](skills/restore-collectible-artwork/references/restoration-prompt.md) 编辑，并将结果保存为 `edited.png`。本地 Python 不会完成这一步。

### 3. 连续处理并导出

在仓库根目录创建 `job.json`：

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

结果位于 `work/result/digital-artwork.png`，检查记录在 `work/result/run.json`。输出目录必须是新目录。自动裁切失败时，可查看实际主体、在配置中填写四个边界和 `generated` 路径，再按 [提速流程](skills/restore-collectible-artwork/references/fast-path.md) 使用手动裁切分支。

最后核对原图与结果的颜色、内容、构图和内部比例。程序通过不代表外观已获认可。

## 提速范围与已知边界

此次发布整合了单进程连续处理、生成主体自动裁切，以及可选的本机图像工具 start/finish 衔接。它主要减少重复加载与工具交接，不保证模型生成时间。

已有照片的一次历史试验，从任务预检到候选 PNG 约 46.4 秒；不包含首次照片四角标定、人工复核或此前对话。它不是稳定耗时承诺，也不是任意新照片的全自动测试。详见 [验证记录](docs/validation.md)。

原始照片的四角仍需逐张确认。文字、签名、颜色与关键内容需要视觉检查；配准只校正位置，不恢复被改写的细节。票根、手写背面、织带等类别不能直接套用小卡结论。

公开仓库包含流程、脚本和合成测试图案的生成代码，不包含个人原图、人物样图、收藏册网站、虚拟材质、密钥或本机运行输出。合成测试用于验证几何和文件处理，不是 AI 外观还原效果展示。

## 验证与打包

```sh
python -m unittest discover -s tests -v
python scripts/package_skill.py
```

打包程序生成 `dist/restore-collectible-artwork-skill.zip`，其中只有可安装的技能文件。`dist/` 不提交到仓库。
