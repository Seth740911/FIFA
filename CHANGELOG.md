# 2026-06-16 工作日志

## FIFA 视频 HLS 密钥缓存修复

### 问题
- FIFA 世界杯 APP（WebView + hls.js + IndexedDB）缓存 m3u8 视频时只存 .ts 分片，不存 AES-128 密钥
- 官网换密钥后，本地缓存无法播放（旧密钥加密的分片用新密钥解不了）
- 旧逻辑 `_playFromCache` 用新鲜 m3u8 的密钥 URL 替换旧的 → 新密钥解旧分片必然失败
- 失败后删缓存重新下载 → 用户流量浪费 + 服务器压力

### 改动（G:\AI\FIFA\index.html）
1. **_saveVideoToDB**: 增加 `keyBlob` 参数，存入 IndexedDB 记录
2. **_cacheVideoSegments**: 缓存时解析 m3u8 提取 `#EXT-X-KEY` URI，下载密钥文件（16字节）存入 `_cachedKeyBlob`，同步保存到 IndexedDB
3. **_playFromCache**: 
   - 有本地密钥时，创建 blob URL 替换密钥 URI（保留 IV），不再请求官网
   - 无本地密钥时（旧缓存兼容），先尝试用原始密钥 URL 补下密钥
   - `#EXT-X-KEY` 替换逻辑改为 `line.replace(/URI="[^"]*"/, ...)` 只替换 URI 部分
4. **_startBackgroundCache 触发条件**: 缓存完整时跳过后台重复下载
5. **freshSegUrls bug 修复**: 第 2486 行 `push(null)` 改为正确构造绝对 URL

### 测试结果
- ✅ 首次打开：网络流播放 + 后台缓存密钥和分片
- ✅ 再次打开（缓存完整）：完全离线播放，手机图标 📱，0 段走新鲜 URL
- ✅ 密钥本地化生效：日志显示 `使用本地密钥 blob URL`，`有本地密钥缓存（16 字节）`
- ✅ 断点续传正常
- ⚠️ 旧缓存（无 keyBlob）且密钥 URL 过期：无法恢复，需重新缓存（过渡期正常现象）

### 技术要点
- uplynk CDN：每个 .ts 段前有独立 #EXT-X-KEY 行（IV 递增），但 URI 相同，密钥内容固定
- 密钥 URL 签名过期 ≠ 密钥内容变，缓存密钥 blob 是彻底解法
- blob URL 可被 hls.js XHR 正常请求，完全离线

### 遗留噪音（非代码问题）
- `inspector.js` 的 `InvalidStateError`：Chrome 扩展注入噪音，忽略
- `_diag` 的 `ERR_EMPTY_RESPONSE`：服务端未实现 `/api/diag`，静默失败不影响功能
