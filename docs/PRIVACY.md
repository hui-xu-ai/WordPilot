# 隐私与数据安全 · PRIVACY

> 面向使用者与贡献者：说明 WordPilot 会把什么数据写在本机、什么数据会发给模型服务、
> 以及仓库里**绝不会**出现哪些文件。

## 1. 一句话原则

**所有用户数据都留在本机 `config/` 目录；唯一外发的数据是"你要 AI 处理的那段文字"，
发往你自己配置的 DeepSeek API 端点。仓库里只有代码、资源与配置模板。**

## 2. 本机数据清单（运行时生成，不入库）

| 文件 / 目录 | 内容 | 敏感度 | 处置 |
| --- | --- | --- | --- |
| `config/.key` | Fernet 加密主密钥（用于加密 API Key） | 🔴 极高 | gitignore；不要拷贝给他人 |
| `config/MWindow_config.json` | API Key、模型、提示词、个人偏好 | 🔴 高 | gitignore；仓库只放 `*.example.json` |
| `config/navigator_config.json` | 题录导航个人偏好 | 🟡 中 | gitignore |
| `config/api_calls.log` | 每次调用的**输入/输出文本片段**（各截 200 字）+ token 统计 | 🔴 高 | gitignore；可在「设置 → API 监控」清空 |
| `config/app.log` / `errors.log` | 运行日志（可能含文档路径） | 🟡 中 | gitignore；可在「设置 → 存储」清理 |
| `config/proof_cache.db*` | 校对结果缓存（含原句与结果） | 🟡 中 | gitignore |
| `config/word_caption_cache.db*` | 题注缓存（含文档路径与题注文字） | 🟡 中 | gitignore |
| `config/ignore_db.json` | 忽略句子库 | 🟡 中 | gitignore |
| `config/plugins/<插件>/` | **各插件用户资产**：提示词版本、个人设置 | 🔴 高 | gitignore（整个目录归插件所有，程序不会当垃圾清理） |
| `config/references/` | 参考文献库 CSV 与派生索引 | 🔴 高 | gitignore |
| `config/Image.docx` | 图片无损裁剪的临时工作文档（可能残留你的图片） | 🟡 中 | gitignore；可在「存储」清理 |
| `config/user.qss` | 用户自定义样式 | ⚪ 低 | gitignore |

打包分发时同样遵守：`dist/` 里只有 `*.example.json`，真实配置由程序首次运行时生成
（详见 [`BUILD.md`](BUILD.md) 的「分发前自检清单」）。

## 3. 会外发什么

- **只发待处理文本**：纠错送当前句子、翻译/改写送选中或剪贴板文本、整段纠错送整段内容；
- 同时发送：系统提示词、提示词模板、语言与强度参数；
- 目标地址 = 你在设置里填的 `api_endpoint`（默认 `https://api.deepseek.com`）；
- **不发送**：文件路径、文档全文、参考文献库、历史调用日志；
- API Key 以 `Authorization: Bearer` 头传输，落盘前用 Fernet（`.key`）加密。

## 4. 仓库侧的隔离机制

1. `.gitignore` 覆盖全部运行时隐私文件（`.key`、`MWindow_config.json`、`api_calls.log`、
   `*.db*`、`config/plugins/`、`config/references/`、`user.qss`、`Image.docx`、日志等）；
2. 仓库只保留 `src/config/MWindow_config.example.json`、`navigator_config.example.json`（`api_key` 为空）；
3. `tools/quality-gate.ps1` 的 G1 规则会扫描源码里的疑似密钥（`sk-*`、`*.pem`、`credentials*`）；
4. 历史记录已核对：这些文件**从未**被提交（`git log --all -- src/config/MWindow_config.json` 为空）。

## 5. 贡献者须知

- 提交前跑一遍：`pwsh -File tools/quality-gate.ps1`（G1 = 隐私扫描）；
- 不要在测试、文档、示例配置里写真实 Key / 真实论文片段；
- 新增会写文件的插件，请把用户数据放在 `ctx.data_dir`（= `config/plugins/<插件id>/`），
  该目录由内核声明为"永不当垃圾清理"，并已在 `.gitignore` 覆盖范围之内；
- 新增运行时文件请同步更新 `.gitignore` 与本文件的清单。

## 6. 用户自查

- 想彻底清空：关闭程序 → 删除 `config/` 目录（会同时删除 API Key 与你保存的提示词版本）；
- 只想清日志：设置 → 「API 监控」→「清空日志」；设置 → 「存储」可清理日志/缓存/回收站；
- 换机器：**不要拷贝 `config/`**（含密钥），在新机器上重新填 Key 即可。
