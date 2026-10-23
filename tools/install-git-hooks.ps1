<#
install-git-hooks.ps1 —— 安装 git 原生钩子，把"提交前跑质量闸门"从**自觉**变成**机制**

为什么需要（2026-09-24 由第三轮会话级验收报告提出）：
`pydev-protocol §4.7` 与 `git-protocol` 都写了"提交前必须跑 quality-gate 且 FAIL=0"，
但这条防线**没有任何强制点**——跑没跑在文件系统里不留痕迹。报告原话：
**"预设里已有大量『提醒模型要做什么』的文本，缺的是『不做就会被拦住』的机制"**，
而**提交恰恰是唯一会把错误固化进历史的动作**。

为什么用 git 原生钩子而不是 DSH 的 hooks 插件：
DSH 确实有 `dsh-hooks-claude-code`（`tools/pre-execute` 能返回 `deny`，且 `${CLAUDE_PLUGIN_ROOT}` 替换可用，已取证），
但它要**新增预设挂载面**、且只能等重启后才知道成不成。本脚本选择**立刻可验证**的方案：
git 钩子对**人和 agent 一视同仁**、不依赖 DSH、不需要改预设。两条路不冲突，后者可作后续增强。

用法：
  pwsh -NoProfile -File tools\install-git-hooks.ps1              # 安装（幂等；已存在且内容相同则跳过）
  pwsh -NoProfile -File tools\install-git-hooks.ps1 -Uninstall   # 移除本脚本装的钩子
  pwsh -NoProfile -File tools\install-git-hooks.ps1 -Force       # 覆盖已存在的（非本脚本装的）pre-commit
说明：`.git/hooks/` **不入版本控制**，所以每克隆一次要装一次——`tools/setup.ps1` 会调用本脚本。
绕过：`git commit --no-verify`（git 始终提供这个口子）——**绕过的成本应当高于遵守**，请在提交信息里写明理由。
#>
[CmdletBinding()]
param(
  [string]$Root = (Get-Location).Path,
  [switch]$Uninstall,
  [switch]$Force
)

$ErrorActionPreference = 'Stop'
$rootFull = (Resolve-Path -LiteralPath $Root).Path
$gitDir = Join-Path $rootFull '.git'
if (-not (Test-Path -LiteralPath $gitDir)) { Write-Output ("  不是 git 仓库：" + $rootFull); exit 2 }
$hooksDir = Join-Path $gitDir 'hooks'
$hookPath = Join-Path $hooksDir 'pre-commit'
$pushPath = Join-Path $hooksDir 'pre-push'
$marker = '# >>> pydev quality-gate hook >>>'
$pushMarker = '# >>> pydev push-gate hook >>>'

if ($Uninstall) {
  foreach ($pair in @(@($hookPath, $marker, 'pre-commit'), @($pushPath, $pushMarker, 'pre-push'))) {
    $p, $m, $name = $pair
    if (Test-Path -LiteralPath $p) {
      $cur = Get-Content -LiteralPath $p -Raw
      if ($cur -like ("*" + $m + "*")) {
        Remove-Item -LiteralPath $p -Force
        Write-Output ("  [OK] 已移除本脚本安装的 " + $name + " 钩子")
      } else {
        Write-Output ("  [SKIP] " + $name + " 存在但不含本脚本标记，未移除（避免删掉别人的钩子）")
      }
    } else { Write-Output ("  [SKIP] 没有 " + $name + " 钩子") }
  }
  exit 0
}

# ⚠️ 必须写成 **LF**：Windows 上 CRLF 的 sh 脚本会让 git 报 "cannot execute" / 语法错。
#    这是本脚本唯一真正容易踩的坑，所以用 .NET 直接写字节，不走 Set-Content。
$lf = "`n"
$lines = @(
  '#!/bin/sh'
  $marker
  '# 提交前闸门：quality-gate 不过就拒绝提交。绕过用 git commit --no-verify（请写明理由）。'
  'ROOT=$(git rev-parse --show-toplevel) || exit 0'
  'GATE="$ROOT/tools/quality-gate.ps1"'
  'if [ ! -f "$GATE" ]; then'
  '  echo "[pre-commit] 未找到 tools/quality-gate.ps1，跳过闸门（本仓库未从模板初始化）"'
  '  exit 0'
  'fi'
  'if command -v pwsh >/dev/null 2>&1; then'
  '  pwsh -NoProfile -File "$GATE" -Root "$ROOT" || {'
  '    printf "\n[pre-commit] 质量闸门未通过 —— 提交已被拒绝（未产生提交）。\n"'
  '    printf "  修掉上面的 FAIL 后重试；确需绕过：git commit --no-verify（请在提交信息里写明理由）。\n"'
  '    exit 1'
  '  }'
  'else'
  '  echo "[pre-commit] 找不到 pwsh，跳过闸门（请装 PowerShell 7）"'
  'fi'
  'exit 0'
  '# <<< pydev quality-gate hook <<<'
)
$content = ($lines -join $lf) + $lf

# pre-push 闸门：真实推送前跑 `proj push-check --mode push`（覆盖本次要推的提交）
$pushLines = @(
  '#!/bin/sh'
  $pushMarker
  '# 推送前闸门：proj push-check --mode push 不过就拒绝推送。绕过用 git push --no-verify（请写明理由）。'
  'ROOT=$(git rev-parse --show-toplevel) || exit 0'
  'PROJ="$ROOT/tools/proj/proj.py"'
  'if [ ! -f "$PROJ" ]; then'
  '  echo "[pre-push] 未找到 tools/proj/proj.py，跳过推送闸门（本仓库未从模板初始化）"'
  '  exit 0'
  'fi'
  'PY=$(command -v python || command -v python3 || true)'
  'if [ -z "$PY" ]; then'
  '  echo "[pre-push] 找不到 python，跳过推送闸门"'
  '  exit 0'
  'fi'
  '"$PY" "$PROJ" push-check "$ROOT" --mode push || {'
  '  printf "\n[pre-push] 推送闸门未通过 —— 推送已被拒绝（远端未更新）。\n"'
  '  printf "  修掉上面的 FAIL 后重试；确需绕过：git push --no-verify（请在提交信息里写明理由）。\n"'
  '  exit 1'
  '}'
  'exit 0'
  '# <<< pydev push-gate hook <<<'
)
$pushContent = ($pushLines -join $lf) + $lf

if (-not (Test-Path -LiteralPath $hooksDir)) { New-Item -ItemType Directory -Force $hooksDir | Out-Null }
$writeCommit = $true
if ((Test-Path -LiteralPath $hookPath) -and -not $Force) {
  $cur = Get-Content -LiteralPath $hookPath -Raw
  if ($cur -like ("*" + $marker + "*")) { $writeCommit = $false; Write-Output "  [OK] pre-commit 已是本脚本安装（幂等跳过）" }
  else { $writeCommit = $false; Write-Output "  [WARN] 已存在非本脚本的 pre-commit 钩子，未覆盖（pre-push 仍会安装）；要覆盖用 -Force。" }
}

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
if ($writeCommit) {
  [System.IO.File]::WriteAllText($hookPath, $content, $utf8NoBom)
}
# git for windows 会忽略权限位，但显式设置无害
try { & git -C $rootFull update-index --chmod=+x -- '.git/hooks/pre-commit' 2>$null | Out-Null } catch { }

if ($writeCommit) {
  $bytes = [System.IO.File]::ReadAllBytes($hookPath)
  $crlf = 0
  for ($i = 0; $i -lt $bytes.Length - 1; $i++) { if ($bytes[$i] -eq 13 -and $bytes[$i + 1] -eq 10) { $crlf++ } }
  Write-Output ("  [OK] 已安装 pre-commit 钩子：" + $hookPath)
  Write-Output ("       验证：CRLF 数量 = " + $crlf + "（必须为 0，否则 sh 会报错）")
}

$writePush = $true
if ((Test-Path -LiteralPath $pushPath) -and -not $Force) {
  $curPush = Get-Content -LiteralPath $pushPath -Raw
  if ($curPush -like ("*" + $pushMarker + "*")) { $writePush = $false; Write-Output "  [OK] pre-push 已是本脚本安装（幂等跳过）" }
  else { $writePush = $false; Write-Output "  [WARN] 已存在非本脚本的 pre-push 钩子，未覆盖；要覆盖用 -Force。" }
}
if ($writePush) {
  [System.IO.File]::WriteAllText($pushPath, $pushContent, $utf8NoBom)
  $pbytes = [System.IO.File]::ReadAllBytes($pushPath)
  $pcrlf = 0
  for ($i = 0; $i -lt $pbytes.Length - 1; $i++) { if ($pbytes[$i] -eq 13 -and $pbytes[$i + 1] -eq 10) { $pcrlf++ } }
  Write-Output ("  [OK] 已安装 pre-push 钩子：" + $pushPath)
  Write-Output ("       验证：CRLF 数量 = " + $pcrlf + "（必须为 0）")
}

Write-Output "  提示：.git/hooks/ 不入版本控制，每克隆一次需装一次（tools/setup.ps1 会调用本脚本）。"
exit 0
