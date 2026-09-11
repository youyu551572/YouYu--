# start_web_api.ps1 — 零模拟器启动小红书搜索 API（Web 引擎）
#
# 与旧版 start_api.ps1 的区别：完全不需要雷电模拟器 / adb / mitmproxy。
# 前置条件只有两项：Python 依赖、一次扫码登录。

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

Write-Host "================================================================" -ForegroundColor Cyan
Write-Host " 小红书搜索/原图 API  (Web 引擎 / 无需模拟器)" -ForegroundColor Cyan
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host ""

$ok = 0
$fail = 0

function Check($name, [scriptblock]$test) {
    Write-Host ("  [{0}] {1} ... " -f "  ", $name) -NoNewline
    try {
        $r = & $test
        if ($r) {
            Write-Host "OK  $r" -ForegroundColor Green
            $script:ok++
        } else {
            Write-Host "FAIL" -ForegroundColor Red
            $script:fail++
        }
    } catch {
        Write-Host "FAIL  $($_.Exception.Message)" -ForegroundColor Red
        $script:fail++
    }
}

# 1. Python
Check "Python 可用" {
    $v = & python --version 2>&1
    if ($LASTEXITCODE -eq 0) { "$v" } else { $false }
}

# 2. 依赖
Check "依赖 (flask/requests/playwright)" {
    $r = & python -c "import flask, requests, playwright; print('flask', flask.__version__)" 2>&1
    if ($LASTEXITCODE -eq 0) { "$r" } else { $false }
}

# 3. Chrome
Check "Chrome 浏览器" {
    $paths = @(
        "C:\Program Files\Google\Chrome\Application\chrome.exe",
        "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
    )
    $found = $paths | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($found) { Split-Path $found -Leaf } else { $false }
}

# 4. 登录态
Write-Host "  [  ] 登录态检查（启动浏览器，约 10 秒）..." -NoNewline
$st = & python -c @"
import sys, json
sys.path.insert(0, r'$here')
try:
    from xhs_web import XhsWeb
    s = XhsWeb(headless=True).status()
    print(json.dumps(s, ensure_ascii=False))
except Exception as e:
    print(json.dumps({'logged_in': False, 'error': str(e)[:200]}))
"@ 2>&1
try {
    $j = $st | ConvertFrom-Json
    if ($j.logged_in) {
        Write-Host "OK  user_id=$($j.user_id)" -ForegroundColor Green
        $ok++
    } else {
        Write-Host "未登录" -ForegroundColor Yellow
        $fail++
    }
} catch {
    Write-Host "解析失败" -ForegroundColor Red
    $fail++
}

# 5. 端口
Check "端口 8700 空闲" {
    $busy = Get-NetTCPConnection -LocalPort 8700 -State Listen -ErrorAction SilentlyContinue
    if ($busy) { $false } else { "free" }
}

Write-Host ""
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host " 预检结果: $ok 项通过, $fail 项待处理" -ForegroundColor $(if ($fail -eq 0) { "Green" } else { "Yellow" })
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host ""

if ($fail -gt 0) {
    Write-Host "提示:" -ForegroundColor Yellow
    Write-Host "  * 登录态未就绪时，先执行:  python login_live.py 900"
    Write-Host "    （会输出 ASCII 二维码，用小红书 App 扫一次即可，cookie 会持久保存）"
    Write-Host ""
}

Write-Host "正在启动 API  (http://127.0.0.1:8700)" -ForegroundColor Cyan
Write-Host "  自述文档: http://127.0.0.1:8700/docs"
Write-Host "  健康检查: http://127.0.0.1:8700/health"
Write-Host "  按 Ctrl+C 停止"
Write-Host ""

$env:PYTHONIOENCODING = "utf-8"
$env:XHS_ENGINE = "web"
python xhs_api.py --host 0.0.0.0 --port 8700
