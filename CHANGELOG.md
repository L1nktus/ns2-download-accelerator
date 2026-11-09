# 更新记录 / Changelog

## v1.5.0 — 2026-11-09

### 中文

- App 使用原生本地状态读取和目录变更通知，取代正常定时刷新中的 Python 和系统查询子进程。
- 空闲检查约 15 秒，活动游戏下载约 2 秒；只在状态变化时发布界面和菜单更新。
- worker 的状态心跳为空闲 15 秒 / 活动 2 秒；监听与游戏连接变化立即触发更新。supervisor 稳定状态每 15 秒保活，恢复策略仍约每 5 秒检查，停服倒计时每秒检查。
- 保留 PID、时间新鲜度、接口地址和监听状态校验，避免旧快照让停止的服务显示为开启。
- 保留 100 KB/s、60 秒低速、15 秒停服、180 秒观察默认值，以及两次/滚动 30 分钟预算、手动停止取消重启和原有 VPN/电源边界。
- 验证：43 项后端测试、18 项原生快照检查、约 15 秒隔离停服与取消恢复测试；原生 App 构建和签名检查。
- 空闲观察：45 秒样本中三个常驻进程合计约 0.07% 单核 CPU；30 秒内两份状态文件各更新约两次。无真实游戏下载。不同锁屏状态、旧版短暂查询子进程未计入等因素限制了前后比较；不代表整机节电比例或续航提升。

### English

- Replaced normal periodic Python/system-tool status queries with native local snapshot reads and directory notifications.
- Poll about every 15 seconds while idle and every 2 seconds during active game downloads. Publish UI/menu changes only when state changes.
- Worker heartbeat: 15 seconds idle / 2 seconds active, with immediate listener/game-connection events. Supervisor heartbeat: 15 seconds for stable state; recovery policy remains on a roughly 5-second cadence and the stop countdown on a 1-second cadence.
- Retained PID, freshness, interface-address, and listener-health validation to reject stale snapshots after manual stop or worker replacement.
- Preserved default 100 KB/s / 60-second low speed / 15-second stop / 180-second observation, two attempts per rolling 30 minutes, manual-stop cancellation, and existing VPN/power-management boundaries.
- Validation: 43 backend tests, 18 native snapshot checks, an isolated roughly 15-second stop/restart and cancellation test, plus native app build/signature checks.
- Idle observation: about 0.07% of one CPU core combined across three resident processes in a 45-second sample; each state file updated about twice in 30 seconds. No real game download was active. Different screen-lock states and excluded old transient query helpers limit before/after comparisons. These figures do not measure whole-system power savings or battery-life improvements.

## v1.4.1

首次公开源码；菜单栏服务状态与隐藏 Dock 的窗口行为修复。见 Git 标签及历史技术记录。

Initial public source snapshot, including menu service-state and Dock/window visibility fixes. See the version tag and historical technical notes.
