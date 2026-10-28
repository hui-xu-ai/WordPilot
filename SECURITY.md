# 安全策略（Security Policy）

WordPilot 是**纯本地**桌面应用：除"你要 AI 处理的那段文字"发送到**你自己配置的 API 端点**外，
不存在任何遥测、上报或云端同步。

## 支持版本

| 版本 | 安全更新 |
| --- | --- |
| 3.2.x | ✅ |
| 3.1.x | ❌（请升级） |

## 报告漏洞

请**不要**开公开 Issue。用 GitHub 的
[Private vulnerability reporting](https://docs.github.com/zh/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
（仓库 Security 标签页 → Report a vulnerability），或直接邮件联系维护者。
请在报告里包含：影响版本、复现步骤、影响面（是否能读到本机文件 / 是否能触发外部请求）。

一般 3 个工作日内确认，7 个工作日内给出修复或缓解方案。

## 不要提交的内容

以下属于**用户隐私数据**，仓库与发布包都不应包含，也不要在 Issue 里粘贴：

- `config/.key`（Fernet 主密钥，泄露等于 API Key 可被解密）
- `config/MWindow_config.json`（含 API Key、模型、个人偏好）
- `config/api_calls.log`、`config/*.log`（含被处理的正文片段）
- `config/proof_cache.db*` / `word_caption_cache.db*`（含原句与文档路径）
- `config/plugins/<插件>/`（用户提示词版本等资产）、`config/references/`（文献库 CSV）
- `config/Image.docx`、`config/user.qss`

要求：提交前跑 `pwsh -NoProfile -File tools/quality-gate.ps1`（内含隐私检查）。

## 已知的安全边界

- API Key 以 Fernet 加密保存于 `config/`，主密钥在同目录 `.key`；
  能读到该目录的攻击者可解密。这是本地桌面应用的固有限制，请确保 Windows 账户与磁盘安全。
- 插件以**同进程 Python 代码**运行，安装第三方插件等于信任其代码，请只安装可信来源的插件。
- 软件不校验 API 端点的证书例外：请只填写可信的 HTTPS 端点。
