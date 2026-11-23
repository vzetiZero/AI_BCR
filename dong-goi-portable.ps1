# -*- coding: utf-8 -*-
# Dong goi AI_BCR thanh ban PORTABLE: may moi giai nen la chay, KHONG can cai
# Python, Node.js, npm hay Chrome.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\dong-goi-portable.ps1
#
# Ket qua: dist\AI_BCR_Portable.zip
#
# Bo cuc ben trong goi:
#   AI_BCR_Portable\
#   |- aibcr\                 backend Python (MVC)
#   |- ui\                    Electron + node_modules da cai san
#   |- runtime\python\        Python 3.12 embeddable + thu vien (selenium, websocket-client...)
#   |- runtime\chrome\        Chrome portable + chromedriver cung phien ban
#   |- Start.bat              mo app
#   |- HUONG_DAN.txt          huong dan cho may moi
#
# Khong dua vao goi: .git, chrome_profile* (cookie dang nhap), aibcr_state*.json,
# lich su ca nhan, __pycache__, build cu.

param(
    [string]$OutDir = "",
    [string]$ChromeVersion = "157.0.8103.79",
    [string]$PythonVersion = "3.12.10",
    [switch]$SkipZip
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $OutDir) { $OutDir = Join-Path $root 'dist' }
$buildDir = Join-Path $root 'build'
$stage = Join-Path $buildDir 'AI_BCR_Portable'
$cache = Join-Path $env:TEMP 'aibcr-portable-cache'

function Say([string]$text, [string]$color = 'Gray') {
    Write-Host $text -ForegroundColor $color
}

function Step([string]$text) {
    Write-Host ""
    Say "== $text" 'Cyan'
}

function Fail([string]$text) {
    Say "[LOI] $text" 'Red'
    exit 1
}

function Get-File([string]$url, [string]$dest) {
    if (Test-Path $dest) {
        $size = (Get-Item $dest).Length
        if ($size -gt 1MB) {
            Say "      dung cache: $(Split-Path $dest -Leaf) ($([math]::Round($size / 1MB)) MB)" 'DarkGray'
            return
        }
    }
    Say "      tai: $url" 'DarkGray'
    $tmp = "$dest.part"
    if (Test-Path $tmp) { Remove-Item -LiteralPath $tmp -Force }
    Invoke-WebRequest -Uri $url -OutFile $tmp -UseBasicParsing
    Move-Item -LiteralPath $tmp -Destination $dest -Force
}

New-Item -ItemType Directory -Force -Path $cache | Out-Null

# --- 0. Kiem tra dieu kien tren may dong goi ------------------------------
Step '0/7 Kiem tra moi truong dong goi'

$electronExe = Join-Path $root 'ui\node_modules\electron\dist\electron.exe'
if (-not (Test-Path $electronExe)) {
    Fail "Chua co ui\node_modules\electron\dist\electron.exe. Chay install-toan-bo.bat truoc."
}
Say "      Electron: $((Get-Content (Join-Path (Split-Path $electronExe -Parent) 'version') -Raw).Trim())" 'DarkGray'

$hostPython = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $hostPython) { Fail 'Khong tim thay python tren may dong goi (can de tai thu vien).' }
$hostPyVersion = & python -c "import sys; print('%d.%d' % sys.version_info[:2])"
if ($hostPyVersion -ne '3.12') {
    Say "      [CANH BAO] May dang dung Python $hostPyVersion; goi portable dung Python $PythonVersion." 'DarkYellow'
}
Say "      Python may dong goi: $hostPyVersion" 'DarkGray'

# --- 1. Chep ma nguon -----------------------------------------------------
Step '1/7 Chep ma nguon (bo .git, profile Chrome, du lieu ca nhan)'

if (Test-Path $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
New-Item -ItemType Directory -Force -Path $stage | Out-Null

$excludeDirs = @(
    (Join-Path $root '.git'),
    (Join-Path $root 'ui\node_modules'),
    (Join-Path $root 'build'),
    (Join-Path $root 'dist'),
    (Join-Path $root 'runtime'),
    (Join-Path $root 'aibcr\chrome_profile'),
    (Join-Path $root 'aibcr\drivers'),
    (Join-Path $root 'aibcr\__pycache__')
)
$excludeFiles = @(
    'aibcr_state*.json', 'aibcr-session-*.json', 'aibcr_history.jsonl',
    'aibcr-history-*.*', 'live_reports.jsonl', 'aibcr_trace.jsonl',
    'aibcr_ws_frames*.*', '*.pyc', '*.bak'
)

$robocopyArgs = @($root, $stage, '/E', '/NFL', '/NDL', '/NJH', '/NJS', '/NP')
if ($excludeDirs.Count -gt 0) { $robocopyArgs += '/XD'; $robocopyArgs += $excludeDirs }
if ($excludeFiles.Count -gt 0) { $robocopyArgs += '/XF'; $robocopyArgs += $excludeFiles }
& robocopy @robocopyArgs | Out-Null
if ($LASTEXITCODE -ge 8) { Fail "robocopy loi (ma $LASTEXITCODE)." }

# node_modules cua Electron (khong co thi khong mo duoc app).
Say '      chep ui\node_modules ...' 'DarkGray'
& robocopy (Join-Path $root 'ui\node_modules') (Join-Path $stage 'ui\node_modules') /E /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { Fail "robocopy node_modules loi (ma $LASTEXITCODE)." }

# Khong mang cache/rac cua Electron sang may khac.
foreach ($junk in @('ui\node_modules\.cache', 'ui\node_modules\.bin\*.ps1')) {
    $p = Join-Path $stage $junk
    if (Test-Path $p) { Remove-Item -LiteralPath $p -Recurse -Force -ErrorAction SilentlyContinue }
}

$srcCount = (Get-ChildItem $stage -Recurse -File | Measure-Object).Count
Say "      $srcCount file ma nguon + UI" 'DarkGray'

# --- 2. Python embeddable -------------------------------------------------
Step "2/7 Python $PythonVersion (embeddable) + thu vien"

$pyZip = Join-Path $cache "python-$PythonVersion-embed-amd64.zip"
Get-File "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip" $pyZip

$pyDir = Join-Path $stage 'runtime\python'
New-Item -ItemType Directory -Force -Path $pyDir | Out-Null
Expand-Archive -LiteralPath $pyZip -DestinationPath $pyDir -Force

$pyExe = Join-Path $pyDir 'python.exe'
if (-not (Test-Path $pyExe)) { Fail "Khong giai nen duoc python.exe vao $pyDir" }

# _pth: them thu muc app goc va site-packages de `python -m aibcr` chay duoc o bat ky o dia nao.
$minor = ($PythonVersion -split '\.')[1]
$pthFile = Join-Path $pyDir "python3$minor._pth"
if (-not (Test-Path $pthFile)) { Fail "Khong thay $pthFile" }
@(
    "python3$minor.zip",
    '.',
    'Lib\site-packages',
    '..\..'
) | Set-Content -LiteralPath $pthFile -Encoding ASCII
Say "      _pth: python3$minor.zip + . + Lib\site-packages + ..\.." 'DarkGray'

Say '      cai selenium + websocket-client vao goi ...' 'DarkGray'
$sitePkgs = Join-Path $pyDir 'Lib\site-packages'
New-Item -ItemType Directory -Force -Path $sitePkgs | Out-Null
& python -m pip install --disable-pip-version-check --no-warn-script-location `
    --target $sitePkgs selenium websocket-client 2>&1 | ForEach-Object { Say "      $_" 'DarkGray' }
if ($LASTEXITCODE -ne 0) { Fail 'pip install vao goi portable that bai.' }
Remove-Item -LiteralPath (Join-Path $pyDir 'Lib\site-packages\bin') -Recurse -Force -ErrorAction SilentlyContinue

& python -c "import sys; sys.path.insert(0, r'$sitePkgs'); import selenium, websocket; print('selenium', selenium.__version__, '| websocket', websocket.__version__)"
if ($LASTEXITCODE -ne 0) { Fail 'Thu vien trong goi khong import duoc.' }

# --- 3. Chrome + chromedriver --------------------------------------------
Step "3/7 Chrome $ChromeVersion + chromedriver (portable)"

$chromeZip = Join-Path $cache "chrome-win64-$ChromeVersion.zip"
Get-File "https://storage.googleapis.com/chrome-for-testing-public/$ChromeVersion/win64/chrome-win64.zip" $chromeZip
$chromeDir = Join-Path $stage 'runtime\chrome'
New-Item -ItemType Directory -Force -Path $chromeDir | Out-Null
Expand-Archive -LiteralPath $chromeZip -DestinationPath $chromeDir -Force
$chromeSub = Join-Path $chromeDir 'chrome-win64'
if (-not (Test-Path (Join-Path $chromeSub 'chrome.exe'))) { Fail "Khong thay chrome.exe trong $chromeSub" }

$driverZip = Join-Path $cache "chromedriver-win64-$ChromeVersion.zip"
Get-File "https://storage.googleapis.com/chrome-for-testing-public/$ChromeVersion/win64/chromedriver-win64.zip" $driverZip
Expand-Archive -LiteralPath $driverZip -DestinationPath $chromeDir -Force
$driverSub = Join-Path $chromeDir 'chromedriver-win64'
if (-not (Test-Path (Join-Path $driverSub 'chromedriver.exe'))) { Fail "Khong thay chromedriver.exe trong $driverSub" }

Say "      Chrome: $((Get-Item (Join-Path $chromeSub 'chrome.exe')).VersionInfo.ProductVersion)" 'DarkGray'

# --- 4. Huong dan cho may moi --------------------------------------------
Step '4/7 Tao HUONG_DAN.txt'

$guide = @"
AI_BCR - BAN DONG GOI SAN SANG CHAY (portable)
==============================================

May moi KHONG can cai Python, Node.js, npm hay Chrome.
Chi can Windows 10/11 64-bit.

CACH CHAY
---------
1. Giai nen file AI_BCR_Portable.zip ra mot thu muc bat ky, vi du:
      D:\AI_BCR
   (Tranh thu muc he thong nhu C:\Program Files - app can ghi file trang thai
    va profile Chrome ngay trong thu muc cua no.)
2. Nhap doi Start.bat  ->  giao dien mo len.
3. Bam "Vao ban hop le", dang nhap sanh Bang nhu binh thuong.

LAN DAU MO
----------
- Windows Defender / SmartScreen co the hoi: chon "More info" -> "Run anyway",
  hoac chuot phai Start.bat -> Properties -> Unblock. Ly do: file .bat/.exe moi
  tai ve thuong bi danh dau.
- O lan dau, Chrome cua app mo ra voi profile trong va yeu cau dang nhap lai.
  Thong tin dang nhap luu trong aibcr\chrome_profile (chi nam tren may nay,
  khong dua vao goi).

CO GI TRONG GOI
---------------
runtime\python\    Python 3.12 + selenium + websocket-client (khong can cai)
runtime\chrome\    Chrome portable + chromedriver cung phien ban
ui\                giao dien Electron + node_modules da cai san
aibcr\             ma nguon backend

GHI CHU
-------
- File du lieu ca nhan (aibcr_state_baccarat.json, aibcr_history.jsonl,
  chrome_profile\) duoc tao moi tren may moi. Neu muon giu so lieu cu, chep
  tay cac file do tu may cu sang.
- Muon dung Chrome cai san tren may thay vi Chrome trong goi: doi ten hoac xoa
  runtime\chrome\chrome.exe, app se tim Chrome trong he thong.
- Cap nhat phien ban: thay ca thu muc bang ban moi, giu lai
  aibcr\chrome_profile va aibcr_state_baccarat.json.

Dong goi ngay: $([DateTime]::Now.ToString('yyyy-MM-dd HH:mm'))
Python $PythonVersion (embeddable) | Chrome $ChromeVersion | Electron + node_modules
"@
$guide | Set-Content -LiteralPath (Join-Path $stage 'HUONG_DAN.txt') -Encoding UTF8

# --- 5. Kiem tra goi ------------------------------------------------------
Step '5/7 Kiem tra goi (import module bang dung Python trong goi)'

Push-Location $stage
try {
    & $pyExe -c "import aibcr, aibcr.service.engine, aibcr.controllers.baccarat, aibcr.models.live_sim, aibcr.adapters.browser as BR; print('module OK'); print('chrome  =', BR.chrome_path()); print('driver  =', BR.DRIVER_PATH, BR.driver_version()); print('driver_for =', BR.driver_for()[0])"
    if ($LASTEXITCODE -ne 0) { Fail 'Goi portable khong import duoc module aibcr.' }

    & $pyExe -m aibcr.service.engine --help | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail 'Goi portable khong chay duoc engine (-m aibcr.service.engine).' }
} finally {
    Pop-Location
}

$bundleMB = [math]::Round(((Get-ChildItem $stage -Recurse -File | Measure-Object Length -Sum).Sum / 1MB), 1)
Say "      tong dung luong: $bundleMB MB" 'DarkGray'

# --- 6. Nen ZIP -----------------------------------------------------------
if ($SkipZip) {
    Say ''
    Say "[XONG] Da dung thu muc: $stage (bo qua nen ZIP)" 'Green'
    exit 0
}

Step '6/7 Nen ZIP (co the mat vai phut)'

$zipName = 'AI_BCR_Portable.zip'
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$zipPath = Join-Path $OutDir $zipName
if (Test-Path $zipPath) { Remove-Item -LiteralPath $zipPath -Force }

# tar (bsdtar co san trong Windows 10/11) nen nhanh hon Compress-Archive nhieu.
& tar.exe -a -c -f $zipPath -C $buildDir 'AI_BCR_Portable'
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $zipPath)) {
    Say '      tar loi, chuyen sang Compress-Archive ...' 'DarkYellow'
    Compress-Archive -Path $stage -DestinationPath $zipPath -CompressionLevel Optimal -Force
}

$zipMB = [math]::Round((Get-Item $zipPath).Length / 1MB, 1)
Say "      $zipPath ($zipMB MB)" 'DarkGray'

# --- 7. Xong --------------------------------------------------------------
Step '7/7 Hoan tat'
Say ""
Say "  Da tao: $zipPath" 'Green'
Say "  Dung luong: $zipMB MB (giai nen ~$bundleMB MB)" 'Gray'
Say ""
Say "  Chep file ZIP sang may khac, giai nen, bam Start.bat." 'Gray'
Say "  Chi tiet trong HUONG_DAN.txt ben trong goi." 'Gray'
Say ""
