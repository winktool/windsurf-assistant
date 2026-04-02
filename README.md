# Windsurf Login Helper v9.0.0

Windsurf 无感号池引擎 — 主动容量探测·UFEF过期优先·即时切换·强制LS重启·真正热重置·速度感知·预防性切换·斜率预测·自动轮转·零中断

## 功能

- **智能轮转**: 自动检测积分余量，切换到最优账号
- **主动容量探测**: 预判账号容量，提前切换避免中断
- **UFEF过期优先**: 优先使用即将过期的账号
- **速度感知**: 根据消耗速率预测切换时机
- **斜率预测**: 基于历史趋势智能调度
- **零中断切换**: 强制LS重启 + 真正热重置，切换过程无感知
- **设备指纹轮转**: 切号时自动重置指纹，防关联
- **批量导入**: 支持多格式批量添加账号
- **紧急切换**: 限流时一键应急

## 安装

### 方式一：VSIX 安装

1. 下载 Release 中的 `.vsix` 文件
2. Windsurf 中 `Ctrl+Shift+P` → `Extensions: Install from VSIX...`
3. 选择下载的 `.vsix` 文件

### 方式二：手动安装

将整个目录复制到 `~/.windsurf/extensions/undefined_publisher.windsurf-login-helper-9.0.0/`

## 使用

安装后在活动栏出现 **Windsurf Login** 图标，点击打开管理面板。

### 添加账号

支持多种格式：

```text
email:password
email password
email----password
email              # 使用默认密码
```

### 命令面板

`Ctrl+Shift+P` 输入 `Windsurf Login` 查看所有命令：

- 切换账号 / 智能轮转
- 刷新积分 / 刷新全部
- 批量添加 / 导入账号
- 紧急切换 / 重置指纹
- 工作区配置

## 文件结构

```text
├── package.json          # 扩展清单
├── .vsixmanifest         # VSIX 包清单
├── LICENSE               # MIT 许可证
├── dist/
│   └── extension.js      # 打包后的扩展代码
└── media/
    └── icon.svg          # 活动栏图标
```

## 许可证

MIT License © 2026 dao
