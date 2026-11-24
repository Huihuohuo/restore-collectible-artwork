# 常见预设与自定义尺寸

五个预设是快捷入口，不是能力边界。每次新任务先取得对象类型及目标宽、高、单位；信息不全先问用户，不从照片猜尺寸，不自动选择标准三寸。用户明确选择下表中已展示的规格，也算确认尺寸。预设数据来源为 [formats.json](formats.json)。

| ID | 范围 | 宽×高依据 | 默认像素 |
| --- | --- | --- | --- |
| `photocard-standard` | 标准三寸整卡 | 54×85 mm | 1080×1700 |
| `photocard-large` | 大三寸整卡 | 63×89 mm | 1260×1780 |
| `instax-mini` | Mini 内部画面 | 46×62 mm | 920×1240 |
| `instax-sq` | SQUARE 内部画面 | 62×62 mm | 1240×1240 |
| `instax-wide` | Wide 内部画面 | 99×62 mm | 1980×1240 |
| `custom` | 用户明确的整件物品或画面窗口 | 用户提供 | 按目标比例选取 |

小卡两种是项目常见规格，不代表全部商业产品。用户提供 54×86 mm 等其他尺寸，直接用 custom，不修改成相近的预设。宽高按目标方向记录；横版同样使用明确的宽高，不让模型改变方向。

## 必须提供的信息

```json
{
  "format_id": "custom",
  "object_type": "明信片",
  "dimensions": {"width": 148, "height": 100, "unit": "mm"},
  "target": "whole_object",
  "long_edge_px": 1700
}
```

- `dimensions` 是**本次提取区域**的目标尺寸，不是源照片的像素尺寸。单位可为 mm、cm、in、px；像素尺寸必须为整数。
- `whole_object` 指整件平面物品，外部可透明；`picture_only` 指完整、不透明的矩形画面窗口。自定义任务必须明确 target。预设由对应定义确定 target。
- 选用预设仍要在配置记录 object_type 与 dimensions。实体尺寸必须与该预设一致；冲突时选择 custom。只提供了像素尺寸时，预设要求比例一致。
- 尺寸指整张相纸时，不直接把它当作拍立得内部画面的尺寸。先说明提取范围，并取得正确的画面尺寸；不同边框另行处理。

## 分辨率与比例

`output_size`、`pixels_per_mm`、`long_edge_px` 三选一。预设保留表中默认像素；自定义物理尺寸默认长边目标 1700px，优先选取不超过该目标的精确整数比例。像素单位自定义尺寸默认直接作为输出像素。

罕见小数比例可能无法在 4096px 内整除，仅允许短边至多半个像素的取整误差，并在报告中记录。此后生成归一与配准始终使用这张固定参照，不再独立横纵拉伸。每边仍限制 128–4096px。

`pixels_per_mm` 仅适用于物理单位。大海报无需按实体大小生成巨幅 PNG，可选择 long_edge_px。自定义尺寸不代表任何极端长宽比、模糊图、曲面或重叠区域都能自动配准；现有匹配与形变限制继续生效。

## 来源与历史记录

Instax 内部画面依据 [富士香港规格](https://www.fujifilm.com.hk/products/instant_photo/films/index.html) 和 [SQUARE FORMAT](https://www.instax.com/squareformat/en/)。

`--allow-legacy-geometry` 仅供明确复现没有 format_id 的旧参数，不用于新任务，也不能绕过新任务的对象/尺寸询问。已生成的历史初版和 v0.1.0 文件不自动重做。
