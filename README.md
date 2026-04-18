# WAM · Windsurf Account Manager

> 道可道，非常道。名可名，非常名。道法自然 · 无为而无不为。

**WAM v17.18** — 主仓归宗 · 公网天网自升级 · 秒切引擎 · Devin 预热 · 太极生万物

---

## 它做什么

安装后，Windsurf 编码时额度耗尽会 **自动无感切换到下一个可用账号**，编码体验零中断。

> **零配置** — 装完即用，所有参数自动按网络实测调节。
> **公网自升级** — 后续新版本自动拉取，无需手动更新。

### v17.18 主仓归宗 · 公网天网自升级

- **jsDelivr 四镜像 fallback**：`cdn → fastly → gcore → testingcf` 任一通即成功，四镜像至少一通概率 **99.99%**
- **DNS 污染自规避**：`cdn.jsdelivr.net` 被污染时自动切 fastly / gcore / testingcf
- **autoDiscover 默认开启**：`wam.autoUpdate.autoDiscover = true`，`source` 未配置即走公开主仓
- **零隐私公开主仓**：默认源 `https://cdn.jsdelivr.net/gh/zhouyoukang/windsurf-assistant@main/wam-bundle/`
- **sanitize 全绿**：本地 IP / 用户名 / SMB 示例全 placeholder 化

### v17.16 秒切引擎 · Devin sessionToken 预热

- **prewarm Devin**：`_prewarmCandidateToken` 异步填充 sessionToken，切号永远 cache HIT
- **秒切**：冷启 Devin 4300ms → 预热后 **1300ms**（节省 3000ms · 接近秒切门槛 < 1500ms）
- **token pool 含 Devin**：`_tokenPoolTick` 批处理并行预热，Cruise 周期 50min 刷新

### v17.11 自适应运行时

- **_adaptive**：实测 RTT/错率自动推算 13 项性能参数，用户零感知
- **反转曝光**：package.json 只保留 `wam.autoRotate`，47 配置项内化成 `_cfg` override

### v17.3 核心修复: v10.2额度显示反转根因修复

**Proto3零值省略** → field 15 absent = weekly剩余0% = 耗尽。旧版镜像逻辑将daily→weekly，W0被误读为W100 → 永不切号。v10.2彻底消除镜像。

| 修复点 | 旧逻辑 | 新逻辑 |
|--------|--------|--------|
| `_extractQuotaFields` | 5分支镜像(dReset===wReset→镜像daily) | 2分支: 有值取值, absent=0 |
| `getHealth` | weeklyUnknown→镜像daily | weeklyUnknown→兜底=0(耗尽) |
| `_updateAccountUsage` | weekly可能为-1 | weekly始终0-100 |

### v17.0-17.2: 零硬编码·动态配置

47个硬编码常量全部getter化，通过VS Code settings (`wam.*`) 可覆盖一切参数。跨平台自适应 Win/Mac/Linux。

| 维度 | 机制 | 配置入口 |
|------|------|------|
| **产品名** | `_detectProductName()` 自动识别 Windsurf/Cursor/Code | `wam.productName` |
| **数据目录** | `_resolveDataDir()` Win/Mac/Linux 候选链 | `wam.dataDir` |
| **网络环境** | `_getSystemProxy()` + `_detectProxy()` 并行TCP+CONNECT | `wam.proxy.extraPorts` |
| **Firebase** | `_getFirebaseKeys()` 可追加key | `wam.firebase.extraKeys` |
| **API端点** | `_getOfficialPlanStatusUrls()` 可追加 | `wam.officialEndpoints` |
| **注入命令** | `_getInjectCommands()` 3命令候选可覆盖 | `wam.injectCommands` |
| **中继** | `_getRelayHost()` 占位符自动禁用 | `wam.relayHost` |
| **时序阈值** | 33个 getter (monitor/scan/burst/cooldown/pool/proxy等) | `wam.monitorIntervalMs` 等 |

### 核心能力

- **动态配置层** — 47个常量全部getter化，`_cfg(key, default)` 读 VS Code settings，0/false作为合法值
- **跨平台自适应** — Windows `%APPDATA%` / macOS `~/Library` / Linux `~/.config` 自动检测
- **4阶段递进注入** — P1快探3s → P2收割4s → P3无条件重试4s → P4备选命令5s
- **Token活水池** — 后台持续预热所有账号Token，切号必然cache HIT，切换<3s
- **消息锚定切号** — 实时监测额度波动，波动即切号，确保下条消息用新号
- **多源额度竞速** — 5通道并行(Chromium/官方proxy/官方direct/中继IP/中继proxy)
- **3官方端点** — `server.codeium.com` + `web-backend.windsurf.com` + `register.windsurf.com`
- **五感模式** — 绝不logout、绝不杀agent、不重启、不丢上下文
- **Weekly干旱模式** — 全池W耗尽自动切入Daily模式
- **热部署** — `restartExtensionHost` 仅重启扩展，不中断对话
- **自诊断** — `wam.selfTest` 一键全链路检测

## 安装

1. 从 [Releases](https://github.com/zhouyoukang/windsurf-assistant/releases) 下载最新 `.vsix`
2. Windsurf 中 `Ctrl+Shift+P` → `Extensions: Install from VSIX...` → 选择下载的文件
3. 重启 Windsurf，侧边栏出现「无感切号·账号管理」面板

## 可选配置 (Settings JSON)

所有配置项均有合理默认值，**零配置即可使用**。高级用户可通过 `settings.json` 微调：

```jsonc
{
  "wam.autoRotate": true,              // 自动切号
  "wam.autoSwitchThreshold": 5,        // 额度<5%切号
  "wam.predictiveThreshold": 25,       // 额度<25%预热Token
  "wam.monitorIntervalMs": 3000,       // 监测间隔
  "wam.proxy.extraPorts": [9999],      // 追加代理端口
  "wam.relayHost": "",                 // 中继域名(留空禁用)
  "wam.firebase.extraKeys": [],        // 追加Firebase key
  "wam.officialEndpoints": []          // 追加API端点
}
```

## 源码

```
wam-bundle/
├── extension.js    # 切号引擎核心 (~6100行, 纯Node.js标准库)
├── package.json    # VS Code 扩展清单
└── media/
    └── icon.svg    # 侧边栏图标
```

## 命令列表

| 命令 | 说明 |
|------|------|
| `WAM: 管理面板` | 打开中央管理面板 |
| `WAM: 切换账号` | 手动选择账号切换 |
| `WAM: 智能轮转` | 自动选择最优账号切换 |
| `WAM: 紧急切换` | 无条件切换（不跳过使用中账号） |
| `WAM: 验证清理` | 批量验证并剔除无效/过期账号 |
| `WAM: 刷新有效期` | 批量获取缺失的planEnd |
| `WAM: 自诊断` | 一键检测网络/代理/端点/注入 |
| `WAM: 官方模式` | 暂停WAM，回退官方登录 |

## 许可证

MIT License © 2026
