# 小红书搜索 / 原图 API — Web 引擎方案

**不需要模拟器。** 其他项目直接调 HTTP 就能搜索并下载小红书原图。

---

## 为什么不再需要模拟器

旧方案要常驻雷电模拟器 + adb + mitmproxy，因为移动端接口的 `shield`
签名由 native so 生成，且**对每次请求内容签名**（实测换一组
`search_id`/`session_id` 立刻返 406），无法复用静态签名。

Web 端是另一套体系：

| | 移动端接口 | Web 端接口 |
|---|---|---|
| 签名 | `shield`（native so，逐请求） | `x-s` / `x-t` / `x-s-common`（页面 JS 生成） |
| 我们的做法 | 必须设备常驻或逆向 so | **让浏览器自己签，我们只取数据** |

实测证据（`replay_probe.py`）：剥离 `shield` → HTTP 406；
剥离 `xy-common-params`/`xy-platform-info` → 406；
换新 `search_id` → 406。签名与请求内容绑定。

而用真实浏览器打开页面时，`racing_get` / `login/activate` /
`qrcode/create` 全部 `code=0 成功` —— **签名自动通过**。

---

## 前置条件（只有两个）

1. Python 依赖：`flask`、`requests`、`playwright`（本机已有）
2. **一次扫码登录** —— 之后 cookie 落在
   `chrome-profile-xhs\` 目录，长期复用

不需要：雷电模拟器、adb、mitmproxy、系统 CA、deeplink 触发。

---

## 快速开始

### 1. 扫码登录（只做一次）

```powershell
cd D:\PYxiangmu\WKnx\workspace\api
python login_live.py 900
```

终端会打印 ASCII 二维码（同时存为 `web_qrcode.png`），
用小红书 App 扫一下。二维码每 90 秒自动刷新，不用担心过期。

登录成功后 `login_ok.json` 会写入 `user_id`。

### 2. 启动服务

```powershell
.\start_web_api.ps1
```

或直接：

```powershell
$env:XHS_ENGINE = "web"
python xhs_api.py --host 0.0.0.0 --port 8700
```

### 3. 调用

```bash
curl -X POST http://127.0.0.1:8700/v1/search \
  -H "Content-Type: application/json" \
  -d '{"keyword":"手冲咖啡","pages":1,"mode":"png"}'
```

---

## 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 服务信息与引擎状态 |
| GET | `/health` | 探活（含登录态） |
| GET | `/docs` | 自述文档 |
| POST | `/v1/search` | 同步搜索（冷启动约 50s，缓存命中 0s） |
| POST | `/v1/search/async` | 异步搜索，返回 `task_id` |
| GET | `/v1/tasks/<id>` | 查询异步任务 |
| GET | `/v1/original?url=&mode=` | 单 URL 转原图 |
| POST | `/v1/original/batch` | 批量转原图 |
| GET | `/v1/image?url=&mode=` | 图片字节流，可直接作 `img src` |
| POST | `/v1/download` | 搜索并下载到服务端 |
| POST | `/v1/relogin` | 重新扫码登录，返回二维码 base64 |
| GET | `/v1/relogin/status` | 查询扫码登录进度 |
| GET | `/v1/relogin/qr` | 二维码原始 PNG，可直接 `img src` |

---

## cookie 失效后重新登录

cookie 存在 profile 目录里，正常情况下有效期很长，但会过期或被登出。
**服务会自动识别并给出明确指引，不会静默返回空结果。**

### 自动识别

每次搜索都会顺带捕获页面自然发出的 `user/me` 响应
（在同一个浏览器会话内，**零额外开销**），据此判定登录态：

- 正常：响应里 `logged_in=true`、`guest=false`、带 `user_id`
- 失效：搜索无任何结果 **且** `user/me` 返回 `guest=true`
  → 抛出 `WebError`，API 返回 **HTTP 401**

401 响应形如：

```json
{
  "status": 401,
  "detail": "登录态已失效（cookie 过期或被登出）。请重新扫码：运行 python login_live.py 900，或在 API 上调用 POST /v1/relogin 获取新二维码。",
  "relogin": "POST /v1/relogin 获取新二维码，或运行 python login_live.py 900"
}
```

### 重新扫码（API 方式）

```bash
# 1) 发起登录，拿二维码
curl -X POST http://127.0.0.1:8700/v1/relogin \
  -H "Content-Type: application/json" -d '{}'

# 响应（qr_png_data_uri 可直接塞进 <img src>）：
# {
#   "status": "running",
#   "has_qr": true,
#   "qr_url": "...",
#   "qr_png": "...base64...",
#   "qr_png_data_uri": "data:image/png;base64,...",
#   "poll": "/v1/relogin/status"
# }

# 2) 扫码后查进度
curl http://127.0.0.1:8700/v1/relogin/status
# {"status":"done","user_id":"625d...","elapsed":31.2}
```

更省事的做法：浏览器直接打开
`http://127.0.0.1:8700/v1/relogin/qr`，就是一张二维码图片，
扫完再刷新 `/v1/relogin/status`。

`status` 取值：`idle` / `starting` / `running` / `done` / `error`。
登录成功后会自动清空搜索缓存（旧登录态的结果不再可信）。

### 重新扫码（命令行方式）

```powershell
cd D:\PYxiangmu\WKnx\workspace\api
python login_live.py 900
```

打印 ASCII 二维码（也能存成 `web_qrcode.png`），
每 90 秒自动刷新，不会因为二维码过期白等。

### 自动化建议

消费方可以在收到 401 时自动调 `/v1/relogin` 取二维码推给运维，
或干脆在启动脚本里先查 `/health` 的 `logged_in` 字段做预检。

### mode 参数

| 值 | 说明 | 相对缩略图 |
|---|---|---|
| `png` | 无损 PNG，原始像素 | ~34x |
| `jpg100` | 最高质量 JPEG | ~21x |
| `jpg` | CDN 默认质量 | ~4.4x |
| `raw` | 原始编码（多为 HEIC） | 不定 |

---

## 原图规则（核心发现）

Web 搜索返回的图片链接形如：

```
http://sns-webpic-qc.xhscdn.com/202609120210/<hash>/notes_pre_post/<fileid>!nc_n_webp_mw_1
```

**关键在结尾的 `!nc_n_webp_mw_1`** —— 它把资源钉死在 webp 转码上，
此时加任何 `imageView2` 参数都无效（实测恒为 52516 字节的 webp）。

正确做法是剥离后缀、换到原图 CDN：

```
https://sns-na-i1.xhscdn.com/{fileid}?imageView2/2/format/png
```

| 构造方式 | 实测结果 |
|---|---|
| webpic 域 + 后缀 | WEBP 52 KB |
| 剥离后缀 + webpic 域 | 403 |
| **原图 CDN + fileid + `format/png`** | **PNG 2160×2880，3.2 MB** |
| 原图 CDN + fileid + `format/jpg/q/100` | JPEG 2160×2880，2.27 MB |

原图 CDN 主机：`sns-na-i1/i2/i4/i6.xhscdn.com`（任选，实测一致）。

**这个环节不需要登录、不需要签名** —— 有 fileid 就能取原图。

---

## 性能特征

| 场景 | 耗时 |
|---|---|
| 冷启动搜索（首次关键词） | ~52s |
| 同关键词 5 分钟内重复 | **0.0s**（命中缓存） |
| `/health` 空闲时 | ~9s |
| 并发搜索 | 串行排队（锁保护），不会互相踩踏 |

**52s 的构成**：Playwright 启动 ~3s + 浏览器冷启动 ~8s +
页面加载 ~10s + 搜索响应等待 ~20s + 滚动 ~3s + 关闭 ~5s。

**建议**：长任务用 `/v1/search/async`，避免 HTTP 超时；
批量场景先用 `/v1/search` 预热缓存再下载。

已知优化空间：浏览器常驻可省掉启动/关闭的约 15s，
但需要专用工作线程承载 Playwright（sync API 有线程亲和性）。

---

## 客户端编码（必读）

中文关键词必须以 **UTF-8** 发送。PowerShell 5.1 的
`Invoke-RestMethod` 默认用非 UTF-8 编码 body，中文会退化成 `?`，
**服务端会静默搜到错误内容**。

实测：`猫咪` 经 `Invoke-RestMethod` 到达服务端变成 `??`，
结果是 HTTP 200 但内容完全不对。

服务端已识别这种退化并返回 **400**。若客户端编码不可控，
改用 `keyword_b64`：

```python
import base64, requests
kw = "猫咪"
requests.post("http://127.0.0.1:8700/v1/search",
              json={"keyword_b64": base64.b64encode(kw.encode()).decode()})
```

---

## 引擎切换

服务支持两个引擎，通过环境变量选择：

```powershell
$env:XHS_ENGINE = "web"     # 默认：浏览器签名，无需模拟器
$env:XHS_ENGINE = "device"  # 备选：模拟器 + adb + mitmproxy
```

| | web | device |
|---|---|---|
| 模拟器 | 不需要 | 需要常驻 |
| 登录 | 一次扫码 | 不需要（App 已登录） |
| 签名 | 浏览器 JS 自动生成 | App native 生成 |
| 依赖 | playwright + Chrome | adb + mitmproxy + 系统 CA |

---

## 实测数据

**搜索「手冲咖啡」**：66 篇笔记 / 169 张原图，51.8s。

**下载「手冲咖啡」**：101 张，0 失败，256.3 MB。

**下载「手冲咖啡」（CLI 全量）**：166 张，0 失败，725.92 MB。
最大单图 **5464×8192（44.76 MP，38.23 MB）**，
最小 896×1200，无 `.part` 残留。

**免登录能力边界**（`guest_probe.py`）：
首页推荐流返回 30 条笔记（SSR 内嵌，非 XHR），
但详情页返回「你访问的页面不见了」，且封面是 `spectrum` 缩略图。
**结论：搜索与原图必须登录。**

---

## 文件说明

**生产文件**

- `xhs_web.py` — Web 引擎核心（登录、搜索、原图、下载、缓存、并发锁）
- `xhs_api.py` — Flask HTTP 层（双引擎适配）
- `xhs_core.py` — 设备引擎（备选）
- `login_live.py` — 扫码登录（ASCII 二维码 + 自动刷新）
- `start_web_api.ps1` — 一键启动（5 项预检）
- `test_web_api.py` — 全端点验证
- `README-API.md` — 旧版设备方案文档

**探测脚本（留有实测证据，可复现）**

- `replay_probe.py` — 签名必要性实验（证明 shield 逐请求签名、必需）
- `guest_probe.py` — 免登录能力边界（证明搜索必须登录）
- `suffix_test.py` — 原图后缀剥离实验（证明 `!nc_n_webp_mw_1` 是症结）
- `post_login_verify.py` — 登录后端到端验证（搜索 + 原图质量）
- `test_web_api.py` — 全端点验证
- `test_api.py` — 旧版设备引擎的测试客户端

---

## 已知限制

1. **必须登录**：搜索接口要求登录态，未登录时页面根本不发
   `search/notes` 请求，裸请求返回 `-101 无登录信息`。
2. **单实例串行**：同一时刻只处理一个搜索请求，靠缓存缓解。
3. **Playwright 线程亲和**：sync API 对象不能跨线程复用，
   因此每次搜索独立启停浏览器（这是 52s 开销的主要来源）。
4. **代理依赖**：当前配置走 `http://127.0.0.1:7897`，
   部署到其他机器需调整 `xhs_web.py` 的 `PROXY` 常量。
