# SubtitleFlow

**字幕合并、简体中文翻译与 Final Cut Pro 字幕时间线导出。**

SubtitleFlow 是一款适用于 Windows 和 macOS 的中文桌面工具。将零碎字幕整理成更完整的句子，再通过自己选择的 AI 服务翻译；也可以直接将 SRT 转换为 FCPXML。所有操作集中在一个主窗口，支持批量处理。

[下载安装](https://github.com/XMRayLabs/SubtitleFlow/releases) · [使用指南](docs/USER_GUIDE.md) · [反馈问题](https://github.com/XMRayLabs/SubtitleFlow/issues)

## 功能

- **智能合并**：设置目标时长、静音间隔与容差，优先保留完整句子；自动整理相邻重复的加粗等样式标签。
- **AI 翻译**：兼容 OpenAI 格式接口，自定义地址、密钥和模型，翻译为简体中文；默认每批 20 条。
- **批量处理**：支持拖入多个 SRT，查看进度、取消、重试和恢复未完成任务。单个文件失败不阻塞其他文件。
- **FCPXML 导出**：将字幕导出为 Final Cut Pro 可编辑标题，支持多种常用帧率。
- **保留原始字幕**：每次任务创建独立目录，保留原始副本、处理结果和任务报告，不覆盖输入文件。
- **音频转录**：拖入音频或视频，在本机转录为 SRT，保存在源文件旁（同名文件不会被覆盖）。需要 Windows 与 NVIDIA 显卡；首次使用时在「音频转录」页安装转录服务（约数 GB，支持断点续传），可在「更多 → 卸载转录服务」中删除。

## 下载与安装

| 系统 | 安装包 | 安装方式 |
| --- | --- | --- |
| Windows x64 | `.exe` | 运行安装程序，使用桌面快捷方式启动 |
| Mac Apple Silicon | `macos-arm64.dmg` | 打开后将应用拖入 Applications |
| Mac Intel | `macos-x86_64.dmg` | 打开后将应用拖入 Applications |

安装包内置 Python、Qt 和运行依赖，用户无需另行安装开发环境。使用 ZIP 便携版时，请保留整个应用目录。

**发布状态：**当前产物为发布候选稿，签名、公证和最终分发验收尚未完成。草稿仅仓库维护者可见；测试构建可从 [GitHub Actions](https://github.com/XMRayLabs/SubtitleFlow/actions) 下载。Windows x64 和两种 Mac 架构已有自动构建与独立启动测试；macOS 13 为构建目标，最低系统版本仍需实机验收。

## 快速开始

1. 启动软件，将 SRT 文件拖入列表。
2. 选择「仅合并」「仅翻译」「合并后翻译」或「SRT → FCPXML」。
3. 根据需要调整参数；翻译时打开「翻译设置」，填写 API 地址和密钥，加载并选择模型。
4. 选择输出目录，点击开始。完成后打开结果目录。

默认合并目标为 **10 秒**，静音阈值为 **3 秒**。相邻字幕间隔达到静音阈值时分组；在目标时长加容差范围内优先选择完整句末，不拆分单条原字幕。默认上限向上取整为 13 秒；原始单条超过上限时单独保留。

## API 配置

支持 OpenAI 兼容的聊天接口，服务费用由所选 API 提供方收取。模型下拉列表可以自动加载，也允许手动填写。

| 配置项 | 说明 |
| --- | --- |
| Base URL | 服务商提供的地址，常见格式为 `https://example.com/v1` |
| API Key | 对应服务商的密钥，默认仅保存在当前会话 |
| 模型 | 加载列表后选择，或手动输入模型 ID |
| 每批条目数 | 默认 20，可按模型上下文大小调整 |

本地 Open WebUI 示例：`https://127.0.0.1:8081/api`。如果使用本机自签名证书，可在设置中开启对应选项；此选项仅适用于回环地址。HTTP 接口支持保留。

## 输出与恢复

完整的「合并后翻译」任务包含：

```text
任务目录/
├── 原始的srt/     原始 SRT 副本
├── 合并后的srt/    合并后的 SRT
├── 翻译后的srt/    翻译后的 SRT
├── 转换后的fcpxml/ 可选的 FCPXML
├── .progress/     翻译恢复缓存
└── report.json    任务报告
```

例如 10 个文件全部成功处理，会生成 10 个原始副本、10 个合并字幕和 10 个翻译字幕。恢复任务需要保留整个任务目录；原始副本缺失时请新建任务。恢复报告不会自动更换当前 API 地址或模型。

为避免异常文件耗尽资源，单个 SRT 上限为 32 MiB，单任务最多 10,000 个文件。更详细的合并规则、帧率和格式说明见[使用指南](docs/USER_GUIDE.md)。FCPXML 已做结构测试，实际 Final Cut Pro 导入仍需验收。

## 更新与卸载

软件可检查 GitHub 稳定版本。安全更新要求受信任的签名清单，下载完成和安装前分别验证文件摘要；处理任务结束后由用户确认安装。**当前采用发布者本机签名，尚未提供有效签名清单的版本不能自动更新。**Mac 更新仍需拖入 Applications，不进行静默安装。

Windows：退出软件，在「设置 → 应用 → 已安装的应用 → SubtitleFlow」中卸载。0.1.0 用户覆盖安装 0.1.1 或更新版本后可使用系统卸载入口。

Mac：退出软件，将 Applications 中的 SubtitleFlow 移到废纸篓。

卸载保留字幕输出、设置、已保存密钥和旧版本备份。若需清除密钥，请先在翻译设置取消「记住密钥」并保存。

## 隐私与授权

字幕和任务记录默认保存在本机。翻译及 AI 辅助断句会把相关字幕发送到用户配置的 API；开启记住密钥后使用系统凭据存储。更新检查连接 GitHub。

SubtitleFlow 专有部分采用 [EULA](legal/EULA.md)，未经许可不得复制分发、转售或修改；第三方组件保留各自许可证授予的权利。公开源码不代表开源授权。

**软件不主张用户输入或输出字幕的权利，不额外限制输出商用，不要求署名，不添加水印。**用户应拥有原内容的相应授权，并遵守所选 API 服务条款。详见[字幕权益说明](legal/OUTPUT_RIGHTS.md)与[第三方声明](legal/THIRD_PARTY_NOTICES.md)。

## 开发与发布

使用官方 CPython 3.14.6，在虚拟环境中安装锁定依赖：

```shell
python -m pip --isolated install --require-hashes --only-binary=:all: -r requirements-bootstrap.lock
python -m pip --isolated install --require-hashes --only-binary=:all: -r requirements-build.lock
python -m pip install --no-deps --no-build-isolation -e .
python -m unittest discover -s tests -v
python -m subtitleflow
```

CI 自动检查 Python 组件漏洞、执行测试并构建三个平台。发布流程与签名配置见 [RELEASE.md](RELEASE.md)，安全边界见 [SECURITY.md](SECURITY.md)。

Copyright © 2026 XMRayLabs. All rights reserved.
