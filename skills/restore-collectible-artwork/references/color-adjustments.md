# 初版之后的可选调色

先交付已核对的未调色 PNG 初版。用户满意即可结束；需要调色再调用本工具，不追加图像生成。每次从同一个初版重新计算，不把上一张派生图当作下一步输入。

## 独立交互页面

以下命令从技能目录运行：

```sh
python scripts/adjust_artwork.py preview --master /path/to/digital-artwork.png --out-dir /path/to/new-preview
```

生成 `index.html` 和初版原文件副本 `initial.png`。HTML 内嵌初版与程序，无外部资源、模型调用或网络请求；可在支持 CompressionStream/DecompressionStream 的现代浏览器直接打开。宿主支持 HTML 展示时提供页面链接；否则提供初版和参数方式，不能声称已展示滑杆。

默认所有参数为 0。页面支持初版下载、初版/当前调整切换、重置、当前调整 PNG 导出、参数 JSON 保存及载入。初版下载与零值导出使用初版文件原字节；其他导出直接编码计算后的 RGBA，避免 Canvas 半透明边缘的往返舍入。即使正在查看初版对比，下载当前调整仍导出所选参数。

页面中的棋盘格仅为透明预览。用户调色选择保留在当前页面，导出参数可用于后续恢复；不自动保存浏览器外的未确认偏好。

## 参数式调整

```sh
python scripts/adjust_artwork.py apply --master /path/to/digital-artwork.png \
  --temperature 8 --saturation -5 --output /path/to/new-adjusted.png
```

也可使用页面导出的参数：

```sh
python scripts/adjust_artwork.py apply --master /path/to/digital-artwork.png \
  --settings /path/to/artwork-adjustments.json --output /path/to/new-adjusted.png
```

| 参数 | 范围 | 作用 |
| --- | --- | --- |
| brightness | −100～100 | 暗到亮 |
| contrast | −100～100 | 柔和到鲜明 |
| saturation | −100～100 | 淡到浓 |
| temperature | −100～100 | 冷到暖 |
| hue | −180～180 度 | 进阶整体色相旋转 |

缺省值为 0，非法值拒绝，不静默截断。前四项沿用已认可试样的调色计算，色相为新增可选步骤。所有通道按 8 位 RGB 计算，不提供专业 ICC 校色或原始 RAW 冲洗。

参数文件可只包含以上字段；页面导出的文件还含母版 SHA-256，载入错误母版时拒绝。CLI 另存同名 `.adjustments.json` 记录来源指纹及实际参数；已有输出或记录均不覆盖。

尺寸、构图和 alpha 不变；全透明像素的 RGB 也保留。**输入仅为还原画面，不包含独立白框。** 拍立得白框在调色后另行合成，不在整张带框成品上套全图调色。工具不检测或自动保护已经烘焙在输入里的白框。
