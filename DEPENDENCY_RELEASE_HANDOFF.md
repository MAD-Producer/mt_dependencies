# 独立依赖发布实施交接

2026-10-03：用户授权直接修改该仓库源码、提交推送，并在推送约半小时后检查 Actions 结果。此文件描述已选择的实现，不代表远端构建或 OpenList 链路已经成功。

## 已确定的来源与契约

- 仓库：`MAD-Producer/mt_dependencies`；不改 MAD-Toolbox 源码或原 `/mt` 存储。
- 五个包：Deno、MediaInfo CLI、FFmpeg/ffprobe、BBDown、yt-dlp。平台为 Windows x64、macOS arm64；最终全部 ZIP。
- Windows FFmpeg 使用 Gyan Release Full 静态包；macOS 使用 Martin Riedl Release Apple Silicon 包，不取 Snapshot。
- Python/musicdl 不进入发布集合；不区分 Toolbox FULL/LITE。
- 正式 `version.json` 使用 README 中的 schema 1：`platforms[平台][工具]`，包含 `version`、`fileName`、`sha256`、`size`、`executables`。
- `executables` 是工具名到实际 ZIP 相对路径的映射；FFmpeg 必须同时声明 ffmpeg、ffprobe。
- 下载 URL 不写死进清单：Toolbox 用固定分享入口拼接 `fileName`。打包修复也通过最终 ZIP 的哈希/文件名识别，不要求改变上游版本。

## 已实现的流程

`.github/workflows/sync.yml` 分成版本解析、两个平台打包、完整发布三个阶段；实现函数集中在 `scripts/sync.py`，没有包管理框架或额外服务层。

- 推送源码触发首次运行；保留手动触发，每天 02:17 UTC / 上海时间 10:17 检查一次。
- 无变化不发布；其他工具变化时复用未变化 ZIP，每个新 Release 仍有完整十个 ZIP 和清单。
- 上游暂不可用且已有有效包时保留旧包并报告；首次缺包则不发布半成品。
- 创建新草稿、上传完整资产、核对上传，再公开设为 latest；不覆盖或先删除上一有效 Release。
- 仅发布 job 使用内置 `GITHUB_TOKEN` 的写权限，不使用 PAT，也不配置 OpenList 管理凭据。
- ZIP 文件名含内容哈希与打包修订，避免 CDN 同名不同内容；ZIP 内不放 Toolbox 本地 `installation.json`。

## 轻量校验边界

校验下载、上游已提供的 SHA-256、归档边界、必要程序、最终 ZIP 和上传完整性。没有上游哈希不一刀切拒绝；不运行下载视频、转码、编码器枚举或严格版本输出解析。

不实现 TUF、历史版本选择、回退或兼容性矩阵。许可证事项不设本轮自动审查 gate，但尽量保留上游附带材料，不能宣称已完成分发许可审核。

## 后续检查

1. 提交推送后确认首次 Actions 确实被触发，记录 run ID、提交 SHA 和失败步骤。
2. 约半小时后检查两个平台及发布 job 的真实结果。失败时根据日志修正并推送；仍运行时报告运行中，不能误报成功。
3. 成功时检查公开 latest Release 的十个 ZIP 与 `version.json`；向 Toolbox 任务提供真实清单，而非内部 `build-info`。
4. 用户配置 `/mt_dependencies` 的 OpenList 挂载和分享后，再检查实际清单及 ZIP 下载路由。GitHub 成功不等于 CDN 已验证。

具体字段、触发方式、来源和运维说明以本仓库 README 为当前真相源。

首次运行记录：版本解析及两个平台的真实打包均通过，十个 ZIP 与清单已上传到草稿。发布步骤按 tag 查询未公开草稿时返回 404；已改为按 Release ID 校验及公开。现有草稿需核对清单、资产大小与可用的服务端哈希后再公开，不能绕过上传完整性检查。
