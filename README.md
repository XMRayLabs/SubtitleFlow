# SubtitleFlow

**专有软件 · Copyright © 2026 XMRayLabs. All rights reserved.**

字幕合并、简体中文翻译与 FCPXML 转换，支持 Windows 和 macOS。
项目源码可查看，但未经书面许可不得复制分发、转售或修改专有部分；安装运行备份与第三方许可证例外详见 [EULA](legal/EULA.md)。

## 下载与自动构建

每次推送 main 自动构建 Windows x64、macOS Apple Silicon、macOS Intel。
在 GitHub Actions 的 Desktop builds 页面下载对应系统 artifact，解压里面的应用 ZIP，打开程序即可，无需安装 Python 或依赖。
推送 v开头版本标签会在三个平台验证成功后生成 GitHub Release 草稿。草稿仅供发布审核，不自动提供给用户更新。
安装包与 update.json 自动上传到发布草稿。完成许可证审核、签名与安装测试后发布稳定版，客户端启动检查和手动检查即可发现版本。

官方更新仓库：XMRayLabs/SubtitleFlow。软件启动自动检查，用户确认后下载并校验 SHA-256，当前任务结束后启动安装。macOS 打开 DMG 后由用户拖入 Applications 完成替换，不承诺静默安装。

## 本机启动

已配置虚拟环境的 Windows 工作目录：

    .\.venv\Scripts\python.exe -m subtitleflow

首次安装（建议官方 CPython 3.14；不要使用 conda 运行时制作正式发布包）：

    python -m venv .venv
    # Windows: .venv\Scripts\activate
    # macOS: source .venv/bin/activate
    python -m pip install -r requirements-build.txt
    python -m pip install --no-deps --no-build-isolation -e .
    python -m subtitleflow

提供仅合并、仅翻译、合并后翻译。将 SRT 拖入列表，选择参数和输出目录，然后开始。仅合并且关闭 AI 时无需 API。API 地址填写服务商的 Base URL（通常以 /v1 结尾），模型名称由服务商提供。兼容 chat/completions，不要求服务商支持 JSON response_format。

默认每批 20 条字幕，不是正文物理行数。API 测试会发送一次最小请求。AI 辅助断句也会产生接口调用。请求顺序执行，临时故障重试，返回结构无效时重试一次；超过模型限制自动缩小批次。取消时等待正在进行的 HTTP 操作退出，单请求超时为 60 秒。

## 合并规则

目标 10 秒、静音 3 秒、容差 25% 均可调。间隔达到静音阈值强制分组；静音仅由字幕时间轴估算。
达到目标并句完就结束；有明确句末时完整句子优先，可以超过软上限。没有可靠句末时，达到目标后优先在停顿处结束；连续无标点字幕按软上限（10 秒为 13 秒）在原条目边界兜底。原单条不截断。
中英文句末标点与常见缩写使用本地规则判断。AI 模式通过原始条目 ID 给出句末边界，不修改正文、不跨静音阈值，仍不能保证所有语义判断正确。

## 输出与恢复

每次创建唯一任务目录，包含 originals、merged、translated 及 report.json；仅单步处理只创建对应输出目录。
originals 是原文件字节副本。同名文件自动追加编号。正式译文只有在校验完整后才生成。
.progress 保存已完成译文，模型、输入、参数或提示词版本变化后不复用旧缓存。重试未完成文件复用当前任务；重启后选择“恢复任务”并选择 report.json 所在目录。恢复使用任务原始副本，避免依赖移动后的源文件。

配置保存于系统应用数据目录。密钥默认不落盘；勾选记住后只使用 Windows 凭据管理器或 macOS 钥匙串。取消勾选并保存设置时删除已保存密钥。字幕及译文保留在本地任务目录，翻译及 AI 断句时会发送到用户配置的 API。

## 版权与许可

软件不主张输入和输出字幕的权利，不额外限制商用，不要求署名，不添加水印。
用户应具备原内容的相应授权，并核实 API 服务允许其使用方式；软件不保证将第三方内容版权转移给视频发布者。
详见 legal/OUTPUT_RIGHTS.md、legal/THIRD_PARTY_NOTICES.md、legal/LGPL_REPLACEMENT.md。

项目自有代码采用专有 EULA，详见 LICENSE 和 legal/EULA.md。公开仓库不构成开源授权，第三方组件仍适用各自许可。
正式分发必须通过实际依赖和二进制审核；开发构建不等于已完成商业发布验收。

## 测试与构建

    python -m unittest discover -s tests -v
    python -c "from pathlib import Path; Path('build').mkdir(exist_ok=True)"
    python -m tests.smoke_gui
    python tools/collect_licenses.py --fetch-sources
    python -m PyInstaller --noconfirm SubtitleFlow.spec

Windows 输出 dist/SubtitleFlow/SubtitleFlow.exe；macOS 输出 dist/SubtitleFlow.app。目录必须完整保留，不能仅复制 exe。Qt 动态库可替换；不使用 onefile。

正式打包步骤详见 RELEASE.md。未配置 GitHub 仓库时不检查更新。真实 API 翻译质量需使用用户自己的服务验证，测试使用模拟响应，不会调用付费接口。

## 模型下拉与本地 Open WebUI

填写 API 地址和密钥后自动加载模型列表，也可点击“加载模型”；下拉框允许手动输入不在列表中的模型。
根地址默认补 /v1，自定义路径保留。对于本机根地址，会通过不带密钥的 /openapi.json 检测 Open WebUI，并改用 /api。

本机示例：https://127.0.0.1:8081/api。该服务使用自签名证书时，可勾选“允许本机自签名证书”；选项默认关闭，且仅允许 localhost/回环 IP。公网 HTTPS 始终正常验证证书。本机请求绕过系统代理，不会自动降级到 HTTP。
Open WebUI 的 API Key 需在该服务中创建/取得；401 表示尚未提供有效密钥。填写后加载模型，再测试连接。

## SRT → FCPXML

处理模式选择“SRT → FCPXML（仅转换）”，添加一个或多个 SRT 后开始；无需 API。
其他处理模式可以勾选“处理完成后同时导出 FCPXML”，导出该流程最终生成的字幕（合并模式取合并结果，翻译模式取译文）。
结果位于任务目录 fcpxml，每个 SRT 对应一个 FCPXML。

选择与你的 Final Cut Pro 项目一致的帧率，支持 23.976/24/25/29.97/30/50/59.94/60；默认 25 fps。
默认 1920×1080，字幕通过 FCP 自带 Basic Title 表示为可编辑标题，白色、底部居中。FCPXML 不附带字体或 Motion 模板。
时间轴从 0 开始，字幕边界四舍五入到最近帧，极短条目至少保留一帧；重叠条目使用不同连接轨道。SRT 自身时间不被修改。
支持将 b/i 样式转换为标题文字样式，其他标签去除但保留文字。合并 SRT 会折叠重复的相邻 b/i/u 标签，保留原来的样式范围。

在 Final Cut Pro 中使用“文件 → 导入 → XML”，导入生成的独立字幕项目；可将标题复制到视频项目。普通 SRT 也可使用 FCP 的字幕导入功能。
导出器使用 Python 标准库实现，符合 Apple FCPXML 1.7 结构；Windows 上完成 DTD 校验，尚未在实际 Final Cut Pro 上验收。

格式参考：https://developer.apple.com/library/archive/documentation/Miscellaneous/Conceptual/LegacyDTDsFinalCutPro/FCPXMLDTDv1.7/FCPXMLDTDv1.7.html

## 现代单窗口界面与免依赖运行

主窗口仅显示文件队列、当前模式必要参数、输出目录和处理按钮。
“翻译设置”弹窗用于 API/密钥/模型配置；“高级选项”默认收起；更新和恢复放在“更多”菜单。
用户运行 dist/SubtitleFlow/SubtitleFlow.exe，无需安装 Python、pip、Qt 或其他运行依赖。必须保留整个目录，不能只复制 EXE。
Windows 本机已在清空 Python/Conda/Qt 环境变量、PATH 仅保留系统目录的条件下验证独立运行，同时测试合并、FCPXML、TLS 和系统凭据后端导入。
这不是全新虚拟机验收；实际操作系统最低版本仍需对应系统实测。

macOS 构建产物是独立 .app，DMG 安装流程为拖入 Applications，同样内含运行时。分别配置 Intel 和 Apple Silicon 构建，最低目标 macOS 13。
Windows x64、Mac Apple Silicon 和 Mac Intel 已通过 GitHub Actions 构建、44 项单元测试、GUI 冒烟测试及清除开发环境后的独立应用启动验证。Mac 的实际交互安装、签名、公证以及最终依赖许可审核尚待完成；macOS 13 最低版本还需单独实测。
