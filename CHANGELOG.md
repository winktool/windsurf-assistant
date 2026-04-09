# Changelog

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
