# 第三方组件与分发说明

运行时：
- CPython：PSF 及其包含组件的许可，附实际解释器 LICENSE 和 Python 源码中的许可声明。
- PySide6 Essentials / Shiboken6 6.11.2：选择 LGPL-3.0 路径。包含 Qt Core/Gui/Widgets 及必要平台动态依赖，按 LGPL_REPLACEMENT.md 交付源码与替换能力。
- keyring 25.6.0、jaraco.classes/context/functools、more-itertools：MIT。
- cryptography 50.0.1：Apache-2.0 或 BSD-3-Clause；cffi 2.1.1：MIT-0；pycparser 3.0：BSD-3-Clause。随 wheel 提供的原生组件许可证一并收集，仍需最终二进制审核。
- Windows pywin32-ctypes：BSD-3-Clause。
- Qt/Python 内嵌的第三方代码：逐项保留源码中的 COPYRIGHT、LICENSE、COPYING 及 SPDX 声明，实际构建审核后才可发布。

构建工具：
- PyInstaller 6.19.0：GPL 附商业分发例外；其例外允许生成非自由及商业应用，不据此要求本项目源码采用 GPL。
- PyInstaller hooks：构建期 hooks 使用 GPL；运行时 hooks 使用项目提供的宽松许可，附完整原文。
- setuptools、altgraph、pefile：MIT；packaging：Apache-2.0 或 BSD-2-Clause；macOS macholib：MIT。
- Windows 安装使用系统 IExpress；macOS 使用系统 hdiutil/pkgbuild，不复制这些构建工具到应用。
- GitHub Actions 仅用于构建，不随应用打包。

许可元数据和版本锁定本身不等于完成分发审核。运行 tools/collect_licenses.py 生成实际环境清单和许可副本；正式发布还必须通过 tools/release_gate.py 的实际安装内容审核。

UI 使用系统字体，无捆绑字体或第三方图片素材。应用图标由项目内原创 Qt 绘图代码生成，不使用 PyInstaller 默认图标或第三方素材。
