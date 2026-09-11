$ProgressPreference = 'SilentlyContinue'
$px = "http://127.0.0.1:7897"
$out = "D:\PYxiangmu\WKnx\workspace\capture\imgtest"
New-Item -ItemType Directory -Force -Path $out | Out-Null

$base = "https://sns-na-i2.xhscdn.com/notes_pre_post/1040g3k031v7ifpdn2q405pgl0ff0u9q19328mio"

# 用单引号整体包裹，避免 PowerShell 解析 | 和 &
$variants = [ordered]@{
  'A_orig_noparam'  = $base
  'B_fmt_png'       = "$base`?imageView2/2/format/png"
  'C_fmt_jpg_orig'  = "$base`?imageView2/2/format/jpg"
  'D_w1080_jpg'     = "$base`?imageView2/2/w/1080/format/jpg"
  'E_w0_jpg_q100'   = "$base`?imageView2/2/w/0/format/jpg/q/100"
  'F_mogr2'         = "$base`?imageMogr2/format/jpg"
  'G_onlyquality'   = "$base`?imageView2/2/q/100"
}

foreach ($k in $variants.Keys) {
  $u = $variants[$k]
  try {
    $r = Invoke-WebRequest -Uri $u -Proxy $px -TimeoutSec 40 -UseBasicParsing
    $bytes = $r.Content
    $f = Join-Path $out "$k.bin"
    [System.IO.File]::WriteAllBytes($f, $bytes)
    $ctype = $r.Headers['Content-Type']
    Write-Output ("{0,-18} status={1} bytes={2,9} ctype={3}" -f $k, $r.StatusCode, $bytes.Length, $ctype)
  } catch {
    $msg = $_.Exception.Message
    if ($msg.Length -gt 60) { $msg = $msg.Substring(0, 60) }
    Write-Output ("{0,-18} ERR {1}" -f $k, $msg)
  }
}
