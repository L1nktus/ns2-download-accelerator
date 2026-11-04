# AI 接手说明

## 先保留这些要求

这是给 Nintendo Switch / Switch 2 下载用的 Mac 局域网转发程序。界面名称使用「NS2下载加速」，不要加入“仅直连、无节点回退”等宣传标签。底层仍必须保持远程连接物理接口绑定，不自动使用用户的 VPN 节点。不能加入 caffeinate、电源断言、亮屏或防睡眠功能。睡眠由使用者自行管理。

恢复必须无人值守，不要求用户操作 Switch。已验证的办法是停止整个转发服务再启动，Switch 自行重试。不要把只新建一个远端 TCP 连接当作能续接旧 TLS 隧道：本程序不解密 HTTPS，不知道文件 URL/Range/密钥。

## 文件与进程

- `src/app/NS2Direct.swift`：原生窗口、菜单栏、配置表单；调用 control.py 的 JSON 接口。
- `src/backend/control.py`：配置校验、原子保存、launchd 启停、诊断。恢复参数热更新，网络参数变更会重启服务。
- `src/backend/supervisor.py`：launchd 主进程，管理转发子进程、停服倒计时、历史和恢复次数预算。必须在停服期间存活。
- `src/backend/proxy_accel.py`：实际监听及转发，DoH、候选 IP、访问限制、流量计数。计数发生在数据成功转给客户端之后。
- `src/backend/recovery.py`：可测试的纯状态机与参数校验。无套接字和系统进程操作。
- `tests/`：40 项自动测试及隔离停服集成测试。

`com.local.ns2proxy` 为每用户 launch agent。监控与转发分开，关闭控制窗口后继续工作；退出 App 会停止转发、监控并取消待执行恢复。重新打开 App 自动启动。首次安装前必须检查 bundle identifier/服务标签/端口是否与已有版本冲突。不要自动覆盖别的程序或使用者配置。

## 恢复判定

按同一个客户端的所有已识别游戏内容连接合计，不能逐连接判慢。当前通过主机名识别 `atum`、`atum-*`、`scsi-download`；登录、心跳、测速不计入。不是全任天堂下载域名清单，需要兼容新域名时用实际记录验证。

累计达到 1 MiB 后启用判定；每 5 秒采样，约 10 秒均速。无内容连接不触发；分块间短暂空档保留计时，空档约 15 秒后清除。阈值和持续时间变更会清除旧计时。四项默认 `100 KB/s / 60s / 15s / 180s`，KB 是十进制字节单位。

状态：idle → monitoring → pausing → waiting → checking。等待中看到足够游戏数据就提前检查，速度达阈值连续 30 秒记录恢复有效。**等待超时只记录未观察到续传并保留服务，绝不以超时本身反复重启。** 此后有游戏数据仍会重新监控。最多两次/滚动 30 分钟，预算持久化；达到上限继续转发。

运行状态在用户目录 `runtime/traffic.json` 和 `runtime/recovery.json`，原子替换。必须核对 worker PID、更新时间，忽略旧进程/过期计数。运行文件、日志不要打进分享包。

## 网络与证据边界

所有远程连接都经 `dial()`，用 Darwin `IP_BOUND_IF` 绑定真实 enN 接口；此处不能加不绑定接口的失败后备路径。DNS 用验证 TLS 的 DoH，不通过可能被 VPN 劫持的 UDP/53。不做 MITM，不安装根证书。

指定接口在普通路由下可绕过默认 VPN 路径；但 macOS VPN 的强制路由/全流量接管设置可能改变优先级。部署到另一台 Mac 后必须检查实际配置和路径，不可仅凭源 IP 就宣称绝不使用节点流量。不要为了让程序工作而擅自关闭或削弱 VPN。

App/后端仍有部署特定默认值：示例网段 `192.168.50.0/24`、接口 en0，以及源码里的 Homebrew Python 路径。`tools/build.py` 会按明确参数替换生成产物中的实际值，支持当前 CPU 架构；没有打包 Python，移动 Python 安装位置后需要重新配置。

先运行测试，再在目标 Mac 用联网检测、可信 HTTPS 和小样本下载验证。测速域名与实际游戏 CDN 不同；短测高速不证明游戏长期稳定，不能据此排除服务端/路线/程序问题。加密隧道也无法准确区分主机暂停、写入等待和真正卡住；不要把“写盘”当作已证实原因。

参考：Apple VPN 路由说明 https://developer.apple.com/documentation/networkextension/routing-your-vpn-network-traffic ，HTTP CONNECT https://www.rfc-editor.org/rfc/rfc9110.html#section-9.3.6 。

## 1.4 菜单与生命周期

菜单直接展示代理地址/端口/认证/DNS、实时游戏下载速度、一个勾/叉启停项、显示主界面、退出。总览也显示速度。有流量时菜单栏图标旁显示速度。UI 每约 2 秒从已转发字节增量计算速度，不发起测速，关闭自动恢复也照常统计；只识别现有游戏内容主机规则。过期样本、旧 worker、不监听时不展示旧速度。

退出 App 先等待已有启停/保存操作完成，再 bootout 转发及监控，成功后才退出；停止失败则保留 App 并显示错误。关窗口继续工作。登录自启使用 `com.local.ns2downloadapp.plist` 调用 open -g 打开 App，不再由旧的登录文件独立启动转发。运行服务仍是 com.local.ns2proxy，KeepAlive 只为运行中的监督进程。control.py 通过 NS2_APP_PATH 获知 App 位置；CLI 无该环境时回退到 /Applications/NS2下载加速.app，因此可从目标 App 内保存登录设置。

显示位置存储在 App UserDefaults：showMenuBar / showDock。至少一项为真，最后一个入口不能关闭，损坏的双 false 保存值启动时修复。从 1.4.1 开始不使用 SwiftUI App/Window/MenuBarExtra 管理生命周期，避免动态 Dock 激活策略被重复管理。Timer 加入 common mode，以便菜单打开时继续采样。不能增加任何电源或亮屏控制。

40 项 Python 测试通过，原生 Apple Silicon 构建和签名通过；显示选项两种组合、实际退出后服务与进程消失、重开启动均已验证。本轮网络不匹配原允许网段，未实测真实 Switch 下载读数；不能伪称完成。

## 1.4.1 界面修复

AppKit AppDelegate 持有 NSWindow（NSHostingView 渲染 SwiftUI 内容）和 NSStatusItem/NSMenu；Info.plist 设置 LSUIElement=true，启动后按保存的 showDock 设置 regular/accessory。切换模式前记录主窗口可见性，只恢复原本可见且未最小化的窗口，不能因隐藏 Dock 关闭主窗口，不能恢复窗口时擅自启用 Dock。

NSMenu 的服务项使用独立 ServiceMenuRow：左侧“下载服务”，最右侧 NSTextField 显示 ✅ 或 ❌，不依赖系统可忽略的 Label SF Symbol。支持点击、NSMenuItem action 和无障碍按钮。主窗口的“下载控制”应用菜单复用同一个菜单；状态栏标题与速度持续更新。只读状态项禁止点击。

在原 Mac 验证 regular 与 UIElement 模式实际切换、隐藏后窗口可继续切页、重启后偏好保留；菜单启停后的可访问状态分别为开启 ✅ 和关闭 ❌。未进行单位网络上的真实 Switch 下载测试。网络后端与恢复规则本次不改。
