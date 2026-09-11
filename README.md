# WKnx — Android 逆向工作台

把 MCP 服务与 skill 组成的安卓逆向能力装进项目目录，DSH 热加载后即可直接调用。

- MCP：2 个服务、42 个工具，走 stdio，已在 DSH web profile 注册并运行
- Skills：5 个，放在项目级 `.dsh/skills/`，DSH 自动发现
- CLI：jadx / apktool / frida 已装好，供 skill 脚本与手工操作使用
- 样本：`samples/ApiDemos-debug.apk`，全链路验证用

---

## 快速开始

```powershell
# 1) 工具进 PATH（skill 脚本与 CLI 都依赖这一步）
$env:PATH = "D:\PYxiangmu\WKnx\tools\bin;" + $env:PATH

# 2) 自检：两个 MCP 服务是否可握手
cd D:\PYxiangmu\WKnx
node tools\verify-mcp.mjs

# 3) 端到端自检：用真实 APK 跑通两个 MCP
node tools\smoke-e2e.mjs
```

MCP 工具已注册到 DSH，模型侧名字形如 `mcp__jadx-headless__load_apk`、
`mcp__apktool__decode_apk`。

---

## MCP 服务

| 服务 | 版本 | 工具数 | 职责 |
|---|---|---|---|
| `jadx-headless` | 0.7.0 | 26 | APK/DEX/JAR 静态分析：反编译、smali、xrefs、检索、Manifest、资源 |
| `apktool` | 3.0.3 | 16 | 解包 / 重打包、smali 与资源读写、工程内全文检索 |

`jadx-headless` 基于 jadx-core，**不需要 JADX GUI**，也没有 Python 适配层：一个 JVM 进程直接讲 MCP。

实测（`samples/ApiDemos-debug.apk`）：

```
load_apk              2371ms   class_count=2876  resource_count=869
get_app_info            33ms   io.appium.android.apis 6.0.17 (43) minSdk=26 targetSdk=33
get_main_activity_class  2ms   io.appium.android.apis.ApiDemos
search_classes_by_keyword 8485ms  WebView1/2/3 …
decode_apk            4899ms   13 个 dex → smali，项目 52MB
list_smali_directories  33ms   smali: 4118 个文件
```

## Skill

| Skill | 来源 | 用途 |
|---|---|---|
| `android-reverse-engineering` | SimoneAvogadro | jadx / Fernflower 反编译、接口提取、调用链追踪，含 Windows 脚本 |
| `apk-reverse` | haikow | 解包、反编译、smali 改写、重打包、Frida Hook 的 CLI 作业规范 |
| `ai-mobile-reverse-skills` | Fausto-404 | 中文 6 阶段总控流程：侦察 → 流量对齐 → JNI/SO → 风险 → 验证 → 报告 |
| `reverse-engineering` | haikow | 通用逆向方法论知识库（二进制、脱壳、反调试、自定义 VM） |
| `open-apk` | jadx-headless-mcp | 把样本装进 jadx-headless MCP 的标准动作 |

skill 里的 `${CLAUDE_PLUGIN_ROOT}` 已替换成本项目绝对路径；工具清单与版本号已按本机实测校正。

## CLI 工具链

| 工具 | 版本 | 位置 |
|---|---|---|
| jadx | 1.5.6 | `tools/jadx/`（包装脚本 `tools/bin/jadx.cmd`） |
| apktool | 3.0.3 | `tools/apktool/apktool_3.0.3.jar`（包装脚本 `tools/bin/apktool.cmd`） |
| frida | 16.5.9 | Python Scripts 目录 |
| java | 17.0.12 | 系统 PATH |

未安装（用到再装，别假设存在）：`adb`、`ida`、`radare2`、`fernflower/vineflower`、`dex2jar`、`apkeep`。

---

## 目录结构

```
D:\PYxiangmu\WKnx\
├─ .dsh\skills\            5 个 skill（DSH 项目级发现路径）
├─ tools\
│  ├─ bin\                 jadx.cmd / apktool.cmd 包装脚本
│  ├─ jadx\                jadx CLI 1.5.6
│  ├─ apktool\             apktool 3.0.3 jar
│  ├─ mcp\jadx-headless\   MCP 服务 jar
│  ├─ mcp\apktool-mcp-server\  MCP 服务源码
│  ├─ venv\                apktool MCP 的 Python 环境（fastmcp）
│  ├─ verify-mcp.mjs       MCP 握手自检
│  ├─ smoke-e2e.mjs        真实 APK 端到端自检
│  └─ diag-mcp.mjs         单服务 stdio 原始流量诊断
├─ samples\                样本 APK
└─ workspace\              解包 / 反编译产物
```

## DSH 注册与回滚

MCP 注册写在 profile patch：

```
C:\Users\YouYu\.dsh\profiles\web\cordis.patch.yml
```

每行 `@deepseek-ai/dsh-mcp-client` 就是一个 stdio MCP 服务，`serverName` 决定
`mcp__<serverName>__<tool>` 前缀。

- `patchReload: live`，**改完无需重启**，DSH 会热加载并拉起服务
- apktool 需要 `apktool` 在 PATH，因此该行的 `env.PATH` 显式追加了 `tools\bin`
- 回滚：同目录下有 `cordis.patch.yml.bak.<时间戳>`，复制回去即可
- 排查：观察 `C:\Users\YouYu\.dsh\desktop.log` 里对应服务的启动日志

## 环境适配与已修缺陷

本机 shell 是 **Windows PowerShell 5.1**（`C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe`），
机器上没有 PowerShell 7。上游 skill 脚本默认按 PS 7 写，直接装进来跑不起来，已修正：

- 4 个脚本的 `#requires -Version 7` 放宽为 `5.1`
  （`decode` / `frida-run` / `manifest-summary` / `rebuild-sign-install`）
- 3 处原生命令捕获（`check-deps.ps1`、`install-dep.ps1`、`decompile.ps1`）：
  PS 5.1 在 `$ErrorActionPreference='Stop'` 下会把原生命令的 stderr 当成终止性错误，
  而 `java -version` 恰好把版本号写 stderr，脚本一开头就会崩。改为在捕获点临时降级 EAP。
- 占位符路径 `C:\Users\YOURNAME\...` 换成本项目真实路径（jadx / apktool / debug.keystore）
- skill 中的 `${CLAUDE_PLUGIN_ROOT}` 全部替换为项目绝对路径（残留 0 处）
- `ai-mobile-reverse-skills/docs/MCP-INTEGRATION.md` 顶部补了本机 MCP 名对照表，
  原文用的 `jadx-mcp` / `ida-mcp` / `ghidra-mcp` 在本机并不存在

### 脚本实测

```
check-deps.ps1          exit=0   Java 17 / jadx 1.5.6 / apktool 3.0.3 全部识别
manifest-summary.ps1     ok      331 activities, 14 permissions
decode.ps1               ok      24s → 2860 个 java 文件 + 13 个 smali 目录
```

`jadx_exit_code=1` 属于 jadx 正常行为（2671 个类中 88 个反编译失败就返回非零），
源码照样可用，脚本已自带对应 warning。

## 网络说明

`github.com`、`f-droid.org` 直连不通，必须走本地代理 `http://127.0.0.1:7897`：

```powershell
Invoke-WebRequest -Uri <url> -OutFile <path> -Proxy http://127.0.0.1:7897
git -c http.proxy=http://127.0.0.1:7897 clone <repo>
```

不带代理时 git/curl 会静默挂到超时。

## 典型用法

```powershell
# 静态分析：直接让模型调 MCP，或走 CLI
jadx -d workspace\apidemos-src samples\ApiDemos-debug.apk
apktool d samples\ApiDemos-debug.apk -o workspace\apidemos

# 重打包（改完 smali 后）
apktool b workspace\apidemos -o workspace\apidemos-patched.apk
```

动态分析（Frida）需要设备或模拟器：`adb` 未安装，且当前无 `frida-server` 目标，
使用前先补 platform-tools 并在设备上跑 frida-server。

---

## 小红书搜索 / 原图 API

`workspace/api/` 下是一套可直接被其他项目 HTTP 调用的服务：
关键词搜索小红书、并按原始分辨率下载图片。

**核心结论：不需要模拟器。** 移动端接口的 `shield` 签名逐请求绑定
（实测换一组 `search_id` 立刻 406），但 Web 端 `x-s`/`x-t` 由页面 JS 生成，
让浏览器自己签、我们只拦 XHR 即可。代价只是一次扫码登录，cookie 长期有效。

```powershell
cd workspace\api
python login_live.py 900   # 扫码一次（ASCII 二维码，90 秒自动刷新）
.\start_web_api.ps1        # 启动，默认 http://127.0.0.1:8700
```

```bash
curl -X POST http://127.0.0.1:8700/v1/search \
  -H "Content-Type: application/json" \
  -d '{"keyword":"手冲咖啡","pages":1,"mode":"png"}'
```

**原图的关键坑**：Web 返回的图片链接尾部挂着 `!nc_n_webp_mw_1`，
它把资源钉死在 webp 转码上，加任何 `imageView2` 参数都无效（恒为 52516 字节）。
剥离后缀并换到原图 CDN 才能取回原始分辨率：

```
https://sns-na-i1.xhscdn.com/{fileid}?imageView2/2/format/png
```

实测同一张图：缩略图 52 KB → **PNG 2160×2880 / 3.2 MB**。

**cookie 失效后重新扫码**（搜索会返回 HTTP 401 并给出指引）：

```bash
curl -X POST http://127.0.0.1:8700/v1/relogin -d '{}'   # 拿二维码
curl http://127.0.0.1:8700/v1/relogin/status            # 查进度
```

浏览 `http://127.0.0.1:8700/docs` 看完整端点，或读
[README-WEB.md](workspace/api/README-WEB.md)（含全部实测证据与踩坑记录）。

环境变量 `XHS_ENGINE=device` 可切回旧的设备引擎（需雷电模拟器 + adb + mitmproxy，但免登录）。
