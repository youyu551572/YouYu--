# start_api.ps1 — 小红书搜索/原图 API 一键启动
#
# 用法:
#   .\start_api.ps1                    # 默认 0.0.0.0:8700
#   .\start_api.ps1 -Port 8800
#   .\start_api.ps1 -Background        # 后台启动

param(
    [string]$HostAddr = "0.0.0.0",
    [int]$Port = 8700,
    [string]$Adb = "C:\Users\YouYu\Desktop\platform-tools\adb.exe",
    [string]$Device = "emulator-5554",
    [switch]$Background
)

$ErrorActionPreference = "Continue"
$ApiDir = "D:\PYxiangmu\WKnx\workspace\api"

function Ok($m)   { Write-Host "  [OK]   $m" -ForegroundColor Green }
function Warn($m) { Write-Host "  [WARN] $m" -ForegroundColor Yellow }
function Bad($m)  { Write-Host "  [FAIL] $m" -ForegroundColor Red }

Write-Host ""
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host " 小红书搜索/原图 API  启动前环境检查" -ForegroundColor Cyan
Write-Host "======================================================" -ForegroundColor Cyan

# 1. adb
if (Test-Path $Adb) { Ok "adb: $Adb" } else { Bad "adb 不存在: $Adb"; exit 1 }

# 2. 设备
$state = & $Adb -s $Device get-state 2>&1
if ($state -match "device") { Ok "设备在线: $Device" }
else { Bad "设备不可用: $Device ($state)"; exit 1 }

# 3. 抓包器端口
$mitm = Test-NetConnection -ComputerName 127.0.0.1 -Port 8080 -InformationLevel Quiet -WarningAction SilentlyContinue
if ($mitm) { Ok "mitmdump 已在 8080 运行" } else { Warn "8080 未监听，API 启动时会自动拉起" }

# 4. reverse 隧道
$rev = & $Adb -s $Device reverse --list 2>&1
if ($rev -match "tcp:8080") { Ok "adb reverse 隧道已建立" }
else {
    Warn "隧道不存在，正在建立"
    & $Adb -s $Device reverse tcp:8080 tcp:8080 | Out-Null
    Ok "隧道已建立: tcp:8080"
}

# 5. 设备代理
$proxy = (& $Adb -s $Device shell "settings get global http_proxy").Trim()
if ($proxy -eq "127.0.0.1:8080") { Ok "设备全局代理: $proxy" }
else {
    Warn "设备代理为 '$proxy'，设置为 127.0.0.1:8080"
    & $Adb -s $Device shell "settings put global http_proxy 127.0.0.1:8080" | Out-Null
    Ok "设备代理已设置"
}

# 6. 系统 CA（证书注入是否仍有效）
$ca = & $Adb -s $Device shell "ls /system/etc/security/cacerts/ 2>/dev/null | grep -c c8750f0d" 2>&1
if ($ca -match "1") { Ok "mitmproxy CA 已在系统信任区" }
else { Warn "系统信任区未见 CA（重启后需重跑 install_ca.sh），HTTPS 抓包可能失败" }

# 7. 端口占用
if (Test-NetConnection -ComputerName 127.0.0.1 -Port $Port -InformationLevel Quiet -WarningAction SilentlyContinue) {
    Bad "端口 $Port 已被占用，API 可能已在运行"
    Write-Host ""
    Write-Host "  现有服务响应: " -NoNewline
    try {
        $info = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/" -TimeoutSec 8
        Write-Host ("$($info.service) v$($info.version)") -ForegroundColor Green
    } catch { Write-Host "（非本服务）" -ForegroundColor Yellow }
    exit 0
}

Write-Host ""
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host " 启动 API" -ForegroundColor Cyan
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host "  监听: http://${HostAddr}:$Port"
Write-Host "  文档: http://127.0.0.1:$Port/docs"
Write-Host ""

Set-Location $ApiDir
$env:PYTHONIOENCODING = "utf-8"

if ($Background) {
    Start-Process -FilePath "python" -ArgumentList "xhs_api.py --host $HostAddr --port $Port" `
        -WorkingDirectory $ApiDir -WindowStyle Hidden
    Write-Host "  已后台启动，等待就绪..." -ForegroundColor Yellow
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 1
        if (Test-NetConnection -ComputerName 127.0.0.1 -Port $Port -InformationLevel Quiet -WarningAction SilentlyContinue) {
            Ok "服务已就绪: http://127.0.0.1:$Port"
            try {
                $h = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 20
                Write-Host "  健康: mitm=$($h.mitm_running) tunnel=$($h.tunnel) app=$($h.app_alive)"
            } catch {}
            exit 0
        }
    }
    Bad "启动超时"
    exit 1
} else {
    python xhs_api.py --host $HostAddr --port $Port
}
