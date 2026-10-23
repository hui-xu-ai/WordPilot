<#
quality-gate.ps1 —— 机械质量闸门（把"工程规范"从提示词文本变成可执行断言）

为什么存在：2026-09-24 体检确认，pydev 预设里**唯一可执行的断言只有 memory-gate**，其余"验证三档 /
确定性闸门 / 目录三态 / 不要写巨文件 / 注意隐私"**全是提示词** —— 而提示词可以被忽略。
DeepSeek Harness 自己的设计笔记标题就是《Mechanical quality gates over prose guidelines》
（https://github.com/deepseek-ai/deepseek-harness/blob/master/.agents/notes/implemented/process/2026-06-11-quality-gates.md）。
本脚本就是那条原则在**项目侧**的落地：一条命令，出 pass/fail，不靠模型记得。

覆盖 7 类问题（编号与体检报告一致）：
  G0 提交边界与 Wiki 新鲜度 —— `proj push-check`（暂存区隐私/垃圾/密钥/大文件）+ `docs/wiki` 是否过期；缺 `tools\proj` 则跳过
  G1 隐私/秘密      —— 会被 push 的文件里是否有密钥、凭据、私钥；.gitignore 是否覆盖敏感路径
  G2 文件规模       —— 防"AI 写出单个巨文件"：超阈值列出最重的 N 个
  G3 目录规约       —— 生成物/运行产物是否落在被忽略的目录里（代码/测试/记忆/用户文档/输出/数据分离）
  G4 垃圾与生成物   —— 未跟踪文件的数量与体积（自测产生的垃圾在提交前就会被拦下）
  G5 测试与版本对应 —— 测试指向的模块是否还存在；改了源码却没动对应测试
  G6 git 卫生       —— 未提交规模、回滚锚点（tag/最近提交）是否存在
  G7 外部工具       —— 配置里的 ruff / pytest 等（存在才跑，缺失只 WARN）

用法：
  pwsh -NoProfile -File tools\quality-gate.ps1                    # 报告
  pwsh -NoProfile -File tools\quality-gate.ps1 -FailOnWarn        # WARN 也算失败（CI 用）
  pwsh -NoProfile -File tools\quality-gate.ps1 -Root <仓库根> -Config <配置>
退出码：0 = 无 FAIL（且未触发 -FailOnWarn 的 WARN）；1 = 有 FAIL。
#>
[CmdletBinding()]
param(
  [string]$Root = (Get-Location).Path,
  [string]$Config = '',
  [switch]$FailOnWarn,
  [switch]$SkipCommands
)

$ErrorActionPreference = 'Stop'
$script:fails = 0; $script:warns = 0

function Say([string]$t) { Write-Output ''; Write-Output ("=== " + $t + " ===") }
function Pass([string]$t) { Write-Output ("  [PASS] " + $t) }
function Info([string]$t) { Write-Output ("  [INFO] " + $t) }
function Warn([string]$t) { $script:warns++; Write-Output ("  [WARN] " + $t) }
function Fail([string]$t) { $script:fails++; Write-Output ("  [FAIL] " + $t) }

if (-not $Config) { $Config = Join-Path $Root 'tools\quality-gate.json' }
if (-not (Test-Path -LiteralPath $Config)) { Fail ("找不到配置：" + $Config); exit 1 }
$cfg = Get-Content -LiteralPath $Config -Raw -Encoding utf8 | ConvertFrom-Json

$rootFull = (Resolve-Path -LiteralPath $Root).Path
$excl = @($cfg.excludeDirs)
function IsExcluded([string]$rel) {
  foreach ($e in $excl) { if ($rel -like ($e + '\*') -or $rel -like ('*\' + $e + '\*') -or $rel -eq $e) { return $true } }
  return $false
}
function IsSource([string]$rel) {
  foreach ($g in $cfg.sourceGlobs) { if ($rel -like $g) { return $true } }
  return $false
}

Write-Output ("=== quality-gate： " + $rootFull + " ===")
Write-Output ("  配置：" + $Config)

# ── 扫描集 = **git 会跟踪的文件**（索引 + 未跟踪但未被忽略）──────────────────────
# ⚠️ 只用 `ls-files`（索引）会漏掉刚创建、还没 `git add` 的新文件 —— 而那正是提交前最该检查的东西。
#    2026-09-24 由会话级验收报告指出：第一次跑闸门时新代码全在 `??` 状态，等于**对新代码零检查**。
#    `--others --exclude-standard` 把"未被忽略的未跟踪文件"一并纳入；被忽略的（work/ 等）仍然不扫。
$tracked = @()
if (Test-Path -LiteralPath (Join-Path $rootFull '.git')) {
  $tracked = & git -C $rootFull ls-files --cached --others --exclude-standard 2>$null
  if ($LASTEXITCODE -ne 0) { $tracked = @() }
}
$tracked = @($tracked | Where-Object { $_ } | Sort-Object -Unique)
$porcelain = @()
if (Test-Path -LiteralPath (Join-Path $rootFull '.git')) { $porcelain = @(& git -C $rootFull status --porcelain 2>$null | Where-Object { $_ }) }

# ══ G0 提交边界与 Wiki 新鲜度（proj-core，缺工具则跳过） ══════════════════════
Say "G0 提交边界与 Wiki 新鲜度（proj-core）"
$projScript = Join-Path $rootFull 'tools\proj\proj.py'
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { Warn "PATH 里没有 python —— 跳过 push-check" }
elseif (-not (Test-Path -LiteralPath $projScript)) { Warn "未找到 tools\proj\proj.py —— 跳过 push-check（项目未从模板初始化）" }
else {
  $pushJson = Join-Path $env:TEMP ("proj-pushcheck-" + [guid]::NewGuid().ToString('n') + ".json")
  $pushOut = & python $projScript push-check $rootFull --mode staged --json $pushJson 2>&1 | Out-String
  $pushCode = $LASTEXITCODE
  if ($pushCode -eq 0) { Pass "push-check（暂存区）无硬问题" }
  else {
    $detail = @($pushOut -split "\r?\n" | Where-Object { $_ -match '\[FAIL\]' })
    if ($detail.Count -gt 0) { foreach ($line in $detail) { Fail ("push-check：" + $line.Trim()) } }
    else { Fail "push-check 退出码非 0（无明细）" }
  }
  if (Test-Path -LiteralPath $pushJson) { Remove-Item -LiteralPath $pushJson -Force }
}
# Wiki 新鲜度：docs/**/*.md 比 docs/wiki/index.json 新 ⇒ WARN（索引是派生物，不手改）
$wikiIndex = Join-Path $rootFull 'docs\wiki\index.json'
if (Test-Path -LiteralPath $wikiIndex) {
  $idxTime = (Get-Item -LiteralPath $wikiIndex).LastWriteTimeUtc
  $newer = @(Get-ChildItem -LiteralPath (Join-Path $rootFull 'docs') -Recurse -File -Filter '*.md' -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -notmatch '\\wiki\\' -and $_.LastWriteTimeUtc -gt $idxTime })
  if ($newer.Count -gt 0) { Warn ("Wiki 索引过期：" + $newer.Count + " 个 docs/*.md 比 index.json 新 ⇒ 跑 python tools\proj\proj.py wiki index .") }
  else { Pass "Wiki 索引新鲜（docs/*.md 未比 index.json 新）" }
} else { Info "无 docs\wiki\index.json —— 跳过新鲜度检查" }
# 依赖循环（proj graph）：WARN，不阻断——存量代码可能本来就有环，先可见再谈清理
if ($py -and (Test-Path -LiteralPath $projScript)) {
  $graphOut = & python $projScript graph $rootFull --fail-on-cycle 2>&1 | Out-String
  if ($LASTEXITCODE -eq 0) { Pass "依赖无循环（proj graph）" }
  else {
    $cycleLines = @($graphOut -split "\r?\n" | Where-Object { $_ -match '->' })
    Warn ("依赖存在循环 " + $cycleLines.Count + " 组 ⇒ 跑 python tools\proj\proj.py graph . 查看")
  }
}

# ══ G1 隐私/秘密 ════════════════════════════════════════════════════════════
Say "G1 隐私与秘密（会被 push 的内容）"
$secHits = @()
$evadeHits = @()
# 两级**行内豁免标记**：
#  ① `allowlist secret`（照抄 detect-secrets 约定）= 本行是**假密钥/示例**；
#  ② `gate: pattern-source`（本项目约定，v2.9.12 新增）= 本行是**检测器/脱敏器自身的模式定义**。
# 为什么要分两级（2026-09-24 第四轮会话级验收实测）：被测代码的模式定义与 `contentPatterns`
# **字面相同** ⇒ "老实写规则"会把闸门打成 FAIL，于是逼出"把密钥前缀拆成两个相邻字面量再拼"这种**规避写法**。
# 语义正确的修法是给模式定义一条**正路**，而不是让模型去拆字符串躲闸门。
$secMarker = if ($cfg.secrets.allowMarker) { [string]$cfg.secrets.allowMarker } else { 'allowlist secret' }
$srcMarker = if ($cfg.secrets.patternSourceMarker) { [string]$cfg.secrets.patternSourceMarker } else { 'gate: pattern-source' }
$prefixFrags = @($cfg.secrets.prefixFragments)
if ($prefixFrags.Count -eq 0) { $prefixFrags = @('sk-', 'ghp_', 'github_pat_', 'AKIA', 'glpat-') }
$litPairRe = [regex]"(['""])([^'""]*)\1\s*\+\s*(['""])([^'""]*)\3"
foreach ($rel in $tracked) {
  if (IsExcluded $rel) { continue }
  foreach ($fp in $cfg.secrets.filePatterns) { if ($rel -match $fp) { $secHits += ("文件名命中: " + $rel); break } }
}
$ignored = @()
foreach ($fp in $cfg.secrets.ignoreGlobs) { $ignored += $fp }
foreach ($rel in $tracked) {
  if (IsExcluded $rel) { continue }
  $skip = $false
  foreach ($ig in $ignored) { if ($rel -match $ig) { $skip = $true; break } }
  if ($skip) { continue }
  if (-not (IsSource $rel) -and $rel -notlike '*.json' -and $rel -notlike '*.yml' -and $rel -notlike '*.yaml' -and $rel -notlike '*.toml' -and $rel -notlike '*.ps1' -and $rel -notlike '*.md') { continue }
  $full = Join-Path $rootFull $rel
  $size = (Get-Item -LiteralPath $full -ErrorAction SilentlyContinue).Length
  if (-not $size -or $size -gt 2MB) { continue }
  $text = Get-Content -LiteralPath $full -Raw -Encoding utf8 -ErrorAction SilentlyContinue
  if (-not $text) { continue }
  # 逐行扫，并支持上面那两级行内豁免标记。
  $lineNo = 0
  foreach ($ln in ($text -split "\r?\n")) {
    $lineNo++
    if ($ln -match [regex]::Escape($secMarker)) { continue }
    if ($ln -match [regex]::Escape($srcMarker)) { continue }
    foreach ($cp in $cfg.secrets.contentPatterns) {
      if ($ln -match $cp) { $secHits += ("内容命中 [" + $cp + "]: " + $rel + ":" + $lineNo); break }
    }
    # **减速带**：本行内若出现 `"前缀前半" + "后半"` 这种**把密钥前缀拆成相邻字面量再拼**的写法，报 WARN。
    # 只抓这一种形态（拼出来的串 ≤16 字符且以某个前缀开头）——任何间接（变量中转 / 切片 / 编码）都能绕过
    # ⇒ 它是**减速带，不是墙**：真正的收益来自"给模式定义一条正路"（patternSourceMarker），本检查只提高规避成本。
    foreach ($m in $litPairRe.Matches($ln)) {
      $joined = $m.Groups[2].Value + $m.Groups[4].Value
      if ($joined.Length -gt 16) { continue }
      foreach ($pf in $prefixFrags) {
        if ($joined -match ('(?i)^' + [regex]::Escape([string]$pf))) {
          $evadeHits += ($rel + ":" + $lineNo + '（"' + $m.Groups[2].Value + '" + "' + $m.Groups[4].Value + '" ⇒ ' + $joined + "）"); break
        }
      }
    }
  }
}
if ($secHits.Count -gt 0) { foreach ($h in $secHits) { Fail ("疑似秘密/隐私：" + $h) } }
else { Pass "tracked 文件中未发现疑似密钥/凭据/私钥" }
if ($evadeHits.Count -gt 0) {
  foreach ($h in $evadeHits) { Warn ("疑似规避闸门：" + $h + " —— 请在**同一行**加语义正确的标记：**模式定义**用 `gate: pattern-source`；**假密钥/示例**用 `allowlist secret`；若是真密钥，立即移除") }
} else { Pass "未发现『拆字符串拼密钥前缀』的规避写法" }

$gi = ''
if (Test-Path -LiteralPath (Join-Path $rootFull '.gitignore')) { $gi = Get-Content -LiteralPath (Join-Path $rootFull '.gitignore') -Raw }
$giMiss = @()
foreach ($need in $cfg.ignoreMustContain) { if ($gi -notmatch [regex]::Escape($need)) { $giMiss += $need } }
if ($giMiss.Count -gt 0) { Warn (".gitignore 缺少敏感/生成物条目：" + ($giMiss -join ', ')) } else { Pass ".gitignore 覆盖了必须忽略的条目" }

# ══ G2 文件规模 ═════════════════════════════════════════════════════════════
Say "G2 文件规模（防『单个巨文件』）"
$big = @()
foreach ($rel in $tracked) {
  if (IsExcluded $rel) { continue }
  if (-not (IsSource $rel)) { continue }
  $full = Join-Path $rootFull $rel
  if (-not (Test-Path -LiteralPath $full)) { continue }
  $n = (Get-Content -LiteralPath $full -ErrorAction SilentlyContinue | Measure-Object).Count
  if ($n -ge [int]$cfg.maxFileLines.warn) { $big += [pscustomobject]@{ File = $rel; Lines = $n } }
}
$big = @($big | Sort-Object Lines -Descending)
foreach ($b in $big) {
  if ($b.Lines -ge [int]$cfg.maxFileLines.fail) { Fail ($b.File + "  " + $b.Lines + " 行 ≥ fail 阈值 " + $cfg.maxFileLines.fail + " —— 拆分（按职责切模块，不是按行数切）") }
  else { Warn ($b.File + "  " + $b.Lines + " 行 ≥ warn 阈值 " + $cfg.maxFileLines.warn) }
}
if ($big.Count -eq 0) { Pass ("所有源文件 < " + $cfg.maxFileLines.warn + " 行") }

# ══ G3 目录规约（生成物是否落在被忽略的目录） ═══════════════════════════════
Say "G3 目录规约（代码 / 测试 / 记忆 / 文档 / 输出 / 数据）"
$misplaced = @()
foreach ($rel in $tracked) {
  foreach ($gp in $cfg.layout.generatedDirPatterns) { if ($rel -match $gp) { $misplaced += $rel } }
}
if ($misplaced.Count -gt 0) { Fail ("生成物目录被 git 跟踪了（应忽略）：" + (($misplaced | Select-Object -First 6) -join ', ')) }
else { Pass "未见被跟踪的生成物目录" }
foreach ($d in $cfg.layout.expectDirs) {
  if (Test-Path -LiteralPath (Join-Path $rootFull $d)) { Pass ("目录存在：" + $d) } else { Warn ("缺少约定目录：" + $d) }
}

# ══ G4 垃圾与生成物 ═════════════════════════════════════════════════════════
Say "G4 未跟踪垃圾与体积"
$untracked = @($porcelain | Where-Object { $_ -match '^\?\?' } | ForEach-Object { $_.Substring(3).Trim('"') })
$junkCount = $untracked.Count
if ($junkCount -ge [int]$cfg.junk.untrackedFailCount) { Fail ("未跟踪条目 " + $junkCount + " ≥ fail 阈值 " + $cfg.junk.untrackedFailCount + " —— 先清理或补 .gitignore") }
elseif ($junkCount -ge [int]$cfg.junk.untrackedWarnCount) { Warn ("未跟踪条目 " + $junkCount + " ≥ warn 阈值 " + $cfg.junk.untrackedWarnCount) }
else { Pass ("未跟踪条目 " + $junkCount + " 个") }

$bigUntracked = @()
foreach ($u in $untracked) {
  $p = Join-Path $rootFull $u
  if (Test-Path -LiteralPath $p -PathType Container) { continue }
  $len = (Get-Item -LiteralPath $p -ErrorAction SilentlyContinue).Length
  if ($len -and $len -gt 5MB) { $bigUntracked += [pscustomobject]@{ File = $u; MB = [math]::Round($len / 1MB, 1) } }
}
foreach ($b in ($bigUntracked | Sort-Object MB -Descending | Select-Object -First 5)) {
  if ($b.MB -ge [double]$cfg.junk.largeUntrackedMB) { Fail ("未跟踪大文件 " + $b.File + "  " + $b.MB + " MB") }
  else { Warn ("未跟踪较大文件 " + $b.File + "  " + $b.MB + " MB") }
}
if ($bigUntracked.Count -eq 0) { Pass "无 >5MB 的未跟踪文件" }

# ══ G5 测试与版本对应 ═══════════════════════════════════════════════════════
Say "G5 测试与当前版本是否对应"
$testFiles = @($tracked | Where-Object {
    $rel = $_; $bn = Split-Path $_ -Leaf
    # ⚠️ 必须同时比 basename 与相对路径：`tests/<name>.py -like 'test_*.py'` 是 **false**（基名才匹配），
    #    只比路径会让整条 G5 **静默失效**（2026-09-24 实测：仓库里 2 个测试文件，G5 却说"没有测试文件"）。
    (@($cfg.tests.testGlobs) | Where-Object { $bn -like $_ -or $rel -like $_ }).Count -gt 0
  })
if ($testFiles.Count -eq 0) {
  # 防"死检查"：有 tests/ 目录却匹配到 0 个测试文件 ⇒ 一定是匹配逻辑坏了。**必须报出来**，
  # 因为"永远不运行的检查"会让人以为有防线——比没有检查更危险。
  if (Test-Path -LiteralPath (Join-Path $rootFull 'tests')) {
    Warn "存在 tests/ 目录却匹配到 0 个测试文件 —— G5 的 testGlobs 匹配逻辑失效（静默失效，比没有检查更危险）"
  } else {
    Info "仓库里没有测试文件"
  }
}
else {
  # 先把 searchRoots 下的模块/包名索引出来（文件去扩展名 + 目录名），用于"测试 → 模块"映射。
  # ⚠️ 为什么不能直接拿 base 去查文件：`tests/test_logfreq_core.py` 的 base 是 `logfreq_core`，
  #    而真实模块是 `src/logfreq/`（包）。精确匹配会**误报"陈旧测试"**，而误报会让整条闸门被无视。
  #    ⇒ 用**最长前缀命中**：把 base 按 `_` 逐步缩短，任一段命中索引即算映射成功。
  $moduleNames = @{}
  foreach ($r in $cfg.tests.searchRoots) {
    $dir = Join-Path $rootFull $r
    if (-not (Test-Path -LiteralPath $dir)) { continue }
    Get-ChildItem -LiteralPath $dir -Recurse -File -Include *.py, *.ts, *.js -ErrorAction SilentlyContinue | ForEach-Object {
      $rel = $_.FullName.Substring($rootFull.Length).TrimStart('\')
      if (IsExcluded $rel) { return }
      $moduleNames[[System.IO.Path]::GetFileNameWithoutExtension($_.Name)] = $true
    }
    Get-ChildItem -LiteralPath $dir -Recurse -Directory -ErrorAction SilentlyContinue | ForEach-Object {
      $rel = $_.FullName.Substring($rootFull.Length).TrimStart('\')
      if (IsExcluded $rel) { return }
      $moduleNames[$_.Name] = $true
    }
  }
  $stale = @()
  foreach ($t in $testFiles) {
    $base = [System.IO.Path]::GetFileNameWithoutExtension($t)
    $base = $base -replace '^(test_|tests_)', '' -replace '(_test|\.test|\.spec)$', ''
    if (-not $base) { continue }
    $segs = $base -split '_'
    $found = $false
    for ($i = $segs.Count; $i -ge 1; $i--) {
      $cand = ($segs[0..($i - 1)] -join '_')
      if ($moduleNames.ContainsKey($cand)) { $found = $true; break }
    }
    if (-not $found) { $stale += $t }
  }
  if ($stale.Count -gt 0) { Warn ("有 " + $stale.Count + " 个测试找不到对应模块（可能是旧版本残留，建议删或标 xfail）：" + (($stale | Select-Object -First 5) -join ', ')) }
  else { Pass ("全部 " + $testFiles.Count + " 个测试都能映射到现存模块") }

  $changed = @($porcelain | Where-Object { $_ -match '^\s?[MARD]' } | ForEach-Object { $_.Substring(3).Trim('"') })
  $changedSrc = @($changed | Where-Object { IsSource $_ })
  $changedTest = @($changed | Where-Object {
      $r = $_; $bn = Split-Path $_ -Leaf
      (@($cfg.tests.testGlobs) | Where-Object { $bn -like $_ -or $r -like $_ }).Count -gt 0
    })
  if ($changedSrc.Count -gt 0 -and $changedTest.Count -eq 0) { Warn ("改了 " + $changedSrc.Count + " 个源文件但没动任何测试 —— 确认是否有意为之") }
  elseif ($changedSrc.Count -gt 0) { Pass ("改动含源码 " + $changedSrc.Count + " / 测试 " + $changedTest.Count) }
}

# ══ G6 git 卫生与回滚锚点 ═══════════════════════════════════════════════════
Say "G6 git 卫生与回滚锚点"
if (-not (Test-Path -LiteralPath (Join-Path $rootFull '.git'))) { Info "不是 git 仓库，跳过" }
else {
  $dirty = @($porcelain).Count
  if ($dirty -ge [int]$cfg.git.dirtyFileWarn) { Warn ("未提交改动 " + $dirty + " 项 ≥ " + $cfg.git.dirtyFileWarn + " —— 大改动前先提交一个回滚锚点") }
  else { Pass ("未提交改动 " + $dirty + " 项") }
  $lastTag = & git -C $rootFull describe --tags --abbrev=0 2>$null
  $lastMsg = & git -C $rootFull log -1 --format='%h %s' 2>$null
  if ($lastTag) { Info ("最近 tag：" + $lastTag) } else { Info "没有 tag（大改动建议先打一个）" }
  if ($lastMsg) { Info ("最近提交：" + $lastMsg) }
}

# ══ G7 外部工具（存在才跑） ═════════════════════════════════════════════════
Say "G7 外部质量工具"
if ($SkipCommands) { Info "已用 -SkipCommands 跳过" }
else {
  foreach ($c in $cfg.commands) {
    if ($c.when -and -not (Test-Path -LiteralPath (Join-Path $rootFull $c.when))) { Info ($c.name + "：触发条件不存在（" + $c.when + "），跳过"); continue }
    $exe = ($c.cmd -split '\s+')[0]
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) {
      if ($c.optional) { Warn ($c.name + " 未安装，跳过（可选）") } else { Warn ($c.name + " 未安装，跳过") }
      continue
    }
    $out = & pwsh -NoProfile -Command ("Set-Location -LiteralPath '" + $rootFull + "'; " + $c.cmd) 2>&1 | Out-String
    $code = $LASTEXITCODE
    $tail = ($out -split "\r?\n" | Where-Object { $_.Trim() } | Select-Object -Last 3) -join ' | '
    if ($code -eq 0) { Pass ($c.name + " 通过") }
    elseif ($out -match 'PermissionError|WinError 5|Access is denied|拒绝访问') {
      # 沙箱 ACL 挡住子进程 tmpdir 时，工具本身没跑起来 ⇒ 记 N/A，不冒充 FAIL 也不冒充 PASS
      Info ($c.name + " 因沙箱 ACL 不可执行（N/A，不可判定）：" + $tail)
    }
    elseif ($c.severity -eq 'fail') { Fail ($c.name + " 失败（exit " + $code + "）：" + $tail) }
    else { Warn ($c.name + " 有问题（exit " + $code + "）：" + $tail) }
  }
}

# ══ 汇总 ════════════════════════════════════════════════════════════════════
Write-Output ''
Write-Output ("=== 汇总：FAIL=" + $script:fails + "  WARN=" + $script:warns + " ===")
if ($script:fails -gt 0) {
  Write-Output "处理：隐私类 FAIL 必须立刻删/轮换密钥并改 .gitignore；巨文件按职责拆分；生成物移入被忽略目录；测试陈旧项删掉或标 xfail。"
  exit 1
}
if ($FailOnWarn -and $script:warns -gt 0) { Write-Output "（-FailOnWarn：WARN 也按失败处理）"; exit 1 }
exit 0
