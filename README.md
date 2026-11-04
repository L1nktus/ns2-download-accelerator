# NS2下载加速

在 Mac 上为 Nintendo Switch / Switch 2 提供局域网 HTTP 下载代理，包含原生菜单栏 App、实时下载速度和可调整的低速恢复功能。

## AI 开发与维护说明

本项目由账号持有人提出需求，主要使用 AI（Codex）协助编写、修改、测试和维护。**账号持有人不具备编程知识，无法独立审查全部实现。** 公开源码是为了保存成果、分享实际使用经验，并方便其他开发者与 AI 继续改进。

这是个人使用项目，不承诺专业人工审计、持续维护或所有设备兼容。文档区分自动测试、模拟验证和真实设备体验；欢迎通过 Issues 报告问题，通过 Pull Requests 提交改进建议。公开查看、下载或 Fork 不授予本仓库写入权限，修改建议需由维护方决定是否合并。

## 用来做什么

Switch 通过同一局域网中的 Mac 转发下载。远程连接绑定指定物理网卡，程序不配置代理节点，也不使用系统代理作为失败回退。VPN 的强制路由策略仍可能影响实际路径，换机器后应验证，不能仅凭网卡绑定就保证任何环境都绕过 VPN。

- 原生界面：服务开关、Switch 代理设置、连接记录和下载速度；菜单栏与 Dock 至少保留一个入口。
- 默认低速恢复：低于 **100 KB/s 持续 60 秒 → 停服 15 秒 → 自动启动 → 最多观察 180 秒**；四项均可修改。观察超时只记录结果，不因此重复断线；每 30 分钟最多恢复两次。
- 关闭窗口继续运行，退出 App 停止服务。熄屏可用，系统睡眠会中断转发；App 不管理亮屏或防睡眠。

## 构建与使用

需要 macOS 14+、Xcode Command Line Tools、Python 3.10+。已验证 Apple Silicon；Intel 构建入口未做实机验证。无第三方 Python 依赖。

先确认自己的物理接口、局域网网段和 Python 路径。以下网段仅为示例：

```sh
python3 tools/test.py
python3 tools/build.py --interface en0 --subnet 192.168.50.0/24 --python /opt/homebrew/bin/python3
```

将 `build/NS2下载加速.app` 复制到“应用程序”并打开。构建脚本只构建，不安装或修改网络。App 中确认网络可用后，在 Switch 手动代理中填显示的地址和端口（默认 8888），无需认证；DNS 保持自动。

升级旧版前先退出并备份运行目录。当前 App 只复制缺失的后端文件：需要将新 App 的四个后端 Python 文件同步到用户的 `Library/Application Support/NS2Proxy`，保留自己的 config.json，避免继续使用旧后端。

## 验证与限制

40 项后端单元/本地套接字测试通过，隔离恢复测试实测停服约 15 秒并验证取消重启。菜单状态和 Dock 隐藏已在原 Mac 检查。使用者实测重启服务后 Switch 可自行续传；最新界面版本的实际游戏下载读数、跨设备兼容和自动恢复的长期效果仍待更多验证。不能保证每次重连都提速。

需要更详细的构建与状态机说明：[AI 接手说明](AI_HANDOFF.md)、[技术记录](docs/technical-notes.md)、[维护说明](MAINTENANCE.md)。

## 来源与许可

基于 [kkksssmmmm-nuoyier/ns2-dns-accel](https://github.com/kkksssmmmm-nuoyier/ns2-dns-accel) 的 proxy_accel.py，基准提交 `23abadf786ea9613a2387810e5a807ef05a6cb3d`，保留原 [MIT License](LICENSE)。本项目没有使用上游 DNS 安装脚本。
