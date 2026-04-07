# Windsurf Assistant

> 道生一，一生二，二生三，三生万物。
> 万物负阴而抱阳，冲气以为和。

**Windsurf 无感切号引擎 — 七子工程全链路体系**

无感切号 · 号池管理端 · 智能轮转 · 自动注册 · 云端号池 · 设备指纹重置 · 补丁系统 · 零中断

---

## 项目架构

```text
windsurf-assistant/
├── pool-admin/                   # ⚡ 号池管理端 v2.0 (旗舰)
│   ├── dist/src/
│   │   ├── extension.js          # 核心入口 (226KB, obfuscated)
│   │   ├── poolManager.js        # 号池管理器 (238KB)
│   │   ├── adminPanel.js         # 管理面板UI (169KB)
│   │   └── lanGuard.js           # LAN安全守护 (120KB)
│   ├── dist/media/
│   │   ├── admin.js              # 前端管理界面 (826KB)
│   │   ├── panel.html            # 面板HTML
│   │   ├── dashboard.html        # Dashboard
│   │   └── icon.svg              # 图标
│   ├── dist/manifest.json        # 构建清单 + 完整性校验
│   └── package.json              # 扩展描述
├── wam-bundle/                   # ⚡ WAM v7.2 源码 (可读)
│   ├── extension.js              # 完整切号引擎源码 (3000行)
│   ├── package.json              # 扩展清单
│   └── purge_and_isolate.py      # 清理隔离工具
├── dist/                         # VSIX v9.0 (obfuscated)
│   └── extension.js              # Windsurf Login Helper 核心
├── engine/                       # 道引擎 (WAM Engine) — 23 files
│   ├── wam_engine.py             # 无感账号管理引擎 (主控)
│   ├── dao_engine.py             # 道引擎：认证链+积分+轮转
│   ├── pool_engine.py            # 号池引擎：多账号调度
│   ├── pool_proxy.py             # 号池代理：远程中继
│   ├── hot_guardian.py           # 热守护：监控+自动恢复
│   ├── hot_patch.py              # 热补丁：运行时注入
│   ├── batch_harvest.py          # 批量收割：积分采集
│   ├── patch_continue_bypass.py  # P1-P4: maxGen+AutoContinue
│   ├── patch_rate_limit_bypass.py # P6-P8: Fail-Open+UI解锁
│   ├── telemetry_reset.py        # 设备指纹重置→新Trial
│   ├── wam_dashboard.html        # WAM 管理面板
│   └── _*                        # 测试+探针+安全检查
├── pipeline/                     # 注册管线 — 11 files
│   ├── _pipeline_v3.py           # 注册管线 v3
│   ├── _gmail_alias_engine.py    # Gmail+alias 引擎
│   ├── _universal_engine.py      # 通用注册引擎
│   ├── _yahoo_auto.py            # Yahoo 自动注册
│   └── turnstilePatch/           # Cloudflare Turnstile 绕过
├── cloud-pool/                   # 云端号池 — 21 files
│   ├── cloud_pool_server.py      # 云端号池服务 v3.1
│   ├── cloud_pool.html           # 管理面板
│   ├── public.html               # 公共查询页
│   └── redeem.html               # 卡密兑换页
├── diagnostics/                  # 诊断工具 — 132 files
│   ├── windsurf_doctor.py        # 全能诊断器 (51KB)
│   ├── credit_toolkit.py         # 积分工具箱 (38KB)
│   ├── _rate_limit_guardian.py   # 限流守护 (29KB)
│   ├── _watchdog_wuwei.js        # 无为守护 (19KB)
│   └── _fix_* / _inject_* / _diag_* / _probe_*
├── research/                     # 研究脚本 — 170 files
│   ├── opus46_ultimate.py        # Opus46终极方案 (34KB)
│   ├── ws_backend.py             # WS后端逆向 (30KB)
│   ├── cascade_*.py / find_*.py / grpc*.py
│   └── proto_*.py                # Protobuf 解析
├── tools/                        # 工具集 — 14 files
│   ├── credit_toolkit.py         # 积分监控/委派/Dashboard
│   ├── ws_repatch.py             # 补丁系统 v4.0 (全静默)
│   ├── windsurf-multi.ps1        # 多实例管理
│   └── _complete_model_matrix.json # 102模型完整矩阵
├── scripts/                      # 一键脚本
│   ├── →一键万法.cmd              # 统一入口
│   └── 一键万法.py                # Python入口
├── docs/                         # 逆向文档
│   ├── DEEP_CREDIT_MECHANISM_v8.md  # 六层计费架构
│   └── ...                       # 限流根因 / 配额系统
├── media/icon.svg
├── package.json                  # VSIX v9.0 扩展清单
├── .vsixmanifest
└── LICENSE                       # MIT
```

## 七子工程

### 1. 号池管理端 v2.0 (pool-admin/) ⚡ 旗舰

截图中的完整管理面板，四模块架构：

- **extension.js**: 核心入口 — 激活/命令注册/Webview生命周期
- **poolManager.js**: 号池管理器 — 55+账号调度/Firebase额度查询/智能轮转/预判切换/UFEF过期优先/多实例锁
- **adminPanel.js**: 管理面板 — 完整WebView UI/日额度·周额度·有效期/状态栏/操作交互
- **lanGuard.js**: LAN安全守护 — 局域网Hub通信/设备发现/远程切号/多机协调

功能亮点：
- **智能轮转**: 查全部·切最优，一键完成
- **验证清理**: 批量剔除过期/无效/Free计划账号
- **有效期刷新**: 批量获取planEnd，标记Trial天数
- **WAM/官方双模式**: 一键切换，可完全回退官方登录
- **管理面板**: 暗色主题WebView，实时状态展示

### 2. WAM 切号引擎 v7.2 (wam-bundle/)

完整可读源码 (3000行JS)，核心切号逻辑：

- **重置时间感知**: Daily 4PM GMT+8 / Weekly Sunday 自动感知
- **智能等待**: 重置将至时等待而非切号
- **突发追踪**: 用户连续发消息时1.5秒极速监测
- **预判切换**: < 25% 时预选候选，< 5% 时自动切换
- **使用中检测**: 快标记·慢清除·防误判
- **多实例协调**: 心跳锁机制，避免多窗口冲突
- **Claude可用性门控**: 自动验证新账号，Free/过期自动剔除

### 3. VSIX v9.0 (dist/)

混淆打包的生产版本，安装即用。

### 4. 道引擎 (engine/)

Python 后端核心，管理认证链全流程：

- **wam_engine.py**: 主控引擎，协调所有模块
- **dao_engine.py**: Firebase认证 + Protobuf积分解析 + Token注入
- **pool_engine.py**: 多账号调度，余额排序，自动轮转
- **hot_guardian.py**: 7×24守护，异常自动恢复
- **补丁系统**: Continue绕过 + 限流Fail-Open + 设备指纹重置

### 5. 注册管线 (pipeline/)

自动注册 Windsurf 账号：Gmail+alias · Yahoo自动 · Turnstile绕过

### 6. 云端号池 (cloud-pool/)

号池管理服务 v3.1：账号统一管理 · Auth blob加密存储 · 远程热切换 · 并发安全

### 7. 文档 (docs/)

源码级逆向知识库：六层计费架构 · 限流根因 · 配额系统 · 全链路分析

## 安装

### Pool Admin (推荐)

```bash
# 将 pool-admin/dist/ + pool-admin/package.json 复制到:
# ~/.windsurf/extensions/dao.pool-admin-2.0.0/
```

### WAM 引擎 (轻量)

```bash
# 将 wam-bundle/ 下 extension.js + package.json 复制到:
# ~/.windsurf/extensions/local.wam-7.2.1/
# 需设置环境变量: WAM_RELAY_HOST=your-relay-host
```

### 后端部署

```bash
python engine/wam_engine.py        # 道引擎
python cloud-pool/cloud_pool_server.py  # 云端号池
python scripts/一键万法.py          # 一键全部启动
```

## 安全说明

- 所有账号数据、密钥、卡密、日志均已 `.gitignore` 排除
- WAM 源码中的 relay host 已替换为环境变量
- Pool Admin 使用混淆保护，manifest.json 含完整性校验哈希
- Deploy 脚本 (含本地IP/用户名) 不入库

## 许可证

MIT License © 2026 dao
