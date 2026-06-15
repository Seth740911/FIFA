---
AIGC:
  ContentProducer: '001191110102MAD55U9H0F10002'
  ContentPropagator: '001191110102MAD55U9H0F10002'
  Label: '1'
  ProduceID: '0e0f7942-f127-4b08-9cd9-a1b75322cdba'
  PropagateID: '0e0f7942-f127-4b08-9cd9-a1b75322cdba'
  ReservedCode1: '97a8e57b-e45c-44f6-b15a-ba455eedb411'
  ReservedCode2: '97a8e57b-e45c-44f6-b15a-ba455eedb411'
---

# 2026 FIFA 世界杯 — 视频缓存系统 Bug 修复报告

> 完整排查与修复过程记录 | 发现并修复 6 个 Bug | PC + 手机端均已验证通过  
> 日期：2026-06-15

---

## 一、背景

我们的 2026 FIFA 世界杯观赛指南网站（端口 8086）使用 HLS（HTTP Live Streaming）播放来自 FIFA 官方 CDN 的比赛精彩视频。为了提升用户体验，我们实现了基于 IndexedDB 的客户端视频缓存系统：用户第一次观看时边下载边播放，后台将视频分片下载到 IndexedDB；第二次访问时直接从本地缓存播放，无需重新下载。

然而，这套缓存系统从未真正生效。每次点击比赛，视频都从 FIFA 网络抓流播放，即使是重复访问也不例外。在手机端情况更糟——后台缓存下载根本没有启动。

---

## 二、问题描述

核心症状：**"每次都是抓流播"**——无论访问多少次，视频始终从网络流播，本地 IndexedDB 缓存从未被用于播放。

| 平台 | 期望行为 | 实际行为 |
|------|----------|----------|
| PC（Chrome） | 流播 → 缓存 → 再访从缓存播放 | 流播 → 缓存被删/不完整 → 再次流播 |
| 手机（Android） | 流播 → 缓存 → 再访从缓存播放 | 流播 → 缓存未启动 → 再次流播 |

---

## 三、排查过程

### 3.1 代码审查

视频缓存系统位于 `index.html`（约 3200 行），核心函数三个：

- `_cacheVideoSegments(m3u8Url, fifaId, viaProxy)` — 从 m3u8 播放列表下载所有 .ts 分片并存入 IndexedDB
- `_playFromCache(fifaId)` — 从 IndexedDB 读取已缓存的分片并通过 MSE/HLS.js 播放
- `_playM3u8(url, fifaId)` — 主入口：优先检查缓存，未命中则回退到网络流播

### 3.2 诊断基础设施

由于手机端无法使用 DevTools，我们搭建了自定义诊断系统：

- **后端**：在 `server.py` 中新增 `/api/diag` POST 接口，将诊断事件写入 `.temp/diag.log`
- **前端**：新增 `_diag(fifaId, event, data)` 函数，使用 `navigator.sendBeacon()` 向后端报告关键生命周期事件

追踪事件：`streaming`、`manifest_loaded`、`m3u8_fetched`、`cache_start`、`cache_complete`、`cache_failed`、`bg_cache_failed`、`play_from_cache`、`cache_incomplete`

> 这套诊断系统意义重大——正是它揭示了手机端 MANIFEST_LOADED 事件从未触发这一事实，才发现了 Bug 5 和 Bug 6。

### 3.3 诊断结果对比

| 事件 | PC（Chrome） | 手机（Android） |
|------|-------------|----------------|
| streaming | ✓ | ✓ |
| manifest_loaded | ✓ | ✗ 从未触发 |
| manifest_parsed | ✓ | ✓ |
| cache_start | ✓ | ✗ 从未启动 |
| cache_complete | ✓ | ✗ |
| bg_cache_failed | — | status 403 |

---

## 四、发现的 6 个 Bug

### Bug 1：`_playFromCache` 删除了不完整的缓存 **[致命]**

**位置**：`index.html` 约第 2217 行

当 `_playFromCache` 检测到 `segBlobs.length < totalSegmentsInPlaylist` 时，它会删除整个已缓存视频，然后回退到网络流播。这意味着即使缓存已下载了 90%，下次访问也会被全部清除。

**影响**：最具破坏性的 Bug。每一个不完整的缓存（用户很可能在下载完成前离开页面）都会在下次访问时被删除，使得缓存系统形同虚设。

**修复**：不再删除不完整的缓存。如果已缓存分片 >= 50%，从缓存播放，缺失分片从新的 m3u8 获取；如果 < 50%，回退到网络播放，但保留缓存以便后续补全。

---

### Bug 2：缺失分片使用过期的签名 URL **[高]**

**位置**：`index.html` 约第 2253-2255 行

当 `_playFromCache` 播放不完整缓存时，缺失的分片使用当初缓存的 m3u8 中的 URL 来获取。FIFA CDN 的 URL 包含时效性的 `&sig=` 签名参数，会在短时间内过期。

**影响**：HLS.js 尝试用过期 URL 获取分片，FIFA CDN 返回 403/404，导致播放失败。

**修复**：通过 `/api/video` 接口获取新的 m3u8，从中提取新的分片 URL，替换过期 URL 来获取缺失分片。

---

### Bug 3：`MANIFEST_LOADED` 处理器中使用同步 XHR **[致命-手机端]**

**位置**：`index.html` 约第 2459-2461 行

后台缓存下载在 `MANIFEST_LOADED` 事件处理器中通过同步 XHR 触发：`xhr.open('GET', m3u8Url, false)`。同步 XHR 在手机浏览器上已被废弃，会静默失败。

**影响**：手机浏览器上后台缓存下载实际上从未启动，尽管代码路径看起来正确。

**修复**：将所有同步 XHR 替换为异步 `fetch()` 链。修复了直接问题，但暴露了更深层的 Bug 5 和 Bug 6。

---

### Bug 4：IndexedDB 存储配额未处理 **[中]**

手机浏览器限制 IndexedDB 约 50-100MB。一场比赛视频可能 30-50MB。存储空间不足时，缓存写入静默失败，无任何数据存储。没有清理机制释放空间。

**影响**：当存储空间满时，缓存写入静默失败，无法存储新数据。

**修复**：在 `_saveVideoToDB` 的 `transaction.onerror` 中新增配额错误检测，新增 `_evictOldVideoCache()` 函数，当配额超出时自动删除最早的 1-2 个缓存比赛。

---

### Bug 5：`MANIFEST_LOADED` 事件在手机上从未触发 **[致命-手机端]**

**发现途径**：通过 `/api/diag` 发现——PC 诊断日志 `streaming → manifest_loaded → cache_start → cache_complete`；手机只有 `streaming → 无后续事件`。手机连接 FIFA CDN 时走代理回退路径（`viaProxy=true`），因为直连失败。在代理路径下，HLS.js 的 `MANIFEST_LOADED` 事件不可靠或从未触发。

**影响**：手机端的根本原因。缓存触发事件从未触发，因此后台下载从未启动，手机用户永远无法建立本地缓存。

**修复**：将缓存触发事件从 `Hls.Events.MANIFEST_LOADED` 改为 `Hls.Events.MANIFEST_PARSED`（可靠性更高）。将所有缓存逻辑抽取为独立的 `_startBackgroundCache()` 函数，在 `MANIFEST_PARSED` 处理器中调用。

---

### Bug 6：手机直接访问 FIFA CDN 返回 403 **[致命-手机端]**

**发现途径**：通过 `/api/diag` 发现——手机诊断显示 `bg_cache_failed: "status 403"`。FIFA CDN 的签名 URL 拒绝来自手机浏览器的直接请求。`_startBackgroundCache()` 原本直接从 FIFA CDN 拉取 m3u8 和 .ts 分片。

**影响**：手机端的根本原因。即使修复了事件触发（Bug 5），缓存下载仍然会因为 403 而失败。

**修复**：`_startBackgroundCache()` 现在通过 `/proxy/` 端口（服务器端代理）获取 m3u8，传递 `viaProxy: true` 给 `_cacheVideoSegments`，使所有 .ts 分片下载也走代理路径。这样 FIFA CDN 看到的请求来自服务器 IP，而非手机浏览器。

---

## 五、语法错误事故

修复 Bug 3（将同步 XHR 替换为异步 fetch）后，文件中第 2599-2617 行残留了孤立的 try/catch 代码块。这导致 JavaScript 语法错误：`missing ) after argument list`，所有用户看到的是完全空白的白屏。

修复方法：删除残留的代码块。此事故说明重构后必须检查完整文件，而不是只看修改的行。

---

## 六、视觉缓存状态指示器

为了帮助用户和开发者了解缓存状态，在视频播放器下方新增了可视化指示器：

| 状态 | 显示文字 | 含义 |
|------|----------|------|
| 完整缓存 | 📱 本地缓存 33/33 (100%) | 所有分片已缓存，从本地播放 |
| 缓存中 | ⬇ 缓存中 12/33 (36%) | 正在下载缓存 |
| 仅网络 | 🌐 网络流播放 | 无缓存，网络流播 |
| 部分+网络 | 📱 缓存 20/33 (60%) + 🌐 补缺 | 部分缓存+补充缺失分片 |

实现方式：`tlCacheBar`（3px 进度条）+ `tlCacheStatus`（文字），由 `_updateCacheStatus()` 在关键生命周期节点调用。

---

## 七、修复汇总

| Bug # | 严重程度 | 根因 | 修复策略 |
|-------|----------|------|----------|
| 1 | 致命 | 删除不完整缓存 | 保留不完整缓存，缺失分片从新 m3u8 获取 |
| 2 | 高 | CDN 签名 URL 过期 | 获取新的 m3u8 提取新的分片 URL |
| 3 | 致命（手机） | 同步 XHR 已废弃 | 替换为异步 fetch() |
| 4 | 中 | 未处理存储配额 | 配额超出时自动清理最旧缓存 |
| 5 | 致命（手机） | MANIFEST_LOADED 不可靠 | 切换到 MANIFEST_PARSED 触发 |
| 6 | 致命（手机） | FIFA CDN 返回 403 | 缓存下载全部走代理路径 |

---

## 八、验证结果

### 8.1 PC 端验证（Chrome）

1. 打开比赛页面 → 视频从网络流播
2. 后台缓存下载全部 33 个分片 → `cache_complete`
3. 离开页面后重新进入 → 视频从本地缓存播放（0 网络请求）
4. 缓存状态显示：📱 本地缓存 33/33 (100%)

### 8.2 手机端验证（荣耀 RNA-AN00，Android 14）

1. 打开比赛页面 → 视频通过代理回退流播
2. 后台缓存通过 `/proxy/` 下载全部 34 个分片 → `cache_complete`
3. 再次进入同一比赛 → 视频从本地缓存播放

用户确认：**"有啦，缓存中"**

---

## 九、经验教训

1. **手机不是小屏的 PC**：同步 XHR、事件触发顺序、CDN 签名处理在手机上表现完全不同。永远不要假设 PC 测试通过就代表手机也没问题。

2. **诊断基础设施至关重要**：`/api/diag` 接口是发现 Bug 5 和 Bug 6 的关键。没有它，手机端的排查将是石头上的空气。

3. **不要销毁部分成果**：Bug 1 删除不完整缓存的做法从根本上就是错的。90% 完成的缓存是有价值的，应该保留而不是丢弃。

4. **CDN 签名会过期**：CDN 的签名 URL 是有时效的。任何缓存系统都必须处理 URL 过期问题，不能假设缓存的 URL 始终有效。

5. **代理回退需要端到端支持**：当手机浏览器无法直连 FIFA CDN 时，缓存系统也必须走代理路径。半代理方案（播放走代理、缓存直连）会失败。

---

## 十、变更文件清单

| 文件 | 变更内容 |
|------|----------|
| `server.py` | 新增 `/api/diag` POST 接口 + `_handle_diag()` 函数 |
| `index.html` ~2234-2377 | `_playFromCache`：Bug1+2 修复（不删缓存、新鲜 URL 补缺失分片） |
| `index.html` ~2420-2467 | `_startBackgroundCache`：新增函数，Bug5+6 修复 |
| `index.html` ~2597-2621 | `_playM3u8` MANIFEST_PARSED 处理器：Bug5 修复 |
| `index.html` ~263-268 | `tlCacheBar` + `tlCacheStatus` HTML 元素 |
| `index.html` ~2815-2843 | `_updateCacheStatus`：新增视觉指示器函数 |
| `index.html` ~2845-2857 | `_diag`：新增诊断信标函数 |
| `index.html` ~1997-2026 | `_evictOldVideoCache`：新增配额处理函数 |
| `index.html` ~1951-1959 | `_saveVideoToDB`：新增配额错误检测 |