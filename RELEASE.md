# 发布操作

## 开发构建与正式发布

开发构建用于验证功能，不允许直接声称完成许可证或双平台发行验收。
推荐使用官方 CPython 的干净环境，按 requirements-build.txt 安装固定版本。各平台分别构建。
本机当前 Python 来自 Miniconda，其打包的原生依赖必须额外审核，不能直接沿用官方 CPython 的来源结论。

1. 运行全部测试和原生 GUI 冒烟测试。macOS 分别验证 Apple Silicon 与 Intel 所需版本。
2. tools/collect_licenses.py --fetch-sources 收集实际许可证、对应 Qt/PySide/Python 源码及哈希。源码归档会随应用打包。
3. PyInstaller onedir 构建，检查 build/warn-SubtitleFlow.txt。禁止静态链接 Qt、禁止 onefile。
4. 对 Windows EXE 或 macOS 应用完成正式代码签名。macOS 使用 packaging/macos-entitlements.plist，允许替换动态库，并进行公证。签名与公证凭据不入库。
5. 运行 tools/release_gate.py dist/SubtitleFlow --inventory-only（macOS 使用 dist/SubtitleFlow.app），取得实际文件清单。
6. 审查所有原生依赖及内嵌第三方代码、完整许可通知、对应源码、库替换能力和平台实测。将真实证据填入 legal/release-audit.json。不能为了通过检查填写未经验证的 true。
7. tools/package_release.py --audit legal/release-audit.json 通过审核后生成 Windows IExpress 安装程序或 macOS DMG。安装包签名、公证后更新对应 .asset.json 中的最终 SHA-256。
8. 汇总各平台 .asset.json，通过 tools/make_update_manifest.py --repo owner/repo --version 0.1.0 --assets dist/installers --output dist/installers/update.json 生成更新清单。
9. 发布 GitHub 稳定版 v0.1.0，上传安装包和 update.json。仓库只用于发布不代表必须公开业务源码。对应源码及许可已随包附带，可同时单独提供下载。

审核 JSON 示例结构（以下不是审核结论）：

    {
      "platform": "win32",
      "reviewer": "填写实际审核人",
      "inventory_sha256": "填写 build/binary-inventory.json 的 SHA-256",
      "licenses_compatible": false,
      "all_native_dependencies_reviewed": false,
      "corresponding_sources_complete": false,
      "library_replacement_verified": false,
      "notices_complete": false,
      "platform_smoke_passed": false
    }

legal/release-audit.json 不包含在包内，避免审核摘要与包内容循环依赖。每次修改代码、依赖或签名后重新生成清单并审核。发布检查失败不得绕过生成正式安装包。

## 更新行为

GitHub Releases latest → update.json → 匹配系统及架构 → 用户确认下载 → SHA-256 校验 → 等待任务结束 → 用户确认打开安装包。下载失败或校验失败不替换当前安装。Windows 安装前检查程序已退出；macOS 用户通过 DMG 完成替换。

真实 GitHub 仓库、签名身份和公证信息由项目所有者配置。仓库未配置时不联网检查。

## GitHub 自动化

main 推送自动构建三平台完整应用 ZIP 并上传 Actions artifacts；标签 vX.Y.Z 自动创建待审核 Release 草稿。
应用 ZIP 包含运行时，用户不需要 Python。macOS 必须保留 .app 包和可执行权限。
草稿发布后才会被客户端 latest 检查发现；发布稳定版前必须上传正式安装包和 update.json，完成既有 release_gate 审核及平台签名。
默认官方仓库为 XMRayLabs/SubtitleFlow，旧设置中空仓库会迁移到官方值。

## 转录服务发布

转录服务独立于主程序构建和发布：主程序只通过本机 HTTP 接口调用服务，不包含 torch 等重型依赖。标签 `transcriber-vX.Y.Z` 触发 `.github/workflows/transcriber.yml`；主程序的 `vX.Y.Z` 标签不会构建转录服务，反之亦然。

1. 在仓库变量中设置经过许可审核的静态 LGPL ffmpeg 下载地址 `TRANSCRIBER_FFMPEG_URL` 及其 `TRANSCRIBER_FFMPEG_SHA256`，工作流校验不符即失败。
2. 构建作业打包 PyInstaller onedir 服务包，用假模型验证可独立启动，下载固定 revision 的模型计算逐文件哈希（模型不上传），压缩后按 1900 MiB 分卷，产出 `transcriber-payload.json`。这个作业装 torch，不接触签名密钥。
3. `draft-release` 作业（见下方「CI 签名环境」）按 payload 复算分卷哈希、签出 `transcriber.json`、删除 payload，再创建 Release 草稿。草稿里应只有分卷与 `transcriber.json`。
   CI 不可用时的后备：取下 payload 与分卷，本机运行 `python tools/transcriber_release.py --sign-payload transcriber-payload.json --parts-dir <分卷目录> --output release-assets --use-local-key`，上传 `transcriber.json` 并删除草稿中的 payload。
   本机完整构建时才用打包形式：`python tools/transcriber_release.py --use-local-key --version X.Y.Z --dist <onedir> --model-dir <模型目录> --model-revision <完整提交哈希> --output release-assets`。注意 PyInstaller 产物无法跨机器逐字节复现，因此不能用本机构建去为工作流产出的分卷签名。
4. 发布时**不得**将转录服务 Release 设为 latest：主程序的更新检查读取 releases/latest，只能看到主程序版本。注意网页发布草稿时「Set as the latest release」默认是勾选的，会覆盖工作流建草稿时的 `--latest=false`；1.0.0 发布时就发生过。发布后务必核对 `curl -s https://api.github.com/repos/<owner>/<repo>/releases/latest`（该接口有缓存，必要时加随机查询参数），指向应为主程序版本；若被覆盖，用 `gh release edit v<主程序版本> --latest` 恢复。
5. 主程序在 `subtitleflow/transcriber_install.py` 的 `TRANSCRIBER_TAG` 固定配套的服务标签。服务接口不兼容时递增 `transcriber.API_VERSION` 与主程序的 `TRANSCRIBER_API_VERSION`，并随新主程序修改 `TRANSCRIBER_TAG`；接口兼容时主程序升级不要求重新下载服务。

## 0.1.3 安全发布变更

构建依赖使用 requirements-bootstrap.lock 和 requirements-build.lock，执行 --require-hashes --only-binary=:all:。CI 产物附 dependency-audit.json、sbom.json 与 binary-inventory.json。禁止跳过安全检查来发布稳定版本。

`update.json` 由 `build.yml` 的 `draft-release` 作业签名生成；缺少密钥时作业失败，不会建出没有清单的草稿。CI 不可用时本机后备：

```shell
python tools/make_update_manifest.py --use-local-key --repo XMRayLabs/SubtitleFlow --version 0.1.3 --assets release-assets --output release-assets/update.json
```

release-assets 需包含实际安装包对应的 asset.json；发布前还应独立复核文件摘要与来源。签名清单与平台代码签名是不同要求，后者仍需配置证书。详见 SECURITY.md。

发布稳定版之前草稿里必须有签名的 update.json，并验证当前客户端 check(repo, current=旧版本) 能解析三个平台。没有清单的安装包草稿不可直接发布为稳定版。清单顶层保留旧客户端兼容字段，新客户端只信任签名 payload。

## CI 签名环境

最低配置（当前状态）：Settings → Secrets and variables → **Actions** → Repository secrets 添加 `SUBTITLEFLOW_UPDATE_SIGNING_KEY`。打标签即自动签名，但没有审批，风险见 SECURITY.md。

加固配置需要仓库管理员：

1. Environments → New environment，名称 `release`；在 `build.yml` 与 `transcriber.yml` 的 `draft-release` 作业加上 `environment: release`。
2. 该环境勾选 Required reviewers 并填发布负责人。多人维护时可勾 Prevent self-review（打标签的人不能自己批准）；单人维护时不要勾，否则无人能批准。
3. 该环境 Deployment branches and tags 选 Selected branches and tags，添加两条标签规则 `v*` 与 `transcriber-v*`。
4. 该环境 Environment secrets 添加 `SUBTITLEFLOW_UPDATE_SIGNING_KEY`，值为 64 位十六进制种子。key_id 默认取 `tools/signing.py` 的 `KEY_ID`，不需要另配变量。
5. 删除仓库级的同名 `SUBTITLEFLOW_UPDATE_SIGNING_KEY`，否则任何作业仍能读到，审批形同虚设。
6. Rules → Rulesets 新建标签规则集，目标 `v*` 与 `transcriber-v*`，限制创建、更新、删除，旁路名单只放发布负责人。

验证：打一个标签，草稿里应有 `update.json` 或 `transcriber.json`，没有 `transcriber-payload.json`。签错钥匙时作业报 `Signing key does not match embedded public key`。
