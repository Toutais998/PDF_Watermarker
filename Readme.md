# PDF 水印去除工具（Mark5）

## 当前版本

- 推荐源码：`Mark5.py`
- 旧版备份：`Mark4.py`
- 图标文件：`pdf_tool_icon.ico`
- 本地 EXE：`dist\Mark5Final.exe`

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
python Mark5.py
python -m PyInstaller --noconfirm --noconsole --onefile --clean --icon="pdf_tool_icon.ico" --name Mark5Final Mark5.py
```

直接运行源码：

```powershell
python Mark5.py
```

如果使用本项目的干净虚拟环境：

```powershell
.\.venv\Scripts\Activate.ps1
python Mark5.py
```

## Mark5 主要能力

Mark5 在 Mark4 基础上增强了图形水印识别：

- 识别文本水印、图片水印、重复矢量水印。
- 支持手动矩形选区和斜框选区。
- 支持预览去除效果，满意后再保存。
- 支持在预览结果上继续分析、继续去除。
- 新增黑色边缘细框识别，可处理 Anderson PDF 第 2 页开始顶部的黑色框。
- 新增选区视觉像素兜底：对象层识别不到时，也可以按渲染出来的暗像素/对比度建立候选。


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

之前的 `Mark5Final.exe` 达到约 1.3GB，是因为直接用全局 Python 打包时，PyInstaller 误收进了 `torch`、CUDA、`cupy`、`pyarrow`、`onnxruntime`、`scipy`、`pandas` 等大型依赖。

这些库 Mark5 不需要。

已重新使用项目内干净虚拟环境 `.venv` 打包：

- 新 EXE：`dist\Mark5Final.exe`
- 新大小：约 83.9 MB
- 这是当前合理版本。

## 重新打包 Mark5

建议始终使用项目内 `.venv` 打包：

```powershell
.\.venv\Scripts\Activate.ps1
python -m PyInstaller --noconfirm --noconsole --onefile --clean --icon="pdf_tool_icon.ico" --name Mark5Final `
  --exclude-module torch --exclude-module cupy --exclude-module scipy --exclude-module matplotlib --exclude-module pandas `
  --exclude-module numba --exclude-module llvmlite --exclude-module pyarrow --exclude-module onnxruntime `
  --exclude-module tensorflow --exclude-module sklearn --exclude-module imageio_ffmpeg --exclude-module tables Mark5.py
```

输出位置：

```text
dist\Mark5Final.exe
```

## 以后打包 Mark6

假设文件名是 `Mark6.py`：

```powershell
.\.venv\Scripts\Activate.ps1
python -m PyInstaller --noconfirm --noconsole --onefile --clean --icon="pdf_tool_icon.ico" --name Mark6Final `
  --exclude-module torch --exclude-module cupy --exclude-module scipy --exclude-module matplotlib --exclude-module pandas `
  --exclude-module numba --exclude-module llvmlite --exclude-module pyarrow --exclude-module onnxruntime `
  --exclude-module tensorflow --exclude-module sklearn --exclude-module imageio_ffmpeg --exclude-module tables Mark6.py
```

输出位置：

```text
dist\Mark6Final.exe
```

## 打包前检查

```powershell
.\.venv\Scripts\python.exe -m py_compile Mark5.py
.\.venv\Scripts\python.exe -c "import fitz, cv2, numpy, PIL, tkinterdnd2; print('deps ok')"
.\.venv\Scripts\python.exe -m PyInstaller --version
```

如果 EXE 又异常变大，检查：

```text
build\Mark5Final\PKG-00.toc
```

如果里面出现 `torch`、`cupy`、`cublas`、`pyarrow`、`onnxruntime` 等，说明又混入了不需要的大型依赖，需要回到 `.venv` 重新打包。

## 维护建议

- 新版本从上一版复制，例如 `Mark5.py` -> `Mark6.py`。
- 不直接覆盖旧版。
- 打包时使用 `python -m PyInstaller`，不要直接用 `pyinstaller`。
- 优先使用 `.venv`，不要用装了大量科学计算/AI 包的全局 Python。
- 打包后只需要分发 `dist\xxx.exe`。
