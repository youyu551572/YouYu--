# 小红书搜索 / 原图 API — 调用文档

HTTP 服务封装小红书的搜索与原图获取能力，供其他项目直接调用。
搜索走设备驱动（deeplink 触发 + 抓包解析），**无需复刻 native 签名**。

```
其他项目 ──HTTP──▶ xhs_api (127.0.0.1:8700)
                      ├── 驱动 adb 下发 deeplink
                      ├── mitmdump 抓包（内置管理）
                      └── 解析响应 + 原图 URL 转换 + 图片代理
```

---

## 快速开始

```bash
# 启动服务（会自动拉起 mitmdump 抓包器）
cd D:\PYxiangmu\WKnx\workspace\api
python xhs_api.py                # 默认 0.0.0.0:8700
```

浏览器打开 `http://127.0.0.1:8700/docs` 查看接口说明。

```python
import requests
r = requests.post("http://127.0.0.1:8700/v1/search",
                  json={"keyword": "猫咪", "pages": 1, "mode": "png"},
                  timeout=240)
data = r.json()
print(data["note_count"], data["image_count"])
for u in data["images"][:5]:
    print(u)          # 已是原图 URL，可直接下载
```

---

## 端点

| 方法 | 路径 | 说明 | 耗时 |
|---|---|---|---|
| GET | `/health` | 环境健康检查（设备/隧道/代理/抓包器/App） | 秒级 |
| GET | `/docs` | 接口文档页 | 秒级 |
| POST | `/v1/search` | 同步搜索 | **10-40s** |
| POST | `/v1/search/async` | 异步搜索，返回 `task_id` | 立即 |
| GET | `/v1/tasks/<task_id>` | 查询异步任务 | 秒级 |
| GET | `/v1/original` | 单 URL 转原图（**不需要设备**） | 毫秒 |
| POST | `/v1/original/batch` | 批量转原图（**不需要设备**） | 毫秒 |
| GET | `/v1/image` | 图片字节流，可直接 `<img src>` | 1-10s |
| POST | `/v1/download` | 搜索并下载原图到服务端目录 | 分钟级 |

### POST /v1/search

请求：

```json
{
  "keyword": "猫咪",
  "pages": 1,
  "mode": "png",
  "timeout": 45,
  "use_cache": true,
  "restart_app": false
}
```

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `keyword` | string | 必填 | 搜索关键词 |
| `keyword_b64` | string | - | 关键词的 base64(UTF-8)，用于规避客户端编码问题 |
| `pages` | int | 0 | 额外滚动翻页次数（0-10） |
| `mode` | string | png | 原图模式，见下表 |
| `timeout` | int | 45 | 等待抓包超时（10-180 秒） |
| `use_cache` | bool | true | 复用 5 分钟内的同参数结果 |
| `restart_app` | bool | false | 先重启 App（崩溃恢复用） |

响应：

```json
{
  "keyword": "猫咪",
  "mode": "png",
  "notes": [
    {
      "note_id": "6a6da0a7000000002202cd06",
      "title": "萌虎出山嗷呜～",
      "user": "小小娜",
      "type": "normal",
      "images": [
        {"base": "https://sns-na-i4.xhscdn.com/notes_pre_post/1040g...",
         "fileid": "notes_pre_post/1040g...",
         "width": 1920, "height": 2560}
      ]
    }
  ],
  "images": ["https://sns-na-i4.xhscdn.com/...?imageView2/2/format/png"],
  "image_bases": ["https://sns-na-i4.xhscdn.com/..."],
  "note_count": 14,
  "image_count": 62,
  "elapsed": 14.73,
  "source_files": ["api__api_sns_v10_search_notes_....json"],
  "cached": false
}
```

`images` 已经是可直接下载的原图 URL；`image_bases` 是剥离参数的纯净地址，
便于自行附加其他格式指令。

### mode 参数

| 值 | 输出 | 相对 App 列表图体积 | 适用 |
|---|---|---|---|
| `png` | 无损 PNG，原始像素 | ~34x | 要求绝对无损 |
| `jpg100` | 最高质量 JPEG | ~21x | 体积与质量平衡（推荐） |
| `jpg` | CDN 默认质量 | ~4.4x | 仅需比列表图清晰 |
| `raw` | 原始编码（HEIC 等） | 不定 | 需要原始字节 |

### 原图规则（服务内部实现）

App 返回的图片 URL 带三重压缩参数：

```
?imageView2/2/w/576/format/heif/q/58|imageMogr2/strip&sc=SRH_PRV&sign=...
     ↑宽576      ↑HEIF        ↑质量58
```

剥离全部查询参数后重新附加格式指令即命中原图，`sign` 并非必需：

```
png    :  {纯净URL}?imageView2/2/format/png
jpg100 :  {纯净URL}?imageView2/2/w/0/format/jpg/q/100
```

---

## 各语言调用示例

### Python

```python
import requests

API = "http://127.0.0.1:8700"

# 搜索
r = requests.post(f"{API}/v1/search", json={"keyword": "猫咪", "mode": "jpg100"},
                  timeout=240)
d = r.json()

# 下载原图
for i, url in enumerate(d["images"]):
    img = requests.get(f"{API}/v1/image", params={"url": url}, timeout=180)
    with open(f"{i:03d}.jpg", "wb") as f:
        f.write(img.content)
```

### JavaScript / Node

```javascript
const API = "http://127.0.0.1:8700";

const res = await fetch(`${API}/v1/search`, {
  method: "POST",
  headers: { "Content-Type": "application/json; charset=utf-8" },
  body: JSON.stringify({ keyword: "猫咪", mode: "png" }),
});
const data = await res.json();
console.log(data.note_count, data.image_count);

// 前端可直接引用图片
// <img src={`${API}/v1/image?mode=png&url=${encodeURIComponent(data.images[0])}`} />
```

### curl

```bash
curl -X POST http://127.0.0.1:8700/v1/search \
  -H "Content-Type: application/json; charset=utf-8" \
  --data-binary '{"keyword":"猫咪","pages":1,"mode":"png"}'

# 纯字符串转换，不需要设备
curl "http://127.0.0.1:8700/v1/original?mode=png&url=$(python -c "import urllib.parse;print(urllib.parse.quote('https://sns-na-i4.xhscdn.com/notes_pre_post/xxx?imageView2/2/w/576'))")"
```

### PowerShell

```powershell
# 必须显式使用 UTF-8 字节，否则中文会退化成 '?'
$json = '{"keyword":"猫咪","mode":"png"}'
$bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
Invoke-RestMethod -Uri "http://127.0.0.1:8700/v1/search" -Method POST `
  -Body $bytes -ContentType "application/json; charset=utf-8" -TimeoutSec 240
```

### Java

```java
HttpClient client = HttpClient.newHttpClient();
String body = "{\"keyword\":\"猫咪\",\"mode\":\"png\"}";
HttpRequest req = HttpRequest.newBuilder()
    .uri(URI.create("http://127.0.0.1:8700/v1/search"))
    .header("Content-Type", "application/json; charset=utf-8")
    .POST(HttpRequest.BodyPublishers.ofString(body, StandardCharsets.UTF_8))
    .timeout(Duration.ofSeconds(240))
    .build();
HttpResponse<String> resp = client.send(req, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
```

---

## 注意事项

**客户端编码**。中文关键词必须以 UTF-8 发送。PowerShell 5.1 的 `Invoke-RestMethod`
与部分 curl 构建默认用非 UTF-8 编码 body，中文会退化成 `?`，导致搜到错误内容。
服务端会识别这种情况并返回 400；若客户端编码不可控，改用 `keyword_b64`。

**单设备串行**。搜索依赖单一模拟器实例，服务内部持锁串行执行。
并发请求会排队，不会并行加速。需要并发就多开模拟器实例部署多份服务。

**耗时**。单次搜索 10-40 秒（含 App 启动、deeplink 跳转、抓包等待）。
长任务建议用 `/v1/search/async` 配合 `/v1/tasks/<id>` 轮询，避免 HTTP 超时。

**缓存**。同参数 5 分钟内复用结果（`cached: true`）。需要最新数据传 `use_cache: false`。

**App 崩溃**。小红书是 arm64 应用跑在 x86_64 雷电的 houdini 转译层上，
其 `CapaFileHook` native 代码会触发转译器缺陷导致进程数分钟后崩溃。
服务检测到 App 不在时会自动拉起；持续异常可传 `restart_app: true`。

---

## 错误码

| 状态 | 含义 | 处理 |
|---|---|---|
| 400 | 参数错误（含编码丢失检测） | 检查 keyword / mode |
| 404 | task 不存在 | 检查 task_id |
| 502 | 设备侧失败（App 起不来、deeplink 失败、抓包超时） | 看 `/health`，尝试 `restart_app: true` |
| 500 | 服务内部错误 | 看服务端日志 |

错误响应格式：

```json
{"detail": "错误说明", "status": 502}
```

---

## 健康检查

```bash
curl http://127.0.0.1:8700/health
```

```json
{
  "adb_ok": true,
  "device": "emulator-5554",
  "device_online": true,
  "mitm_running": true,
  "tunnel": true,
  "device_proxy": "127.0.0.1:8080",
  "app_alive": false,
  "top_activity": "com.android.launcher3/.Launcher"
}
```

`app_alive: false` 属正常（崩溃后未运行），下次搜索会自动拉起。

---

## 作为 Python 库使用

不启动 HTTP 服务，直接 import 核心引擎：

```python
import sys
sys.path.insert(0, r"D:\PYxiangmu\WKnx\workspace\api")
from xhs_core import XhsEngine

eng = XhsEngine()
data = eng.search("猫咪", pages=1, mode="png")
print(data["note_count"], data["image_count"])

# 纯 URL 转换（不需要设备）
print(eng.to_original("https://sns-na-i4.xhscdn.com/xxx?imageView2/2/w/576", "png"))

# 取图
raw, ctype = eng.fetch_image(data["images"][0])

# 批量下载
stats = eng.download(data["images"], r"D:\out\cats", mode="png", workers=8)
print(stats["ok"], stats["fail"])
```
