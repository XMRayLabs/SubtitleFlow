# 本次实现验证记录

环境：Windows 11 x64、Python 3.14.6、PySide6 Essentials / Shiboken6 6.11.2。
当前本机 Python 来自 Miniconda，正式发行应使用干净官方 CPython 环境重新构建并审核原生依赖。

已验证：
- 33 项自动测试：SRT 编码/时间轴、长句合并、静音边界、无标点兜底、翻译批次与恢复、取消、错误重试、同名和批量失败隔离、更新版本/下载校验、发布审核缺失时阻止发布。
- 本地 HTTP 模拟服务：真正发送兼容 chat/completions 请求，解析翻译与 AI 断句结果，不调用付费服务。
- Windows 原生 Qt 界面：中文显示正常，界面启动后台任务并生成正确合并字幕。
- Windows PyInstaller onedir EXE：已运行独立打包程序并完成同一界面任务，结果见 build/packaged-smoke.json。
- 10 个同名 SRT 完整流程生成 10 个原始副本、10 个合并字幕、10 个中文译文；字幕不带水印或软件许可文本。
- Qt/PySide/Python 对应源码已下载至 legal/sources，许可证副本及版本清单已收集。
- 包内仅保留需要的 Qt Core/Gui/Widgets；已排除环境 PATH 误收集的非系统 ICU，修复独立 EXE 启动故障。

尚未验证或需要发布配置：
- 用户实际第三方 API 的翻译质量、模型兼容性和商用条款。
- macOS 两种架构构建、钥匙串、库替换、安装和更新实测；已有构建配置，当前主机不能代替 Mac 验收。
- Windows/macOS 安装包端到端安装及正式签名、公证；安装脚本和更新模块已实现，正式打包由 release_gate 阻止，直到具体安装内容审核完成。
- 实际分发包所有原生依赖/内嵌第三方代码的最终许可证兼容审核，特别是本地 Miniconda 运行时。当前清单状态为 pending-binary-review，不声明已完成商业发行授权审核。
- GitHub 发布仓库尚未配置，因此没有发布版本，也没有联网自动更新。

开发版可本地运行。不要单独复制 EXE；必须保留整个 dist/SubtitleFlow 目录。

## 单窗口现代化更新

- 44 项核心回归测试通过；模型下拉/手动输入和 FCPXML 模式 GUI 集成测试通过。
- 全新单窗口、API 设置弹窗、模式自适应参数、高级折叠、原创应用图标。
- Windows 打包程序在移除开发环境变量和搜索路径后完成合并/FCPXML，并确认 TLS 与系统凭据后端导入可用；见 build/standalone-validation.json。
- macOS 独立包验证已加入 CI，但尚未在本次本地环境执行；不能据此声明 Mac 安装包实测完成。
