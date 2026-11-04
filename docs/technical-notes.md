# NS2下载加速 · 源码 1.4.1

原生 macOS App + Python 标准库下载转发服务。交给 AI 接手时先读 **AI_HANDOFF.md**。

本包只含源码、测试、图标、构建工具和说明，不含分享者的本机配置、日志、账户、VPN 配置、证书或私钥。所有 `192.168.50.*` 都是示例或测试数据，使用者必须按自己的网络配置。

## 功能

- HTTP 和 HTTPS CONNECT 转发；阿里、腾讯 DoH 多解析源，按 TCP 建连延迟选择地址。
- 远程连接绑定指定物理网卡；没有代理节点、没有系统代理回退。
- App 提供启停、登录自启、CDN 择优、单设备限制、诊断、测速、日志。
- 无人值守低速恢复：默认低于 **100 KB/s 持续 60 秒 → 停服 15 秒 → 自动启动 → 最多观察 180 秒**，四项可修改。
- 观察期超时只记日志并保持服务开启，不因此再次重启。每 30 分钟最多恢复两次。
- 不管理亮屏或睡眠；关屏可工作，系统睡眠不能转发。

## 环境与构建

需要 macOS 14+、Xcode Command Line Tools、现有 Python 3.10+。没有第三方 Python 依赖。实际验证平台为 Apple Silicon；Intel 构建入口已提供，但未进行 Intel 实机验证。

先让 AI 检查真实网络接口、局域网网段和 Python 位置：

```sh
networksetup -listallhardwareports
ifconfig en0
command -v python3
python3 tools/test.py
```

根据实际网段构建，下面的网段只是示例：

```sh
python3 tools/build.py --interface en0 --subnet 192.168.50.0/24 --python /opt/homebrew/bin/python3
```

Intel/Homebrew 常见 Python 路径不同，应填实际路径。构建工具会把参数写入生成的 App，不改源码、不安装 App、不启动服务。产物在 `build/NS2下载加速.app`，采用本机临时签名。

确认参数后可把 App 拖入「应用程序」并打开。首次打开会在当前用户的 `Library/Application Support/NS2Proxy` 创建服务文件及配置；首次默认关闭登录自启。App 打开后自动启动服务，在 Switch 同一局域网连接的代理设置里填 App 显示的地址和端口（默认 8888），无需认证，DNS 保持自动。

**升级已有安装时要特别处理运行目录：** App 目前只复制缺失的后端文件，不会覆盖已有文件。先停止服务、退出旧 App，备份运行目录，再把新 App Resources 中的四个后端 `.py` 文件同步过去，保留个人 `config.json`，然后启动。不要同时运行两套同端口服务。

## 测试范围

```sh
python3 tools/test.py --integration
```

40 项单元/本地套接字测试；集成测试使用临时目录和本机测试端口，真实等待约 15 秒验证停服后重启及取消恢复，不访问真实 Switch。真实主机上手动重启服务改善低速、约两分钟内自动续传已获单设备使用者验证；自动策略的长期、跨设备效果尚未系统验证。不能保证每次重连都提速。

## 上游与许可

基于 https://github.com/kkksssmmmm-nuoyier/ns2-dns-accel ，基准提交 `23abadf786ea9613a2387810e5a807ef05a6cb3d`，保留 MIT License。主要衍生自其 `proxy_accel.py`；本包不提供其 DNS 安装脚本，避免误装端口 53 的另一套方案。

## 1.4 菜单与生命周期

菜单直接展示代理地址/端口/认证/DNS、实时游戏下载速度、一个勾/叉启停项、显示主界面、退出。总览也显示速度。有流量时菜单栏图标旁显示速度。UI 每约 2 秒从已转发字节增量计算速度，不发起测速，关闭自动恢复也照常统计；只识别现有游戏内容主机规则。过期样本、旧 worker、不监听时不展示旧速度。

退出 App 先等待已有启停/保存操作完成，再 bootout 转发及监控，成功后才退出；停止失败则保留 App 并显示错误。关窗口继续工作。登录自启使用 `com.local.ns2downloadapp.plist` 调用 open -g 打开 App，不再由旧的登录文件独立启动转发。运行服务仍是 com.local.ns2proxy，KeepAlive 只为运行中的监督进程。control.py 通过 NS2_APP_PATH 获知 App 位置；CLI 无该环境时回退到 /Applications/NS2下载加速.app，因此可从目标 App 内保存登录设置。

显示位置存储在 App UserDefaults：showMenuBar / showDock。至少一项为真，最后一个入口不能关闭，损坏的双 false 保存值启动时修复。从 1.4.1 开始不使用 SwiftUI App/Window/MenuBarExtra 管理生命周期，避免动态 Dock 激活策略被重复管理。Timer 加入 common mode，以便菜单打开时继续采样。不能增加任何电源或亮屏控制。

40 项 Python 测试通过，原生 Apple Silicon 构建和签名通过；显示选项两种组合、实际退出后服务与进程消失、重开启动均已验证。本轮网络不匹配原允许网段，未实测真实 Switch 下载读数；不能伪称完成。

## 1.4.1 界面修复

AppKit AppDelegate 持有 NSWindow（NSHostingView 渲染 SwiftUI 内容）和 NSStatusItem/NSMenu；Info.plist 设置 LSUIElement=true，启动后按保存的 showDock 设置 regular/accessory。切换模式前记录主窗口可见性，只恢复原本可见且未最小化的窗口，不能因隐藏 Dock 关闭主窗口，不能恢复窗口时擅自启用 Dock。

NSMenu 的服务项使用独立 ServiceMenuRow：左侧“下载服务”，最右侧 NSTextField 显示 ✅ 或 ❌，不依赖系统可忽略的 Label SF Symbol。支持点击、NSMenuItem action 和无障碍按钮。主窗口的“下载控制”应用菜单复用同一个菜单；状态栏标题与速度持续更新。只读状态项禁止点击。

在原 Mac 验证 regular 与 UIElement 模式实际切换、隐藏后窗口可继续切页、重启后偏好保留；菜单启停后的可访问状态分别为开启 ✅ 和关闭 ❌。未进行单位网络上的真实 Switch 下载测试。网络后端与恢复规则本次不改。
