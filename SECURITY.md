# 安全说明

0.1.3 修复 S1、S3、S4、S5、S6。按产品要求保留远程 HTTP API 支持（S2）。

- 恢复任务不回退到报告中的外部 source，拒绝符号链接及 Windows 重解析点；不自动更换 API 地址和模型。写入使用随机临时文件并原子替换。
- 构建 pip 固定 26.2.1，依赖按 SHA-256 锁定，CI 查询 OSV；查询失败或已知漏洞匹配会阻止构建。
- 更新清单必须通过内置 Ed25519 公钥验证，下载校验 SHA-256，安装前再次校验；HTTPS 重定向禁止降级。
- JSON 上限 8 MiB，更新清单上限 1 MiB，SRT 上限 32 MiB、100,000 条、单条 1,000,000 字符；单任务最多 10,000 文件，模型最多 5,000 项。更新包上限 512 MiB。
- JSON 读取总期限 60 秒，更新下载总期限 600 秒；使用分段 read1 检查截止时间，单次阻塞还受 socket timeout 限制，因此截止退出可能延后至当前读超时。取消在分段读取间生效。
- CI Actions 固定提交 SHA；源码归档在解析前匹配 source-lock.json；输出组件 SBOM 和原生文件摘要清单。

## 更新签名

安装包内置 release-v1 公钥，私钥仅保存在发布者系统凭据库 SubtitleFlow-release / ed25519-v1。不要将私钥写入仓库、日志或 issue。工具 tools/provision_update_key.py 可在首次设置时创建系统凭据与公钥文件；已有发布用户时不可随意更换公钥。

默认采用本机签名：下载并核验 CI 安装包及 asset.json 后运行 tools/make_update_manifest.py --use-local-key。CI 未配置私钥时只生成安装包草稿，不生成 unsigned update.json。必须把合法签名清单加入发布草稿再发布。

若启用 CI 签名，需要单独保护签名作业：配置 SUBTITLEFLOW_UPDATE_SIGNING_KEY Secret（32 字节种子十六进制）与 SUBTITLEFLOW_UPDATE_KEY_ID=release-v1，并使用审批环境、保护分支/标签。拥有源码和 CI 修改权仍可能获取 CI 密钥，因此本机独立签名为默认方案。

这不是 Windows Authenticode 或 Apple Developer ID 签名。平台签名、公证需要相应证书，当前未配置。没有有效签名清单时客户端会阻止自动更新。

## 边界

本地文件校验防止预置链接和常见报告攻击，不承诺抵御具有同一用户写权限的恶意进程并发替换路径；该进程本就能修改应用与用户数据。下载后复核缩小篡改窗口，不等于操作系统级可信执行。

OSV 扫描覆盖 Python 包；SBOM 是 Python 包及主要运行时清单，不是全部原生传递依赖的完整审计。Qt、Python、OpenSSL 等原生组件仍需结合最终三平台产物和厂商公告进行发布验收。签名库及其捆绑原生依赖同样适用。

构建 hash lock 的包摘要来自官方 PyPI 元数据；Qt 源码摘要已与官方 .sha256 对比，Python 源码摘要固定自已有审核材料，更新时需再次核验上游。锁定防止之后漂移，不证明上游从未被入侵。
