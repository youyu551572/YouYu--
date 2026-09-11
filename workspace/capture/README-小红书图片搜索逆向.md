# 小红书 9.33.4 图片搜索接口逆向 — 交付文档

> 目标设备：雷电模拟器 9（Android 9 / x86_64 / Magisk Delta 25201）
> 目标应用：小红书 `com.xingin.xhs` v9.33.4（versionCode 9334801, targetSdk 35, arm64-v8a）
> 工作目录：`D:\PYxiangmu\WKnx\workspace\capture`

---

## 一、抓包链路（已打通并验证）

### 1.1 拓扑

```
小红书 App ──系统代理 127.0.0.1:8080──▶ adb reverse 隧道 ──▶ 宿主机 mitmdump:8080
                                                                    │
                                                              xhs_addon.py
                                                       （结构化落盘 + 图片 URL 抽取）
```

关键取舍：设备在 `172.16.1.15/24`，宿主机 LAN 在 `192.168.1.167`，**跨网段且设备无默认路由**，
因此弃用「直连宿主机 IP」方案，改用 `adb reverse` —— 走 adb 通道，天然绕过网段与防火墙问题。

### 1.2 证书注入（Android 9 需装到系统信任区）

App 的 `targetSdk=35`，不属于用户 CA 信任范围，必须注入系统信任区。

```bash
# 1) 生成 Android 格式证书名（subject_hash_old）
"C:\Program Files\Git\usr\bin\openssl.exe" x509 -inform PEM -subject_hash_old \
  -in "%USERPROFILE%\.mitmproxy\mitmproxy-ca-cert.pem" -noout
# → c8750f0d

# 2) 注入（脚本见 install_ca.sh，已执行）
mount -o rw,remount /                                    # system-as-root，remount 根分区
cp c8750f0d.0 /system/etc/security/cacerts/              # 系统信任区
cp c8750f0d.0 /data/misc/user/0/cacerts-added/           # 用户信任区（双保险）
# 同时落 Magisk 模块 /data/adb/modules/xhs_mitm_ca 做持久化

# 3) 隧道 + 代理
adb reverse tcp:8080 tcp:8080
adb shell settings put global http_proxy 127.0.0.1:8080
```

**实测结论：系统 CA 已足够，小红书未在 HTTP 层做证书固定（CertificatePinner）阻挡。**
无需 Frida unpinning 即可解密全部 HTTPS 流量 —— 这大幅提升了稳定性。

### 1.3 启动抓包

```bash
mitmdump --listen-host 0.0.0.0 --listen-port 8080 \
  --set block_global=false -s xhs_addon.py --flow-detail 0
```

---

## 二、已逆向的接口

### 2.1 搜索接口（核心）

```
GET https://so.xiaohongshu.com/api/sns/v10/search/notes
    ?keyword={URL编码关键词}
    &filters=%5B%5D
    &sort=
    &page=1
    &page_size=20
    &source=deep_link
    &search_id={时间戳}
    &session_id=...
```

伴随接口：

```
GET https://edith.xiaohongshu.com/api/sns/v2/search/filter
    ?keyword=...&search_id=...&tab_id=general...
```

### 2.2 关键请求头（签名体系）

| 头部 | 作用 |
|---|---|
| `shield` | 核心签名（native 生成，形如 `XYAAQABAAAAA...`） |
| `xy-common-params` | 公共参数（`fid`/`gid`/`device_id` 等） |
| `x-mini-s1` / `x-mini-sig` / `x-mini-mua` | 设备指纹与二次签名 |
| `xy-platform-info` | 平台信息（`platform`/`build`/`deviceId`） |
| `x-legacy-did` / `x-legacy-sid` | 设备与会话标识 |

### 2.3 触发搜索的方式（无需 UI 自动化）

小红书把全部 deeplink 收口到导出组件 `com.xingin.xhs/.routers.RouterPageActivity`，
因此可以直接用 `am start` 驱动，绕开 uiautomator 被拒的问题：

```bash
adb shell am start -a android.intent.action.VIEW \
  -d 'xhsdiscover://search/result?keyword=%E7%8C%AB%E5%92%AA' com.xingin.xhs
# → 命中 com.xingin.alioth.search.GlobalSearchActivity
```

Manifest 中已确认的 deeplink host：`search`、`image_search`、`instore_search`、`note`、`user`、`topic` 等。

### 2.4 图片搜索（以图搜图）接入点

已从 Manifest 完整定位组件，供后续扩展：

```
com.xingin.imagesearch.ImageSearchActivity                          （未导出）
com.xingin.alioth.imagesearch.page.ImageSearchActivity
com.xingin.alioth.imagesearch.active.scan.ImageSearchScanActivity   （拍照识别）
com.xingin.alioth.imagesearch.active.container.ActiveImageSearchActivity
com.xingin.alioth.imagesearch.question.ImageSearchQuestionPageActivity
com.xingin.alioth.search.result.images.detail.SearchImageDetailActivity
deeplink: xhsdiscover://image_search
```

---

## 三、原图下载规则（核心成果）

### 3.1 问题

App 搜索列表返回的图片 URL 带三重压缩参数：

```
https://sns-na-i2.xhscdn.com/notes_pre_post/1040g3k0...
  ?imageView2/2/w/576/format/heif/q/58|imageMogr2/strip
  &redImage/frame/0/enhance/4&ap=5&sc=SRH_PRV&sign=...
        ↑宽576   ↑HEIF  ↑质量58
```

### 3.2 解法

**剥离全部查询参数，只保留原始路径，再按需附加格式指令。**

```
原图（无损 PNG）  :  {base}?imageView2/2/format/png
原图（高质量JPEG）:  {base}?imageView2/2/w/0/format/jpg/q/100
原始字节（HEIC）  :  {base}
其中 base = 去掉 "?" 及其后全部内容的纯净 URL
```

`fileid` 字段提供纯净路径，可直接拼接：`https://{cdn_host}/{fileid}`。

### 3.3 实测画质对比（同一张图，真实测速数据）

| 变体 | 格式 | 分辨率 | 体积 | 相对压缩版 |
|---|---|---|---|---|
| App 列表基线 `w/576/format/heif/q/58` | HEIC | 576×766 | 178 KB | 1.0x |
| `format/jpg` | JPEG | 1924×2560 | 788 KB | 4.4x |
| `w/1080/format/jpg` | JPEG | 1080×1437 | 313 KB | 1.8x |
| `w/0/format/jpg/q/100` | JPEG | 1924×2560 | 3.71 MB | 20.8x |
| **`format/png`** | **PNG** | **1924×2560** | **6.16 MB** | **34.5x** |

**`sign` 参数并非必需** —— 剥离后仍稳定返回 200，故可直接构造原图 URL。

### 3.4 批量下载实测（15 篇笔记 / 67 张图）

```
成功 67  失败 0  总下载 493.77 MB  平均单张 7.37 MB  耗时 288s
尺寸与响应中声明值 100% 一致（部分实际更大）

分辨率分布：
  3024x4032  x18  (12.2 MP)
  4284x5712  x5   (24.5 MP)
  1920x2560  x4
  ...

相对 App 内 576x766 的面积提升：最高 x55.5
最大单张：4284x5712  18.42 MB
```

---

## 四、工具清单

| 文件 | 作用 |
|---|---|
| `install_ca.sh` | 设备端证书注入（系统区 + 用户区 + Magisk 模块） |
| `xhs_addon.py` | mitmproxy 插件：全量落 JSONL、抽取图片 URL、搜索响应单独存盘 |
| `parse_search.py` | 解析搜索响应 → `notes.json` / `originals_png.txt` / `originals_jpg.txt` |
| `download_originals.py` | 原图批量下载器（png / jpg100 / jpg / raw 四模式，多线程，断点跳过） |
| `probe_image_variants.py` | 画质变体实测工具（验证 CDN 压缩参数规律） |
| `xhs_capture.js` | Frida SSL unpinning + 代理强制（当前链路非必需，留作备用） |
| `ui_dump.js` / `launch_imgsearch.js` | Frida UI 树 dump 与 Activity 拉起（受限，见第五节） |

### 常用命令

```powershell
# 触发搜索 + 抓包
adb shell am start -a android.intent.action.VIEW \
  -d 'xhsdiscover://search/result?keyword=%E7%8C%AB%E5%92%AA' com.xingin.xhs

# 解析
python parse_search.py

# 下载原图（无损 PNG）
python download_originals.py --mode png --workers 6
# 或最高质量 JPEG（体积约为 PNG 的 60%）
python download_originals.py --mode jpg100
```

---

## 五、已知限制与规避

### 5.1 进程 houdini 转译崩溃（重要）

tombstone 证据链：

```
#09 com.xingin.capa.hook.CapaFileHook.registerFileHook   ← native 方法
#04 libnb.so  android::native_bridge2_getTrampoline
#00 libhoudini.so  SIGSEGV                                ← ARM 转译层崩溃
#16 com.xingin.longlink.t.run                             ← 长连接线程触发
```

小红书是 arm64 应用，经雷电的 houdini 转译层运行；其 `CapaFileHook` native 代码会触发
转译器缺陷导致 `SIGSEGV`。**进程通常存活数分钟后崩溃。**

规避：把握启动后的可用窗口完成操作；或改用真机 / arm 架构模拟器彻底规避。

### 5.2 风控码

抓包统计（单次会话）：

```
200 -> 691    460 -> 110    403 -> 134
```

`460` 是小红书风控拦截码，多见于推送类接口。**搜索接口本身返回 200，不受影响。**
若遇 460 增多，重启 App 取得新会话即可。

### 5.3 uiautomator 不可用

设备 `accessibility_enabled=0`，`uiautomator dump` 报 `null root node`；
小红书主线程对 `dumpsys activity top` 亦返回 `Timeout`（反 dump）。

规避：改用 **deeplink 驱动**（已验证可行），无需 UI 自动化。

### 5.4 Frida 与小红书

Frida 可注入（x86_64 server 对 arm64 转译进程，Java 层 Hook 有效），
但激进 Hook（全类遍历 Hook TrustManager）会引发 `HeapTaskDaemon` GC 崩溃。
**当前抓包链路不依赖 Frida，建议保持不注入。**

---

## 六、产物位置

```
workspace/capture/
  api__api_sns_v10_search_notes_*.json    搜索响应原文（114 KB）
  notes.json                              15 篇笔记结构化数据
  originals_png.txt                       67 条无损原图 URL
  originals_jpg.txt                       67 条高质量 JPEG URL
  download_manifest.json                  原图清单（含声明尺寸）
  traffic.jsonl                           全量抓包记录
  install_ca.sh / xhs_addon.py / ...      工具脚本

workspace/downloads/xhs/
  6a91403c..._三喜～棉花糖/                 按笔记分目录的原图
  ...
  _download_report.json                   下载报告
```

---

## 七、端到端流水线（推荐入口）

一条命令完成「搜索 → 抓包 → 解析 → 下载原图」：

```powershell
# 前置：mitmdump 已在 8080 运行 + adb reverse 已建立
python xhs_pipeline.py --keyword "咖啡" --pages 2 --mode png
python xhs_pipeline.py --keyword "猫咪" --pages 0 --mode jpg100 --skip-download
```

流水线内部五步：

1. 清理上次抓包产物
2. 重启小红书（force-stop → LAUNCHER）
3. 下发 `xhsdiscover://search/result?keyword=...` deeplink 触发搜索
4. `input swipe` 滚动加载更多页（可配 `--pages`）
5. 解析响应 → 下载原图

滚动加载走屏幕中轴 swipe，**不依赖精确坐标**，因此规避了 uiautomator 不可用的问题。

### 下载模式

| 模式 | 参数 | 输出 | 相对 App 列表图体积 |
|---|---|---|---|
| 无损 PNG | `--mode png` | 原始像素 PNG | ~34x |
| 高质量 JPEG | `--mode jpg100` | `w/0/format/jpg/q/100` | ~21x |
| 标准 JPEG | `--mode jpg` | CDN 默认质量 | ~4.4x |
| 原始字节 | `--mode raw` | 原始编码（HEIC 等） | 不定 |

下载器特性：多线程、`.part` 临时文件 + 原子替换、`Content-Length` 完整性校验（防截断图）、
连接中断自动重试 5 次（指数退避）、已存在文件自动跳过（可安全重跑补齐）。

---

## 八、最终验证数据

两轮独立关键词搜索的完整核验：

| 数据集 | 笔记 | 图片 | 体积 | 最高分辨率 | 低于 App 列表图的 | 面积提升（中位 / 最高） |
|---|---|---|---|---|---|---|
| `猫咪` | 15 | 67 | 493.77 MB | 4284×5712 (24.5 MP) | 0 张 | x27.6 / x55.5 |
| `咖啡` | 17 | 61 | 315.42 MB | 4284×5712 (24.5 MP) | 0 张 | x27.6 / x55.5 |

* 成功率：`猫咪` 67/67；`咖啡` 首轮 55/61（网络中断），启用重试后补齐至 61/61
* `.part` 残片：0
* 所有图片尺寸与响应中声明值 **100% 一致**（部分实际更大）

**画质不压缩目标达成**：每张图都显著大于 App 内列表图，且并非放大所得，
而是直接取自 CDN 原始资源。

### 补充发现

* 图片族不止 `notes_pre_post`，还存在 `note_pre_post_uhdr`（Ultra HDR）等前缀，
  解析器不依赖固定前缀，均能正确落到原图。
* CDN 节点：`sns-na-i1/i2/i4/i6.xhscdn.com`、`sns-avatar-qc.xhscdn.com`（头像）、
  `ads-img-al.xhscdn.com`。
* `sign` 参数非必需：剥离全部查询参数后重新附加格式指令即可命中原图。

