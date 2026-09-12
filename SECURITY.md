# 安全说明

0.1.3 修复 S1、S3、S4、S5、S6。按产品要求保留远程 HTTP API 支持（S2）。

- 恢复任务不回退到报告中的外部 source，拒绝符号链接及 Windows 重解析点；不自动更换 API 地址和模型。写入使用随机临时文件并原子替换。
- 构建 pip 固定 26.2.1，依赖按 SHA-256 锁定，CI 查询 OSV；查询失败或已知漏洞匹配会阻止构建。
- 更新清单必须通过内置 Ed25519 公钥验证，下载校验 SHA-256，安装前再次校验；HTTPS 重定向禁止降级。
- JSON 上限 8 MiB，更新清单上限 1 MiB，SRT 上限 32 MiB、100,000 条、单条 1,000,000 字符；单任务最多 10,000 文件，模型最多 5,000 项。更新包上限 512 MiB。
- JSON 读取总期限 60 秒，更新下载总期限 600 秒；使用分段 read1 检查截止时间，单次阻塞还受 socket timeout 限制，因此截止退出可能延后至当前读超时。取消在分段读取间生效。
- CI Actions 固定提交 SHA；源码归档在解析前匹配 source-lock.json；输出组件 SBOM 和原生文件摘要清单。

## 更新签名

安装包内置 release-v2 公钥。私钥保存在 GitHub 仓库 `release` 环境的 Secret `SUBTITLEFLOW_UPDATE_SIGNING_KEY`（32 字节种子十六进制），不依赖某一位发布者的本机，发布者更替或本机丢失都不会丢钥匙。不要将私钥写入仓库、日志或 issue。已有发布用户时不可随意更换公钥。key_id 与凭据账户定义在 tools/signing.py 顶部，轮换时只改那里。

tools/provision_update_key.py 只用于**首次**创建密钥：系统凭据库里没有钥匙时它会生成新钥匙并覆盖 subtitleflow/update_trust.py 的公钥，等同轮换。不要用它导入已有私钥。

0.2.0 轮换记录：release-v1 私钥已不可用，改用 release-v2。客户端只信任自己安装包内嵌的公钥，因此 0.1.6 及更早版本无法验证新清单——它们不会提示有新版本，只会在状态栏显示「后台更新检查未成功」。这些用户必须通过应用外的渠道通知其手动重新安装。轮换会造成这种断裂，非必要不得重复。

默认由 CI 签名：

- 只有 `build.yml` 与 `transcriber.yml` 的 `draft-release` 作业（仅由 `v*` / `transcriber-v*` 标签触发）引用私钥；装 torch、跑 PyInstaller 的构建作业与拉取请求作业不引用。来自复刻仓库的拉取请求拿不到仓库 Secret。
- 签名作业签转录服务清单前按 payload 复算每个分卷的 SHA-256；缺少密钥时作业失败（`--require-key`），不产出无签名草稿。
- **当前私钥是仓库级 Actions Secret，签名作业没有审批。** 这意味着任何有本仓库写权限的人都能在自己的分支上改工作流读出私钥。写权限只授予可信的发布者。仓库管理员可按 RELEASE.md「CI 签名环境」补上审批环境与标签规则并把 Secret 移入环境。
- 私钥只能放在 Settings → Secrets and variables → **Actions**，不要放进 Agents（Copilot 云代理）或 Codespaces，那里所有协作者都能让代理读到它。

怀疑泄露只能轮换钥匙，见上方轮换记录的代价。

本机签名保留为后备（例如 CI 不可用）：`--use-local-key` 从系统凭据库读取，或临时设置 `SUBTITLEFLOW_UPDATE_SIGNING_KEY` 环境变量后运行，用完即清除。

这不是 Windows Authenticode 或 Apple Developer ID 签名。平台签名、公证需要相应证书，当前未配置。没有有效签名清单时客户端会阻止自动更新。

## 边界

本地文件校验防止预置链接和常见报告攻击，不承诺抵御具有同一用户写权限的恶意进程并发替换路径；该进程本就能修改应用与用户数据。下载后复核缩小篡改窗口，不等于操作系统级可信执行。

OSV 扫描覆盖 Python 包；SBOM 是 Python 包及主要运行时清单，不是全部原生传递依赖的完整审计。Qt、Python、OpenSSL 等原生组件仍需结合最终三平台产物和厂商公告进行发布验收。签名库及其捆绑原生依赖同样适用。

构建 hash lock 的包摘要来自官方 PyPI 元数据；Qt 源码摘要已与官方 .sha256 对比，Python 源码摘要固定自已有审核材料，更新时需再次核验上游。锁定防止之后漂移，不证明上游从未被入侵。
