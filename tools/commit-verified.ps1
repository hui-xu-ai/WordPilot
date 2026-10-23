<#
commit-verified.ps1 —— 让"提交前跑闸门"从**自觉**变成**机制**

为什么需要（2026-09-24 由第三轮会话级验收报告提出）：
`pydev-protocol §4.7` 与 `git-protocol` 都写了"提交前必须跑 `quality-gate.ps1` 且 FAIL=0"，
但这条防线**没有任何强制点**——跑没跑，在文件系统里**不留任何痕迹**。
一个会话完全可以在写完报告后直接 `git commit`，事后无人能分辨它到底跑没跑。
报告原话：**"预设里已有大量『提醒模型要做什么』的文本，缺的是『不做就会被拦住』的机制"**；
而提交恰恰是唯一会把错误**固化进历史**的动作。

本脚本把顺序钉死：**先闸门 → 再显式列路径 → 才提交**。绕过的成本因此高于遵守。

用法：
  pwsh -NoProfile -File tools\commit-verified.ps1 -Message "feat(x): 主题","验证：...","影响面：..." -Files src/x.py,tests/test_x.py
  pwsh -NoProfile -File tools\commit-verified.ps1 -Message "chore: ..." -Files tools/a.ps1 -SkipGate   # 仅在闸门不适用时才跳过（会打印警告）
退出码：0 = 已提交；1 = 闸门未过（**未动索引、未提交**）；2 = 用法/路径错误。
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string[]]$Message,
  [Parameter(Mandatory = $true)][string[]]$Files,
  [string]$Root = (Get-Location).Path,
  [string]$GateArgs = '',
  [switch]$SkipGate
)

$ErrorActionPreference = 'Stop'
$rootFull = (Resolve-Path -LiteralPath $Root).Path
$gate = Join-Path $rootFull 'tools\quality-gate.ps1'

# ── 1. 闸门（不可绕过）───────────────────────────────────────────────────────
if ($SkipGate) {
  Write-Output "  [WARN] -SkipGate：本次跳过了质量闸门。请在提交信息里写明为什么不需要它。"
} elseif (-not (Test-Path -LiteralPath $gate)) {
  Write-Output ("  [FAIL] 找不到 " + $gate + " —— 本仓库未从模板初始化？先说明再决定是否 -SkipGate。")
  exit 2
} else {
  Write-Output "=== 提交前闸门 ==="
  $gateCmd = @('-NoProfile', '-File', $gate, '-Root', $rootFull)
  if ($GateArgs) { $gateCmd += ($GateArgs -split '\s+') }
  $gateOut = & pwsh @gateCmd 2>&1 | Out-String
  $gateCode = $LASTEXITCODE
  $tail = ($gateOut -split "\r?\n" | Where-Object { $_ -match '\[FAIL\]|汇总' }) -join "`n"
  if ($gateCode -ne 0) {
    Write-Output $tail
    Write-Output "  [拒绝提交] 闸门 FAIL>0。**索引未改动、未提交** —— 先修掉再跑本脚本。"
    exit 1
  }
  Write-Output (($tail -split "\r?\n" | Select-Object -Last 1).Trim())
}

# ── 2. 只 add 显式列出的路径（永不 git add -A）───────────────────────────────
Write-Output "=== 显式列路径 ==="
foreach ($f in $Files) {
  foreach ($one in ($f -split ',')) {
    $p = $one.Trim()
    if (-not $p) { continue }
    $full = Join-Path $rootFull $p
    if (-not (Test-Path -LiteralPath $full)) {
      Write-Output ("  [FAIL] 路径不存在：" + $p + " —— 提交信息里写了什么就必须真的存在。")
      exit 2
    }
    & git -C $rootFull add -- $p
    if ($LASTEXITCODE -ne 0) { Write-Output ("  [FAIL] git add 失败：" + $p); exit 2 }
    Write-Output ("  + " + $p)
  }
}
$staged = @(& git -C $rootFull diff --cached --name-only)
if ($staged.Count -eq 0) { Write-Output "  [WARN] 暂存区为空——这些路径可能已是最新。无内容可提交。"; exit 0 }
Write-Output ("  暂存 " + $staged.Count + " 个文件：")
foreach ($s in $staged) { Write-Output ("    " + $s) }

# ── 3. 提交（多段 -m）───────────────────────────────────────────────────────
Write-Output "=== 提交 ==="
$gitArgs = @('-C', $rootFull, 'commit')
foreach ($m in $Message) { $gitArgs += @('-m', $m) }
& git @gitArgs | Out-String | ForEach-Object { $_.Trim() } | Where-Object { $_ } | ForEach-Object { Write-Output ("  " + $_) }
if ($LASTEXITCODE -ne 0) { Write-Output "  [FAIL] git commit 失败"; exit 2 }
$hash = (& git -C $rootFull log -1 --format='%h %s')
Write-Output ("  已提交：" + $hash)
Write-Output "  （提醒：记忆仓库 .dsh-memory 是独立仓库，需要时在那里另行提交）"
exit 0
