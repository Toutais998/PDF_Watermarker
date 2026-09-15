# PDF 水印去除工具（Mark15）

Mark15 是当前唯一保留的版本，支持 Windows 与 macOS。工具用于合法持有的 PDF：
输入已知密码或空密码解除加密/权限保护，并检测、预览和删除文本、图片、矢量及
PDF 内置水印对象。程序不会猜测或破解未知密码。

## 项目结构

```text
PDFWaterMarker/
├── Mark15.py                     # 当前跨平台入口
├── PDFWaterMarker.command        # macOS 双击启动快捷方式
├── pdf_watermarker/              # GUI、检测、移除及共享业务代码
├── pdf_decryptor/                # pikepdf/libqpdf 解密模块
├── assets/pdf_tool_icon.ico      # Windows/macOS 打包图标源文件
├── scripts/
│   ├── build_windows.ps1         # Windows EXE/安装包构建入口
│   └── generate_icon.py          # 图标生成工具
├── tests/                        # 自包含回归测试
├── requirements.txt
├── Readme.md
└── AGENTS.md
```

`pdf_watermarker/` 内部按职责拆分：

- `app.py`：Tkinter UI、文件导入、预览及会话生命周期。
- `detection.py`：文本、图片、矢量、显式水印及 ROI 检测。
- `processing.py`：水印移除、无加密保存和可选 OCR 输出。
- `content_stream.py`：PDF 内容流解析和浅色矢量过滤。
- `geometry.py`：画布/PDF 坐标与多边形运算。
- `models.py`、`constants.py`、`version.py`：共享模型、常量和版本号。
- `ui_preferences.py`：中英文、明暗主题、系统外观检测与设置持久化。

## 外观和语言

右侧顶部可以即时切换：

- 外观：跟随系统、明亮、黑暗；“跟随系统”在启动时读取当前系统外观。
- 语言：中文、English；按钮、标签、状态、候选类型和常用对话框随之切换。

设置保存在用户配置目录，不写入项目，不会污染 Git 工作区。

## 为什么需要 OpenCV

OpenCV 不参与 PDF 密码解锁。解锁由 `pikepdf/libqpdf` 完成；OpenCV 只服务于
“看渲染结果找水印”的检测路径：

- 灰度转换和浅色像素阈值分割；
- 形态学去噪和连通区域统计；
- 斜向浅灰水印带检测；
- 手动 ROI 的模糊、缩放、掩膜和跨页模板相似度比较。

如果删除 OpenCV，上述像素级检测和没有明确 PDF 对象标记时的视觉兜底需要重写，
仅 PDF 解锁、文本对象和显式 XObject 水印处理则不依赖它。项目使用的是
`opencv-python-headless`，不包含 OpenCV 自己的 GUI，但 macOS wheel 仍链接较多
图像/视频编解码动态库。

## 旧 macOS `.app` 为什么较大

本机 Apple Silicon 构建展开后约 221 MB，其中 `Contents/Frameworks` 约 208 MB。
主要占用如下（文件系统统计为近似值）：

| 组件 | 约占用 | 用途 |
|---|---:|---|
| OpenCV (`cv2`) | 118 MB | 像素级检测；其中主二进制约 40 MB、关联动态库约 78 MB |
| PyMuPDF | 46 MB | PDF 打开、渲染、对象编辑、redaction 和保存 |
| lxml | 8.7 MB | pikepdf 的 XML/XMP 元数据依赖 |
| Pillow | 7.9 MB | 页面预览、Tk 图像和图标处理 |
| NumPy | 7.1 MB | 像素矩阵和数值计算 |
| Python 3.14 运行时 | 约 11 MB | 独立运行所需解释器和标准扩展 |
| pikepdf/libqpdf | 约 6-7 MB | PDF 加密检查及授权解密 |
| Tcl/Tk 与资源 | 约 6 MB | 桌面 GUI 和拖放 |

最大来源是 OpenCV。macOS 的 `cv2` 目录内还包含 FFmpeg、AV1、H.26x、OpenEXR、
JPEG XL 等链接库；即使本程序不调用视频接口，动态链接关系也使得直接删库可能导致
`cv2` 整体无法载入。PyInstaller 的 `.app` 是依赖展开目录，不能拿压缩下载包大小
直接比较。PyInstaller 本身只是构建工具，不会被整体装进最终应用。

Mark15 不再为 macOS 生成 `.app`。它直接使用项目已有的 `.venv`，因此不会再复制
一份 OpenCV、PyMuPDF、Python 和 Tcl/Tk。项目总体仍需要这些运行库，但磁盘上只
保留虚拟环境中的一份；启动时也没有 one-file 解压过程。

## 安装与运行

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-windows-build.txt
python Mark15.py
```

macOS：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
./PDFWaterMarker.command
```

Finder 中可直接双击 `PDFWaterMarker.command`。它只定位当前项目目录并执行
`.venv/bin/python Mark15.py`，没有依赖复制，也不需要 PyInstaller。若移动整个项目
文件夹，快捷方式仍然有效。

## 测试

每次修改后必须对当前源码重新运行，不能沿用旧版本测试结论：

```bash
.venv/bin/python -m compileall -q Mark15.py pdf_watermarker pdf_decryptor tests
.venv/bin/python -m unittest discover -v
```

测试覆盖关键词水印检测、文本水印移除及正文保留、已知密码解锁、错误/缺失密码
拒绝、空用户密码解锁，以及输出 PDF 无加密验证。

## Windows 构建与 macOS 运行职责

Windows 可以在本地保留完整构建链、spec、EXE 和安装包（这些内容仍不提交 Git）：

```powershell
.\scripts\build_windows.ps1
```

输出：`dist\Mark15Final.exe`。Windows 构建依赖集中在
`requirements-windows-build.txt`，其中包含 PyInstaller。

macOS 遵循最小依赖、最少磁盘占用和最快启动原则：

- 不安装 PyInstaller；
- 不生成 `.app`、DMG 或重复依赖目录；
- 直接通过 `PDFWaterMarker.command` 使用 `.venv`；
- 开发完成后清除 `build/`、`dist/`、spec、测试截图和 Python 缓存。

## Mark15 变更

- 导出的 PDF 默认使用连续单列滚动（`OneColumn`）页面布局。
- 保存对话框默认打开源 PDF 所在文件夹，同时仍可浏览选择其他目录。

## Mark14 变更

- 新增明亮、黑暗、跟随系统三种外观模式。
- 新增中文和 English 界面模式，并持久化用户选择。
- macOS 从 221 MB `.app` 改为直接调用本地 `.venv` 的快捷启动方式。
- PyInstaller 从公共运行依赖移到 Windows 专用构建依赖。
- 明确 Windows 完整构建与 macOS 最小运行的职责边界。
