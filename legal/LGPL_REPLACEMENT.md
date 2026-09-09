# Qt / PySide6 库替换与源码交付

本产品选择 PySide6、Shiboken6 和使用的 Qt 模块的 LGPL-3.0 许可路径。仅使用 QtCore、QtGui、QtWidgets 以及经检查的必要平台支持库。不得加入仅 GPL 许可的模块。

采用 PyInstaller onedir 目录式打包，不使用 onefile、不静态链接 Qt。Windows 动态库位于 _internal/PySide6 和 _internal/shiboken6；macOS 位于应用包 Contents/Frameworks 内（具体清单见 binary-inventory.json）。PySide6 Python 模块使用外部文件收集，允许替换匹配 ABI 的模块及动态库。

用户可备份应用目录，在自己的副本中以 ABI 兼容的修改版替换这些库。关闭应用后替换，保留对应架构及文件名，然后重新启动。应用不对这些库实施自定义签名锁定或完整性限制。macOS 本地修改后可能需要重新进行本地签名：
    codesign --force --deep --sign - SubtitleFlow.app

正式 macOS 签名启用 disable-library-validation，保留用户替换库的能力。不得发布到禁止上述权利的分发渠道。

legal/sources 提供构建版本对应的 Qt base、PySide/Shiboken 源码归档，包含上游构建文件；legal/licenses 提供许可文本与第三方版权通知。依赖修改时必须同步提供补丁、构建说明及修改源码。源码归档应随安装包一同分发，不能仅依赖会失效的上游链接。发布清单记录归档 SHA-256。

不同平台的 Python 和原生依赖可能不同。每个平台必须运行许可证收集、实际二进制清单检查及人工审核，不得直接把另一平台的审核结论当成本平台结论。
