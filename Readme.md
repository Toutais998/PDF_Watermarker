# PDF 水印去除工具（Mark9）

## 当前版本

- 推荐源码：`Mark9.py`
- 上一版：`Mark8.py`
- 图标文件：`pdf_tool_icon.ico`
- 本地 EXE：`dist\Mark9Final.exe`（已完成本地构建，不纳入 Git）

## Git 同步范围

本项目只通过 Git 同步核心源码、图标、说明文档和 `requirements.txt`。

以下内容属于本地开发或编译生成文件，不是核心代码，不会同步到 GitHub：

- `.venv/`、`venv/`：Python 虚拟环境
- `build/`：PyInstaller 构建临时文件
- `dist/`：PyInstaller 编译生成的 EXE 文件
- `*.spec`、`__pycache__/`：打包配置和 Python 缓存
- 其他测试缓存、IDE 配置和本地环境文件

在其他电脑上 clone 项目后，需要先创建虚拟环境并安装依赖：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

之后即可在本地运行源码或重新生成 `build/`、`dist/`：

```powershell
python Mark9.py
```

直接运行当前版本源码：

```powershell
python Mark9.py
```

## 版本更新记录

每个版本的更新内容、新增功能以及所用的实现办法：

### Mark1 —— 初版

- 功能：关键词/边缘文本水印检测、图片水印检测、手动矩形选区、去除时用白色擦除。
- 办法：遍历文本块匹配关键词、按页面边缘判定；图片按 xref 处理；用矩形 redact 注解白填覆盖。

### Mark2 —— 检测增强

- 功能：改进关键词与边缘文本检测逻辑，新增跨页重复内容聚合，去除逻辑更稳定。
- 办法：按相对位置把跨页重复的文本/图形聚合为同一水印候选。

### Mark3 —— 图形水印与交互升级

- 功能：文本/图形双 Tab 候选列表；tkinterdnd2 拖拽导入；斜框选区；矢量图形检测；重复图形按指纹+位置聚合；选区分析可扫描全部页面的同位置重复图形。
- 办法：`get_drawings()` 生成路径指纹（类型/宽度/颜色/路径前缀）并跨页聚合；ROI 模板灰度匹配评分定位重复图形。

### Mark4 —— 预览与保存

- 功能：去除预览（生成临时 PDF 并载入预览）、保存当前结果、返回原文件。
- 办法：把去水印操作写入临时文件再打开预览；图片按 xref 删除引用 + 文本用局部 redaction 白填。

### Mark5 —— 浅色矢量与视觉兜底

- 功能：大面积浅色/半透明矢量识别（soft vector）、黑色边缘细框识别、选区视觉像素兜底、矢量用白多边形覆盖。
- 办法：用颜色亮度阈值（luminance）判定浅色矢量；对选区渲染灰度图做暗像素/对比度评分作为无对象层时的兜底。

### Mark6 —— 斜向浅灰水印（沪江风格）

- 功能：新增渲染像素级斜向水印检测（`_find_rendered_diagonal_watermarks`）；从内容流剥离 Pattern 型斜向水印（`_remove_rendered_diagonal_pattern`）。
- 办法：渲染灰度图 + 连通域 + 硬编码对角线距离过滤；内容流前缀含 `/Pattern scn f*` 时整段裁剪。

### Mark7 —— 二维码/背景图片与批量导入

- 功能：底部重复二维码图片、底部“扫一扫”说明文字、右侧新东方在线背景图；支持多选 PDF、递归导入文件夹、批量文件下拉浏览结果。
- 办法：重复图片按指纹+相对位置聚合；图片去除改为按页面删除引用，避免清空图片流后留黑块；批量文件顺序分析并缓存结果。

### Mark8 —— 斜向量水印安全去除

- 功能：
  1. 修复 Test-3 斜置浅灰“沪江德语”矢量水印：不再用整块白色矩形覆盖，而是直接删除页面内容流中落在选区内的浅色/半透明矢量路径，正文不再被大面积擦除。
  2. 兜底方案：内容流解析未命中时，只对选区内的浅色水印像素做小方格 redaction，暗色正文像素完全避开。
  3. 关键词文本检测收敛：仅当关键词命中且位于页面边缘或文字旋转时才判为水印，正文中恰好包含品牌词（如 Hujiang）的行不再被误删。
  4. 渲染斜向检测自动拟合主对角线方向并聚类平行带，不再依赖硬编码斜率/截距；页面已有矢量水印命中时跳过该检测。
- 办法：自研 PDF 内容流迷你解析器，tokenize 路径算子（`m/l/c/v/y/re/h` 与 `f/f*/B/B*/b/b*`）并跟踪图形状态（`q/Q` 保存恢复、`g/rg/k/scn` 颜色、`gs` 透明度、`cm` 变换矩阵），计算每个填充路径的包围盒（底部原点坐标翻转为顶部原点）并与目标矩形判定相交；凡浅色（亮度≥0.5）或半透明（alpha<0.9）的填充路径即删除。重建内容流时只切掉对应字节区间，其余内容原样保留。
- 验证：Test-3 全 321 页逐页比对提取文本为 0 处差异；斜向水印带内的浅色像素占比从 16% 降至约 1%，正文暗色像素保留率 99% 以上。

### Mark9 —— 加密 PDF 授权解密（当前版本）

- 在创建 PyMuPDF 文档和分析水印之前，先通过项目内 `pdf_decryptor` 模块检查 PDF 加密字典。
- 加密文件使用调用方提供的合法用户密码或所有者密码解密；空用户密码文件自动处理，非空密码通过隐藏输入框输入，密码错误可重试。
- 解密模块基于 pikepdf/libqpdf，只尝试给定密码，不包含暴力破解、字典攻击或未知密码恢复。
- 解密到会话级临时 PDF，经“未加密 + 页数一致”验证后再交给 Mark9 分析；原 PDF 不修改，程序退出时删除临时文件。
- 批量文件和重新选择文件时复用同一个已验证的临时副本，避免重复解密和对象编号变化。
- 支持 RC4 40/128 位、AES-128-CBC、AES-256-CBC（R=5/6）；qpdf 不支持的安全处理器会明确报错。

## 推荐操作

自动处理：

1. 打开或拖入 PDF。
2. 点击“分析水印”。
3. 在“文本水印 / 图形水印”中确认候选。
4. 点击“预览去除效果”。
5. 检查预览结果。
6. 满意后点击“保存当前结果”。

手动处理：

1. 翻到有水印的页面。
2. 用“矩形”或“斜框”框选水印。
3. 点击“分析所选区域”。
4. 选择是否扫描全部页面同位置。
5. 点击“预览去除效果”并保存。

## 本次打包结果

Mark9 已使用项目 `.venv` 构建 `dist\Mark9Final.exe`，约 97 MiB。构建清单已确认包含
`pikepdf\_core.pyd` 和 `pikepdf.libs\qpdf30-*.dll`；隐藏启动冒烟测试通过。

之前的 `Mark5Final.exe` 达到约 1.3GB，是因为直接用全局 Python 打包时，PyInstaller 误收进了 `torch`、CUDA、`cupy`、`pyarrow`、`onnxruntime`、`scipy`、`pandas` 等大型依赖。

这些库本项目不需要。
建议始终使用项目内 `.venv` 打包：

## 打包 Mark9

```powershell
.\.venv\Scripts\Activate.ps1
python -m PyInstaller --noconfirm --noconsole --onefile --clean --icon="pdf_tool_icon.ico" --name Mark9Final `
  --exclude-module torch --exclude-module cupy --exclude-module scipy --exclude-module matplotlib --exclude-module pandas `
  --exclude-module numba --exclude-module llvmlite --exclude-module pyarrow --exclude-module onnxruntime `
  --exclude-module tensorflow --exclude-module sklearn --exclude-module imageio_ffmpeg --exclude-module tables Mark9.py
```

输出位置：

```text
dist\Mark9Final.exe
```

## 打包前检查

```powershell
.\.venv\Scripts\python.exe -m py_compile Mark9.py pdf_decryptor\core.py
.\.venv\Scripts\python.exe -c "import fitz, cv2, numpy, PIL, tkinterdnd2, pikepdf; print('deps ok')"
.\.venv\Scripts\python.exe -m PyInstaller --version
```

如果 EXE 又异常变大，检查：

```text
build\Mark9Final\PKG-00.toc
```

如果里面出现 `torch`、`cupy`、`cublas`、`pyarrow`、`onnxruntime` 等，说明又混入了不需要的大型依赖，需要回到 `.venv` 重新打包。

## 维护建议

- 新版本从上一版复制，例如 `Mark8.py` -> `Mark9.py`。
- 每次新增版本或修改主要功能时，同时更新 `Readme.md` 和 `AGENTS.md`。
- 不直接覆盖旧版。
- 打包时使用 `python -m PyInstaller`，不要直接用 `pyinstaller`。
- 优先使用 `.venv`，不要用装了大量科学计算/AI 包的全局 Python。
- 打包后只需要分发 `dist\xxx.exe`。
