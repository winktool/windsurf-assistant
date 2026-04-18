# Changelog

## v17.18.0 — 主仓归宗 · 公网天网自升级 · 太极生万物 (2026-04-18)

### 核心: 从 v17.3 到 v17.18 跨越 15 次根因打磨 · 一体集成

- **主仓归宗**: `_DEFAULT_PUBLIC_SOURCE` 指向 `cdn.jsdelivr.net/gh/zhouyoukang/windsurf-assistant@main/wam-bundle/` · 公开主仓作为官方发布源
- **sanitize 全绿**: 所有本地 IP / Windows 用户名 / SMB 示例统一 `<host>` / `<share>` placeholder · 零隐私残留
- **多场景 E2E 全通**: DNS 污染 / 慢网 / 镜像全挂 / SMB / 私有 HTTPS / raw / 4 起点 fallback / autoDiscover / HTTP 4xx / 快路径 / 多用户 / semver 10 场景 38 pass / 0 fail

### v17.17 公网天网 (2026-04-18)

- **jsDelivr 四镜像 fallback**: `_JSDELIVR_MIRRORS = [cdn, fastly, gcore, testingcf]` · 任一通即成功
- **_expandJsdelivrSources**: 对 jsDelivr 域名 source 自动展开为镜像链 · 用户源保持不变 (SMB/私有 HTTPS 原样)
- **autoDiscover 默认开**: `wam.autoUpdate.autoDiscover=true` · source 未配置时自动走公开 jsDelivr · 零配置即享自升级
- **DNS 污染自感规避**: China `cdn.jsdelivr.net → 8.7.198.46` 假 IP 自动 fallback 至 fastly/gcore/testingcf
- **四镜像至少一通概率**: 99.99% (单镜像 p=0.1 独立失败计算)

### v17.16 秒切引擎 · Devin sessionToken 预热 (2026-04-17)

- **_prewarmCandidateToken**: Devin 分支 · `_devinFullSwitch` 预热 token · 切号永远 cache HIT
- **_tokenPoolTick**: Devin 纳入 pool · 替代 v13.4 `continue` 跳过 · batch 并行预热
- **秒切理论**: 冷启 Devin (login 1500 + postAuth 1500 + inject 1300) = 4300ms → 预热后 = 1300ms · 节省 3000ms
- **实战验证**: ~1300ms 近似秒切 (< 1500ms 门槛)

### v17.15 披褐怀玉 · 日志静默 · 精准根因修复 (2026-04-17)

- **_injectAdaptive 乐观默认**: 样本不足时 `_DEFAULT_TIMEOUT_MS` 8000 → 3500 (2×P95 留裕量) · 首次切号 P1 不误触发 P2
- **proxy cache 业务错误豁免**: `!isBusinessErr` 才清 cache · 防 `relay_not_configured` 每次清空 proxy TTL
- **monitor rate_limited 静默**: `result.error !== "rate_limited"` 才打 fail 日志 · 不刷屏

### v17.14 公网闭环 · HTTPS 302 重定向 (2026-04-16)

- **autoUpdate HTTPS 支持**: 301/302/303/307/308 五种重定向 · GitHub Releases `/latest/download/` 直达
- **tokenCache v2 持久化**: `{_v:2, idTokens, devinSessions}` 双 bucket 磁盘格式
- **MAX_REDIRECTS 防循环**: 5 次限制

### v17.13 injectAuth 自适应 · 反者道之动 (2026-04-15)

- **_injectAdaptive 独立于网络 RTT**: 样本 ≥ 5 用 p95×1.5 · 删除 Phase 2 死等 · 慢场景 -2s, 卡死 -4s

### v17.12 UI 内化 · 太上不知有之 (2026-04-15)

- **autoVerify / autoExpiry**: 验证清理与刷新有效期内化为 6h/12h 周期定时器 · UI 按钮移除

### v17.11 自适应运行时 (2026-04-14)

- **_adaptive**: RTT/错率自动推算 13 项性能参数 · 用户零感知
- **反转曝光**: package.json 只保留 `wam.autoRotate` · 47 配置项内化

### v17.10 autoUpdate · 太上不知有之 (2026-04-14)

- **autoUpdate 自动推送新版本**: SMB / HTTPS 源 · 原子 rename · 自动备份 · 默认静默

### v17.5-v17.9 Devin 身份迁移 · Firebase fallback (2026-04-12~13)

- **Devin 三通道登录**: `/_devin-auth/password/login` · `sessionToken` 格式 `devin-session-token$<JWT>`
- **Firebase→Devin fallback**: `INVALID_LOGIN_CREDENTIALS` 自动探测 Devin · 死号复活
- **wam.clearBlacklist / wam.testDevinSwitch**: 一键复活 + 链路诊断

### v17.4 输入净化 · 指纹日志 (2026-04-12)

- **_sanitizeCredential**: NFKC 归一 · 全角→半角 · 零宽剔除 · 纯 ASCII 快路径零开销
- **_hexFingerprint**: 失败时首尾 12 字节 hex dump · 精确溯源 IME 污染

---

## v17.3.0 — 反者道之动·v10.2额度根因修复 (2026-04-11)

### 根因: Proto3零值省略导致Weekly镜像反转

Proto field 14/15 = `remainingPercent`（逆向实证）。Proto3零值省略 → field 15 absent = weekly剩余0% = 耗尽。旧版v9.3的镜像逻辑在 `dReset===wReset` 时将daily镜像到weekly → **W0被误读为W100 → 永不切号 → Quota Exhausted**。

### 核心修复 (`wam-bundle/extension.js`)

| 函数 | 修改 |
|------|------|
| `_extractQuotaFields` | 删除全部5分支镜像逻辑 → 2分支, absent field 15 = 0 |
| `getHealth` | weeklyUnknown兜底从 `daily镜像` → `0(耗尽)`, 绝不镜像 |
| `_updateAccountUsage` | weekly始终0-100, 兜底逻辑仅防极端情况 |
| monitor/scan snapWeekly | 注释同步v10.2 |

### 数据流验证

```
API → proto field14=100, field15=absent(proto3零值省略)
  → _extractQuotaFields: daily=100, weekly=0 ✓ (旧版: weekly=100 ✗)
  → getHealth: D100 W0 ✓
  → 官方: Daily usage=0%, Weekly usage=100% → 对应关系正确
```

### 实测证据

```
D100 W34, D100 W37, D100 W100 — D/W完全独立, 非镜像 ✓
scan: batch[70+10] 8ok 6changed — 正常运转 ✓
Errors: 0 ✓
```

### 设计原则

- **不做假设性镜像** — 若API真的D/W统一, field15会显式=field14
- **无迁移逻辑** — W=D=100是合法新账号, API scan自然刷新
- **防御性保留** — `weeklyUnknown` flag + `result.weekly >= 0` guard仍在

---

## v17.1.0 — 去芜留菁·47常量零残留 (2026-04-11)

### 核心: 剩余19个硬编码常量+魔法数字全部getter化

v17.0完成28个常量动态化后，审视发现仍有16个行为常量以`const`硬编码 + 3处魔法数字散落代码中。v17.1彻底清除，实现**47常量零残留**。

| 分类 | 常量 | getter | 配置键 |
|------|------|--------|--------|
| 注入冷却 | `INJECT_FAIL_COOLDOWN` | `_getInjectFailCooldown()` | `wam.injectFailCooldownMs` |
| 清理间隔 | `PURGE_INTERVAL_MS` | `_getPurgeIntervalMs()` | `wam.purgeIntervalMs` |
| 代理缓存 | `PROXY_CACHE_TTL` | `_getProxyCacheTtl()` | `wam.proxyCacheTtlMs` |
| 代理失败 | `PROXY_FAIL_TTL` | `_getProxyFailTtl()` | `wam.proxyFailTtlMs` |
| Claims缓存 | `CLAIMS_CACHE_TTL` | `_getClaimsCacheTtl()` | `wam.claimsCacheTtlMs` |
| 额度节流 | `QUOTA_MIN_INTERVAL` | `_getQuotaMinInterval()` | `wam.quotaMinIntervalMs` |
| 429退避 | `QUOTA_429_BACKOFF` | `_getQuota429Backoff()` | `wam.quota429BackoffMs` |
| Relay IP | `RELAY_IP_TTL` | `_getRelayIpTtl()` | `wam.relayIpTtlMs` |
| Pool冲刺 | `TOKEN_POOL_BURST_MS` | `_getTokenPoolBurstMs()` | `wam.tokenPool.burstMs` |
| Pool巡航 | `TOKEN_POOL_CRUISE_MS` | `_getTokenPoolCruiseMs()` | `wam.tokenPool.cruiseMs` |
| Pool冲刺时长 | `TOKEN_POOL_BURST_DURATION` | `_getTokenPoolBurstDuration()` | `wam.tokenPool.burstDurationMs` |
| Pool续期边距 | `TOKEN_POOL_MARGIN` | `_getTokenPoolMargin()` | `wam.tokenPool.marginMs` |
| 冲刺并行 | `POOL_PARALLEL_BURST` | `_getPoolParallelBurst()` | `wam.tokenPool.parallelBurst` |
| 巡航并行 | `POOL_PARALLEL_CRUISE` | `_getPoolParallelCruise()` | `wam.tokenPool.parallelCruise` |
| 拉黑阈值 | `POOL_TEMP_BAN_THRESHOLD` | `_getPoolTempBanThreshold()` | `wam.tokenPool.tempBanThreshold` |
| 拉黑时长 | `POOL_TEMP_BAN_DURATION` | `_getPoolTempBanDuration()` | `wam.tokenPool.tempBanDurationMs` |
| 切号冷却 | `15000` (magic) | `_getSwitchCooldownMs()` | `wam.switchCooldownMs` |
| 限速冷却 | `10000` (magic) | `_getRateLimitCooldownMs()` | `wam.rateLimitCooldownMs` |
| 干旱缓存 | `10000` (magic) | `_getDroughtCacheTtlMs()` | `wam.droughtCacheTtlMs` |

### 同时修复

- **文件头版本注释**: `v16.0` → `v17.1`
- **`package.json`**: 新增19个配置属性声明 (两份package.json同步)
- **`const UPPER_CASE = number`**: 零残留 (`grep` 验证通过)

---

## v17.0.0 — 道法自然·零硬编码·动态配置·真正实现彻底的适配 (2026-04-11)

### 核心突破: 从根本底层去除一切硬编码

**道可道非常道** — 审视当前插件最新成果，锚定本源，从根本底层去除各个无意义确定性编码。真正实现彻底的适配所有用户电脑环境、所有Windsurf环境、所有代理环境。

### 动态配置层 (28个常量 → getter函数)

| 分类 | 常量数 | 动态化方式 | 配置入口 |
|------|--------|-----------|---------|
| 产品名/数据目录 | 2 | `_detectProductName()` / `_resolveDataDir()` | `wam.productName` / `wam.dataDir` |
| Firebase认证 | 2 | `_getFirebaseKeys()` / `_getFirebaseReferer()` | `wam.firebase.extraKeys` / `wam.firebase.referer` |
| API端点 | 1 | `_getOfficialPlanStatusUrls()` | `wam.officialEndpoints` |
| 注入命令 | 1 | `_getInjectCommands()` | `wam.injectCommands` |
| Relay中继 | 1 | `_getRelayHost()` — 占位符自动禁用 | `wam.relayHost` |
| 代理端口 | 2 | `_getFallbackScanPorts()` / `_getGatewayPorts()` | `wam.proxy.extraPorts` / `wam.proxy.extraGatewayPorts` |
| 时序/阈值 | 14 | `_getMonitorFastMs()` ... `_getInstanceDeadMs()` | `wam.monitorIntervalMs` 等 |
| 版本号 | 1 | `WAM_VERSION` 一处定义 | — |
| 账号文件名 | 1 | `${PRODUCT_NAME}-login-accounts.json` | 自动 |
| 配置读取 | 1 | `_cfg(key, default)` — 修复0/false边界 | — |

### 跨平台自适应

- **Windows**: `%APPDATA%/{ProductName}` → Windsurf/Code 候选
- **macOS**: `~/Library/Application Support/{ProductName}` → 候选链
- **Linux**: `~/.config/{ProductName}` → 候选链
- 自动检测第一个存在的目录，兼容自定义安装路径

### Bug修复

- **`_cfg()` 0/false边界** — 用户设置 `changeThreshold: 0` 或 `autoRotate: false` 不会被忽略
- **Relay通道空指针** — relay未配置时通道3/4 gracefully skip而非崩溃
- **AccountStore文件名** — 动态化 `${productName}-login-accounts.json`，向后兼容旧文件

### 验证 (91 PASS / 0 FAIL)

| 测试类别 | 项目数 | 结果 |
|----------|--------|------|
| 模块加载/语法 | 3 | ✓ |
| 配置函数存在性 | 17 | ✓ |
| 旧常量根除 | 12 | ✓ |
| 跨平台路径 | 3 | ✓ |
| Relay边界条件 | 9 | ✓ |
| 端口合并逻辑 | 7 | ✓ |
| Firebase配置 | 8 | ✓ |
| 代理环境探测 | 2 | ✓ |
| TCP端口探测 | 1 | ✓ |
| LAN网关检测 | 1 | ✓ |
| AccountStore动态化 | 8 | ✓ |
| _cfg()边界条件 | 8 | ✓ |
| Windsurf安装验证 | 1 | ✓ |
| CONNECT隧道 | 2 | ✓ |
| Firebase连通性 | 1 | ✓ |
| Official API | 4 | ✓ |
| 悬挂引用检查 | 4 | ✓ |

### 当前机器实测结果

- 代理 `127.0.0.1:7890` CONNECT → Firebase OK
- `server.codeium.com` 401/941ms ✓
- `web-backend.windsurf.com` 401/474ms ✓
- `register.windsurf.com` 404/860ms ✓

---

## v16.1 — 共享Promise修复并发竞争 (2026-04-10)

- **消灭startup burst竞争** — ch2/ch5 no_proxy 共享Promise

---

## v16.0 — 万法归宗·从根本去除端口依赖 (2026-04-10)

- **统一代理描述符** — `_detectProxy()` 返回 `{host, port, source}` 或 `null`
- **消灭双缓存** — `_proxyPortCache` + `_proxyHostCache` → 单一 `_proxyCache`
- **系统代理优先** — env/VS Code/Windows Registry → LAN网关 → localhost扫描(末路兜底)

---

## v15.0.0 — 万法归宗·道法自然·Chromium原生网络桥 (2026-04-10)

### 核心突破

- **Chromium原生网络桥** — Webview fetch()走Chromium渲染进程，与Windsurf官方登录完全同一网络路径
- **道法自然** — 只要用户能官方登录Windsurf，此通道必然可达Firebase/Codeium，不受任何网络条件制约
- **`_nativeFetch()`** — 通过sidebar webview发送请求，自动继承系统代理/DNS/TLS
- **`_handleFetchResult()`** — webview→extension消息桥，支持JSON和Binary双模式
- **代理端口扩展** — PROXY_PORTS覆盖v2rayN/Clash/SSR等主流客户端全部常见端口
- **额度查询Chromium通道** — fetchAccountQuota新增native通道，5通道竞速(Chromium > 官方proxy > 官方direct > Relay IP > Relay proxy)

### 验证 (125 PASS / 0 FAIL)

| 子系统 | 测试数 | 结果 |
|--------|--------|------|
| Protobuf解析 | 15 | ✓ |
| 账号解析/评分 | 20 | ✓ |
| 实例协调 | 10 | ✓ |
| Bug修复验证 | 8 | ✓ |
| 网络/代理 | 12 | ✓ |
| 环境模拟 | 10 | ✓ |
| Token缓存 | 8 | ✓ |
| getBestIndex | 12 | ✓ |
| 注入系统 | 10 | ✓ |
| 真实账号(63) | 8 | ✓ |
| Chromium桥 | 12 | ✓ |

---

## v14.4.0 — 道法自然·专注本源 (2026-04-10)

- **仓库归一** — 专注切号助手本源，隔离所有非核心资源于本地
- **WAM引擎同步** — wam-bundle/ 同步至最新 v14.3 (sanitized)
- **安全审计** — relay域名/用户路径/API密钥全量脱敏

---

## v14.3.0 — 为道日损·零环境依赖·完善到底 (2026-04-10)

### 核心改进

- **p3无条件重试** — 无论code:0还是timeout，Phase3始终发新命令（成功率+50%），省掉外层retry 12s+
- **注入冷却3s** — `INJECT_FAIL_COOLDOWN` 5s→3s，p3已内含充分等待
- **连续失败命令重置** — `_consecutiveInjectFails >= 3` 自动清除 `_workingInjectCmd`，Phase4重新探测
- **热部署安全激活** — `restartExtensionHost` 替代 `reloadWindow`，仅重启扩展进程，对话/编辑器/终端全部保留
- **热部署信号机制** — `_reload_signal` + `_reload_ready` 文件协议，外部部署脚本无感触发

### 零环境依赖验证（全链路）

| 子系统 | 机制 | 不受制约 |
|--------|------|----------|
| 代理检测 | `_getSystemProxy()` 读env/VS Code + `_detectProxy()` 并行TCP+CONNECT | 网络环境 |
| Firebase登录 | key×channel全并行竞速 + proxy/direct双通道 + Phase2刷新重试 | 网络环境 |
| 注入 | 3命令候选 + 4阶段递进 + 连续失败重置 | Windsurf版本 |
| 额度查询 | 3官方端点 + 4通道竞速 + DoH双路径DNS | 服务器可用性 |
| Token池 | 冲刺3并行→巡航1串行 + 临时拉黑 + 持久化 | 冷启动速度 |
| 依赖 | 仅Node.js标准库(vscode/crypto/https/http/net/fs/path/os) | 电脑环境 |

---

## v14.2.0 — 万法归宗·根治全部环境依赖 (2026-06-17)

### 架构重构（v10.x → v14.2）

此版本是从v10.0.5到v14.2的**完整架构升级**，包含以下核心变更：

#### 代理系统重构
- **`_getSystemProxy()`** — 自动读取 `HTTPS_PROXY`/`HTTP_PROXY`/`ALL_PROXY`/VS Code `http.proxy` 配置
- **`_detectProxy()`** — 系统代理优先→本地常见端口扫描，并行TCP探测+CONNECT功能验证
- **`_proxyHostCache`** — 动态跟踪验证通过的代理Host（不再硬编码`127.0.0.1`）
- **`_invalidateProxyCache()`** — 请求失败时强制刷新代理缓存，自动恢复

#### 注入系统重构
- **`INJECT_COMMANDS`** — 3命令候选列表，版本自适应（`provideAuthTokenToAuthProvider` / `codeium.provideAuthToken` / `windsurf.provideAuthToken`）
- **`_workingInjectCmd`** — 缓存验证成功的命令，避免每次重新探测
- **4阶段注入** — Phase1快探3s → Phase2收割4s → Phase3无条件重试5s → Phase4备选命令5s
- **根治hung promise** — Phase3发新命令逃逸hung provider，不复用卡死的Promise
- **连续失败重置** — `_consecutiveInjectFails >= 3` 时自动清除命令缓存，允许重新探测

#### Firebase认证精简
- **单key模式** — 移除多key轮转复杂度，单Firebase key + `Referer: https://windsurf.com/` 头
- **Token缓存持久化** — `_token_cache.json` 跨重启保留，冷启动无需重新登录

#### 额度查询增强
- **3官方端点** — `server.codeium.com` + `web-backend.windsurf.com` + `register.windsurf.com`
- **4通道竞速** — 官方proxy / 官方direct / 中继proxy / 中继direct，Promise.any最快返回

#### 新增功能
- **`wam.selfTest`** — 一键自诊断：网络连通性/代理验证/端点可达性/注入命令测试
- **热部署支持** — `_reload_signal` 机制，无需重启即可更新扩展

---

## v10.0.5 — 首选账号同步修复 (2026-04-09)

### 根因

Windsurf v1.108+ 的 `handleAuthToken` 函数创建新 session 但**不调用 `updateAccountPreference`**，导致 `state.vscdb` 中 `codeium.windsurf-windsurf_auth` 仍指向旧账号。Cascade agent 基于首选账号获取 apiKey，因此切号后实际仍使用旧账号的额度。

### 修复

- **`_syncPreferredAccount()`** — 注入成功后使用 `@vscode/sqlite3`（Windsurf 内置）直接更新 `state.vscdb` 的首选账号键
- **双路径覆盖** — `switchToAccount` 和 `fileWatcher` 两个注入入口均在成功后调用
- **优雅降级** — 若 `@vscode/sqlite3` 不可用则跳过，不影响现有功能
- **五感原则进化** — 不再禁止写 `state.vscdb`，因为这是完成账号切换的必要操作

---

## v10.0.4 — 审计修复5项底层bug (2026-04-09)

### 修复清单

#### 1. [CRITICAL] 版本号不一致统一
所有用户可见版本号统一为 v10.0.4:
- header注释: v10.1.0 → v10.0.4
- activate日志: v10.1.1 → v10.0.4
- 状态栏tooltip(3处): v10.0.3 → v10.0.4
- status命令: v10.0.3 → v10.0.4

#### 2. [BUG] getPoolStats() pwCount计数错误
- `pwCount++` 在 `if (!a.password) continue` **之前**执行
- 导致统计所有账号数量而非有密码账号数量
- 修复: 移动 `pwCount++` 到密码检查之后

#### 3-5. [BUG] DoH DNS解析请求缺少 agent:false
3个DoH HTTP/HTTPS请求未设置 `agent: false`:
- DoH直连HTTPS请求 (Cloudflare/Google DNS)
- DoH via proxy的CONNECT请求
- DoH via proxy的内层HTTPS请求

**影响**: DNS请求被VSCode全局proxy agent劫持, 可能导致DNS解析循环依赖, 违反v10.0.4 agent隔离设计原则

---

## v10.0.3 — 冷却时间优化·Force Bypass·多Key轮转 (2026-04-08)

- 冷却时间从120秒降至15秒
- Force bypass cooldown for manual/panic switches
- Token预热与多Key智能轮转
- CONNECT握手验证代理 (排除SOCKS-only端口)
- 四层代理发现引擎

## v10.0.0 — 根治公网用户额度获取失败 (2026-04-08)

- 多端点并行竞速 (Promise.any)
- DNS解析三层fallback
- 三层缓存降级
- 全局限流协调
- 冷启动修复
