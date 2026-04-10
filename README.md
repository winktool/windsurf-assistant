# WAM · Windsurf Account Manager

> 为道日损。损之又损，以至于无为。无为而无不为。

**WAM v14.3** — 零环境依赖 · 系统代理自适应 · 版本自适应注入 · 4阶段递进注入 · 多源额度竞速 · Token活水池 · 纯热替换

---

## 它做什么

安装后，Windsurf 编码时额度耗尽会 **自动无感切换到下一个可用账号**，编码体验零中断。

### 零环境依赖 — 从根本上不受任何环境制约

| 维度 | 机制 | 说明 |
|------|------|------|
| **网络环境** | `_getSystemProxy()` + `_detectProxy()` | 自动读取系统代理(env/VS Code) → 并行TCP+CONNECT验证 → 无代理时直连 |
| **电脑环境** | 仅依赖 Node.js 标准库 | `vscode`/`crypto`/`https`/`http`/`net`/`fs`/`path`/`os` — 无第三方依赖 |
| **Windsurf版本** | `INJECT_COMMANDS` 3命令候选 | `provideAuthTokenToAuthProvider` / `codeium.provideAuthToken` / `windsurf.provideAuthToken` 自动探测 |
| **代理软件** | 系统代理优先 + 常见端口扫描 | Clash/V2Ray/Trojan/SSR 任意一种均可，无代理也能直连 |
| **DNS环境** | DoH双路径 | Google DNS + Cloudflare DNS，绕过 Clash fake-ip |

### 核心能力

- **4阶段递进注入** — P1快探3s → P2收割4s → P3无条件重试(新命令)4s → P4备选命令5s，总超时≤12s
- **Token活水池** — 后台持续预热所有账号Token，任意切号必然cache HIT，切换<3s
- **消息锚定切号** — 实时监测额度波动，波动=有人发消息→立即切到新号，确保下条消息用新号
- **多源额度竞速** — 4通道并行(官方proxy/官方direct/中继IP/中继proxy)，`Promise.any`第一个成功即返回
- **3官方端点** — `server.codeium.com` + `web-backend.windsurf.com` + `register.windsurf.com`
- **五感模式** — 绝不logout、绝不杀agent、不重启、不丢上下文
- **Weekly干旱模式** — 全池W耗尽时自动切入只看Daily模式，避免无效轮转死循环
- **Rate-limit拦截** — 主动感知编辑器中的rate limit错误，自动触发无感切号
- **热部署** — `restartExtensionHost` 仅重启扩展进程，不中断对话
- **自诊断** — `wam.selfTest` 一键检测 proxy/firebase/official_api/relay/inject_cmd 全链路

### v14.3 核心改进

- **p3无条件重试** — 无论code:0还是timeout，Phase3始终发新命令给provider第二次机会（成功率+50%）
- **注入冷却3s** — 5s→3s，p3已内含充分等待，外层冷却缩短
- **连续失败重置** — `_consecutiveInjectFails >= 3` 自动清除命令缓存，允许Phase4重新探测
- **热部署安全激活** — `restartExtensionHost` 替代 `reloadWindow`，不中断对话

## 安装

1. 从 [Releases](https://github.com/zhouyoukang/windsurf-assistant/releases) 下载最新 `.vsix`
2. Windsurf 中 `Ctrl+Shift+P` → `Extensions: Install from VSIX...` → 选择下载的文件
3. 重启 Windsurf，侧边栏出现「无感切号·账号管理」面板

## 项目结构

```
windsurf-assistant/
├── wam-bundle/          # WAM 切号引擎 (VS Code 扩展源码)
│   ├── extension.js     #   核心引擎 (~5400行)
│   ├── package.json     #   扩展清单
│   └── media/icon.svg   #   侧边栏图标
├── monitor/             # Windsurf 实时监控仪表盘 (NEW)
│   ├── windsurf_hot_monitor.py  # gRPC逆向 + HTTP API + SSE推送
│   └── dashboard.html           # 万法归宗管理面板
├── engine/              # 道引擎 (Python后端)
├── pipeline/            # 注册管线 (账号铸造)
├── cloud-pool/          # 云端号池服务
├── pool-admin/          # 号池管理端
├── tools/               # 工具集 (额度检查/模型矩阵/快速切号)
├── scripts/             # 一键脚本 (部署/启动/注册)
└── diagnostics/         # 诊断工具 (医生/看门狗/安全存储)
```

## 命令列表

| 命令 | 说明 |
|------|------|
| `WAM: 管理面板` | 打开中央管理面板 |
| `WAM: 切换账号` | 手动选择账号切换（无cooldown限制） |
| `WAM: 智能轮转` | 自动选择最优账号切换 |
| `WAM: 紧急切换` | 无条件切换（不跳过使用中账号） |
| `WAM: 验证清理` | 批量验证并剔除无效/过期账号 |
| `WAM: 刷新有效期` | 批量获取缺失的planEnd |
| `WAM: 自诊断` | 一键检测网络/代理/端点/注入 |
| `WAM: 官方模式` | 暂停WAM，回退官方登录 |

## 许可证

MIT License © 2026
