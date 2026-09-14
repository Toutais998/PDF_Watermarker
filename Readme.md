# PDF 水印去除工具（Mark13）

Mark13 是当前唯一保留的版本，支持 Windows 与 macOS。工具用于合法持有的 PDF：
输入已知密码或空密码解除加密/权限保护，并检测、预览和删除文本、图片、矢量及
PDF 内置水印对象。程序不会猜测或破解未知密码。

## 项目结构

```text
PDFWaterMarker/
├── Mark13.py                     # 当前跨平台入口
├── pdf_watermarker/              # GUI、检测、移除及共享业务代码
├── pdf_decryptor/                # pikepdf/libqpdf 解密模块
├── assets/pdf_tool_icon.ico      # Windows/macOS 打包图标源文件
├── scripts/
│   ├── build_macos.sh            # macOS .app 构建
│   ├── build_windows.ps1         # Windows EXE 构建
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

## 为什么 macOS 安装包较大

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

## 安装与运行

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python Mark13.py
```

macOS：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python Mark13.py
```

## 测试

每次修改后必须对当前源码重新运行，不能沿用旧版本测试结论：

```bash
.venv/bin/python -m compileall -q Mark13.py pdf_watermarker pdf_decryptor tests
.venv/bin/python -m unittest discover -v
```

测试覆盖关键词水印检测、文本水印移除及正文保留、已知密码解锁、错误/缺失密码
拒绝、空用户密码解锁，以及输出 PDF 无加密验证。

## 构建

macOS：

```bash
./scripts/build_macos.sh
```

输出：`dist/Mark13Mac.app`。构建架构取决于 Python 架构；Apple Silicon 构建不能
直接用于 Intel Mac。默认只有 ad-hoc 签名，本机可运行；公开分发仍需 Apple
Developer ID 签名和公证。

Windows PowerShell：

```powershell
.\scripts\build_windows.ps1
```

输出：`dist\Mark13Final.exe`。Windows 仍使用同一入口、业务包和依赖集合。

`.venv/`、`build/`、`dist/`、`*.spec`、`.app` 和 `.dmg` 均为本机构建内容，
不纳入 Git。

## Mark13 变更

- 删除 Mark1-Mark12 历史入口，仅保留当前版本。
- 建立 `assets/`、`scripts/`、业务包和测试的清晰目录边界。
- 使用 `version.py` 作为界面版本号的唯一来源。
- 添加 Windows/macOS 可重复构建脚本。
- 文档记录 OpenCV 的实际用途和 macOS `.app` 体积组成。
