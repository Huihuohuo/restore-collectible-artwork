# 固定比例参照、归一化和配准

脚本依赖 NumPy/Pillow，优先用环境已有运行时；缺失时在隔离环境准备 `scripts/requirements.txt`。脚本不调用模型、API 或网络。输出目录必须全新，源图与已认可图片不覆盖。

## 先锁版型，再拉正

每张照片配置 JSON。五种 ID 与比例见 [版型规则](formats.md)，脚本从 `formats.json` 读取。示例配置如下，四角数字仅演示形状，**必须换成本张照片实测的源像素坐标**：

```json
{
  "format_id": "photocard-standard",
  "source_size": [1600, 1600],
  "quad": [[300, 200], [1100, 220], [1120, 1450], [290, 1440]],
  "pixels_per_mm": 20,
  "corner_radius_px": 28,
  "edit_gutter_px": 32
}
```

- `source_size`：EXIF 方向校正后的源图尺寸。
- `quad`：左上、右上、右下、左下，按本次图片标定，不照搬案例或让用户测量实体。
- `format_id`：调用者指定的五种之一；普通小卡默认使用标准三寸，其他规格按用户说明选择。
- `pixels_per_mm`：可选正整数，默认 20，按版型生成整比像素。也可用 `output_size` 明确像素，但不能与其同时出现，且必须严格符合选定比例。两种方式均限制每边 128–4096px。
- `corner_radius_px`：小卡根据外形设置；其他轮廓可用相对配置文件的 `mask_path`。卡内白色保持不透明。
- 拍立得正面使用 `instax-mini` / `instax-sq` / `instax-wide`，四角只框内部照片；圆角为 0，不提供 mask。输出完整不透明矩形。编辑留白默认 0，可显式配置；小卡留白默认 32。

```sh
python3 scripts/prepare_artwork.py prepare \
  --input source.png --geometry geometry.json --out-dir work/item-01/prepared
```

输出 `reference.png`、`mask.png`、`edit-input.png`、源图副本、解析后的几何配置及含 `format` 的 `report.json`。工具不会自动识别四角。此阶段只校正几何，不负责去材质。

缺失/未知版型或不匹配的输出比例会拒绝，不能通过调宽容差或最后横纵拉伸来绕过。其他类别可显式设 `format_id: "custom"`，同时提供 `custom_reason` 和 `output_size`；仅复现旧例时可给无 `format_id` 的历史配置加 `--allow-legacy-geometry`。

## 编辑结果归一化

返回主体可能含外部留白或碎点。查看主卡体/画面边界后确定 `LEFT TOP RIGHT BOTTOM`，不能直接取碎点 alpha 的包围框：

```sh
python3 scripts/prepare_artwork.py normalize \
  --input edited.png --reference work/item-01/prepared/reference.png \
  --crop 18 6 999 1539 --out-dir work/item-01/normalized
```

上述裁切数不是通用默认值。归一化等比缩放加居中小幅裁取，拒绝超过 2% 的初始比例差，不独立拉伸横纵比例。这是工具返回主体的误差检查，**不是对固定版型放宽 2%**；最终轮廓仍严格沿用参照。检查裁取是否丢失边缘人物、文字或图案；不能以脚本成功代替视觉验收。

## 按需检查、对齐

两图须同尺寸：参照提供坐标和 alpha，还原图提供 RGB。主体应先等比归一到参照，再做内部位置校正。

```sh
python3 scripts/align_artwork.py \
  --reference work/item-01/prepared/reference.png \
  --artwork work/item-01/normalized/artwork.png \
  --out-dir work/item-01/alignment --mode auto
```

`auto` 接近零偏移时不变形，否则按留出误差选择整体仿射或平滑局部场。`check` 仅报告；`affine` / `local` 留给有依据的诊断。一般无需先 check 再 auto 跑两遍；只在实际发现问题时增加检查。

算法使用 1000px 宽的分析坐标、两级局部匹配、反向检查、稳健拟合、异常匹配排除及必要的正则化薄板样条。部分位置留出不参与拟合，变换后重新匹配并检查折叠及边缘外推。默认最大边缘外推为分析宽度的 1.5%，仅复制既有边缘颜色，不恢复未知画面。重要内容受损或边缘痕迹明显时不接受。

成功输出仍标为 `candidate_requires_visual_review`，包含 `aligned.png`、叠加图、报告及变换场。匹配少、覆盖不足、形变过大或未改善时停止自动校正；不靠反复放宽阈值得到伪成功。误差是 1000px 分析宽度的图像块位置估计，不是原生像素逐点或人体关键点精度。

颜色仅从还原图重采样，匹配用灰度/轻度平滑不作用于成品；配准不会恢复已被重写的细节。固定外形比例与内部内容对齐是两项独立要求，都要成立。
