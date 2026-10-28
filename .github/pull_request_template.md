## 这个 PR 做了什么

<!-- 一两句话说明改动目的 -->

## 现场 / 原因

<!-- 用户看到的是什么问题？根因在哪一行？为什么这样修？ -->

## 验证（勾选并写清命令与数字）

- [ ] `python -m pytest -q -p no:cacheprovider` → ___ passed, ___ skipped
- [ ] `pwsh -NoProfile -File tools/quality-gate.ps1` → FAIL=___ / WARN=___
- [ ] `python tools/version.py --check`（改了版本号或 CHANGELOG 时）
- [ ] 涉及界面：新控件已实现 `retranslateUi()`，并跑过
      `tests/test_i18n_roundtrip_contract.py`（中 → 英 → 中 往返一致）

## 自查清单

- [ ] 没有提交用户隐私数据（`.key` / `MWindow_config.json` / `api_calls.log` / `*.db*` /
      `config/plugins/` / `config/references/`）
- [ ] 没有把 API Key、内网地址写进代码或测试
- [ ] 界面文案进了语言包（中英 key 集合一致，英文无中文）
- [ ] 分层没被打破（`app/` 不碰 COM/API/缓存，`core/` 无 Qt）
- [ ] 提交信息说明了「为什么」，而不只是「改了什么」
