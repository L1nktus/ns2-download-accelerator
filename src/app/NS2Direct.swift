import SwiftUI
import AppKit
import Combine

struct Configuration: Codable {
    var interface: String
    var allowed_subnet: String
    var port: Int
    var optimize_cdn: Bool
    var allowed_client: String
    var recovery_threshold_kbps: Int
    var recovery_low_seconds: Int
    var recovery_pause_seconds: Int
    var recovery_wait_seconds: Int
    var auto_recovery: Bool
    var launch_at_login: Bool
    static var empty: Configuration { Configuration(interface: "en0", allowed_subnet: "192.168.50.0/24", port: 8888, optimize_cdn: true, allowed_client: "", recovery_threshold_kbps: 100, recovery_low_seconds: 60, recovery_pause_seconds: 15, recovery_wait_seconds: 180, auto_recovery: true, launch_at_login: true) }
}
struct PhysicalInterface: Codable, Identifiable {
    var device: String
    var name: String
    var address: String
    var id: String { device }
}
struct RecoveryStatus: Codable {
    var phase: String
    var message: String
    var speed_mbps: Double
    var low_seconds: Int
    var attempts: Int
    var events: [String]
}
struct TrafficStatus: Codable {
    var available: Bool
    var worker_pid: Int?
    var time: Double?
    var total_bytes: Double?
    var active_downloads: Int?
}
struct ServiceStatus: Codable {
    var config: Configuration
    var running: Bool
    var listening: Bool
    var address: String
    var home_network: Bool
    var interfaces: [PhysicalInterface]
    var logs: String
    var recovery: RecoveryStatus?
    var traffic: TrafficStatus?
}
struct Reply: Decodable {
    var ok: Bool
    var error: String?
    var result: ServiceStatus?
    enum CodingKeys: String, CodingKey { case ok, error, result }
}

@MainActor final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    static weak var shared: AppDelegate?
    static weak var model: Model?
    private var retainedModel: Model?
    private var mainWindow: NSWindow?
    private var statusController: StatusController?

    func applicationDidFinishLaunching(_ notification: Notification) {
        Self.shared = self
        installMainMenu()
        let model = Model()
        retainedModel = model
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 820, height: 720),
                              styleMask: [.titled, .closable, .miniaturizable, .resizable],
                              backing: .buffered, defer: false)
        window.title = "NS2下载加速"
        window.identifier = NSUserInterfaceItemIdentifier("main")
        window.minSize = NSSize(width: 760, height: 650)
        window.isReleasedWhenClosed = false
        window.delegate = self
        window.contentView = NSHostingView(rootView: ContentView().environmentObject(model).tint(.teal))
        window.center()
        window.setFrameAutosaveName("main")
        mainWindow = window
        statusController = StatusController(model: model)
        let controls = NSMenuItem(title: "下载控制", action: nil, keyEquivalent: "")
        controls.submenu = statusController?.controlMenu
        NSApp.mainMenu?.addItem(controls)
        updateVisibility()
        showMainWindow()
    }
    func updateVisibility() {
        guard let model = retainedModel else { return }
        statusController?.update()
        let wanted: NSApplication.ActivationPolicy = model.dockVisible ? .regular : .accessory
        guard NSApp.activationPolicy() != wanted else { return }
        let wasVisible = mainWindow?.isVisible == true
        let wasMiniaturized = mainWindow?.isMiniaturized == true
        NSApp.setActivationPolicy(wanted)
        // macOS may order windows out during a policy transition; restore only a visible one.
        if wasVisible && !wasMiniaturized {
            showMainWindow()
            DispatchQueue.main.async { [weak self] in self?.showMainWindow() }
        }
    }
    @objc func showMainWindow() {
        guard let window = mainWindow else { return }
        if window.isMiniaturized { window.deminiaturize(nil) }
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }
    @objc private func about() { NSApp.orderFrontStandardAboutPanel(options: [.applicationName: "NS2下载加速", .applicationVersion: "1.4.1"]) }
    private func installMainMenu() {
        let menu = NSMenu()
        let appItem = NSMenuItem(); menu.addItem(appItem)
        let appMenu = NSMenu(); appItem.submenu = appMenu
        let aboutItem = appMenu.addItem(withTitle: "关于 NS2下载加速", action: #selector(about), keyEquivalent: ""); aboutItem.target = self
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "隐藏 NS2下载加速", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        appMenu.addItem(withTitle: "退出 NS2下载加速", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        let editItem = NSMenuItem(); menu.addItem(editItem)
        let edit = NSMenu(title: "编辑"); editItem.submenu = edit
        for (title, action, key) in [("撤销", "undo:", "z"), ("剪切", "cut:", "x"), ("复制", "copy:", "c"), ("粘贴", "paste:", "v"), ("全选", "selectAll:", "a")] {
            edit.addItem(withTitle: title, action: NSSelectorFromString(action), keyEquivalent: key)
        }
        let windowItem = NSMenuItem(); menu.addItem(windowItem)
        let windows = NSMenu(title: "窗口"); windowItem.submenu = windows
        windows.addItem(withTitle: "关闭窗口", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        windows.addItem(withTitle: "最小化", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        let show = windows.addItem(withTitle: "显示主界面", action: #selector(showMainWindow), keyEquivalent: ""); show.target = self
        NSApp.mainMenu = menu
        NSApp.windowsMenu = windows
    }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard let model = retainedModel else { return .terminateNow }
        model.stopAndQuit()
        return .terminateLater
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        showMainWindow()
        return true
    }
}

/// A real menu button with a dedicated trailing state indicator, including accessibility.
@MainActor final class ServiceMenuRow: NSView {
    let caption = NSTextField(labelWithString: "下载服务")
    let indicator = NSTextField(labelWithString: "❌")
    var activated: (() -> Void)?
    var enabled = true
    private var hovered = false
    private var tracking: NSTrackingArea?
    override init(frame: NSRect) {
        super.init(frame: frame)
        caption.font = NSFont.menuFont(ofSize: 14)
        indicator.font = NSFont.systemFont(ofSize: 15)
        caption.frame = NSRect(x: 14, y: 5, width: 210, height: 20)
        indicator.frame = NSRect(x: frame.width-38, y: 4, width: 26, height: 22)
        indicator.alignment = .right
        indicator.autoresizingMask = [.minXMargin]
        addSubview(caption); addSubview(indicator)
        setAccessibilityElement(true)
        setAccessibilityRole(.button)
        setAccessibilityLabel("下载服务")
        setAccessibilityIdentifier("service-toggle")
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
    func update(enabled: Bool, on: Bool) {
        self.enabled = enabled
        indicator.stringValue = on ? "✅" : "❌"
        setAccessibilityValue(on ? "开启 ✅" : "关闭 ❌")
        setAccessibilityEnabled(enabled)
        caption.textColor = enabled ? .labelColor : .disabledControlTextColor
        needsDisplay = true
    }
    override func updateTrackingAreas() {
        super.updateTrackingAreas()
        if let tracking = tracking { removeTrackingArea(tracking) }
        let area = NSTrackingArea(rect: bounds, options: [.mouseEnteredAndExited, .activeAlways, .inVisibleRect], owner: self)
        addTrackingArea(area); tracking = area
    }
    override func mouseEntered(with event: NSEvent) { hovered = true; needsDisplay = true }
    override func mouseExited(with event: NSEvent) { hovered = false; needsDisplay = true }
    override func draw(_ dirtyRect: NSRect) {
        if hovered && enabled {
            NSColor.selectedContentBackgroundColor.setFill()
            NSBezierPath(roundedRect: bounds.insetBy(dx: 5, dy: 1), xRadius: 5, yRadius: 5).fill()
        }
        caption.textColor = !enabled ? .disabledControlTextColor : (hovered ? .selectedMenuItemTextColor : .labelColor)
    }
    private func activate() {
        guard enabled else { return }
        enclosingMenuItem?.menu?.cancelTracking()
        activated?()
    }
    override func mouseUp(with event: NSEvent) { activate() }
    override func accessibilityPerformPress() -> Bool { activate(); return enabled }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
}

@MainActor final class StatusController: NSObject, NSMenuDelegate {
    private let model: Model
    private var item: NSStatusItem?
    private let menu = NSMenu(title: "下载控制")
    var controlMenu: NSMenu { menu }
    private var observer: AnyCancellable?
    private let titleItem = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    private let speedItem = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    private let addressItem = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    private let portItem = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    private let serviceItem = NSMenuItem(title: "下载服务", action: nil, keyEquivalent: "")
    private let serviceRow = ServiceMenuRow(frame: NSRect(x: 0, y: 0, width: 280, height: 30))
    private var quitItem: NSMenuItem!
    init(model: Model) {
        self.model = model
        super.init()
        menu.autoenablesItems = false
        menu.minimumWidth = 280
        menu.delegate = self
        for entry in [titleItem, speedItem] { entry.isEnabled = false; menu.addItem(entry) }
        menu.addItem(.separator())
        for entry in [NSMenuItem(title: "Switch 代理设置", action: nil, keyEquivalent: ""), addressItem, portItem, NSMenuItem(title: "认证：不需要 · DNS：自动", action: nil, keyEquivalent: "")] {
            entry.isEnabled = false; menu.addItem(entry)
        }
        menu.addItem(.separator())
        let show = menu.addItem(withTitle: "显示主界面", action: #selector(showWindow), keyEquivalent: ""); show.target = self
        serviceItem.target = self
        serviceItem.action = #selector(toggleService)
        serviceItem.view = serviceRow; menu.addItem(serviceItem)
        serviceRow.activated = { [weak self] in guard let self = self else { return }; self.model.setService(!self.model.serviceEnabled) }
        menu.addItem(.separator())
        quitItem = menu.addItem(withTitle: "退出 NS2下载加速", action: #selector(quit), keyEquivalent: ""); quitItem.target = self
        observer = model.objectWillChange.sink { [weak self] _ in
            DispatchQueue.main.async { self?.update() }
        }
        update()
    }
    func update() {
        if model.menuVisible && item == nil {
            item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
            item?.menu = menu
            item?.button?.setAccessibilityLabel("NS2下载加速")
        } else if !model.menuVisible, let item = item {
            NSStatusBar.system.removeStatusItem(item); self.item = nil
        }
        if let button = item?.button {
            button.image = NSImage(systemSymbolName: model.listening ? "arrow.up.right.circle.fill" : "arrow.up.right.circle", accessibilityDescription: "NS2下载加速")
            button.image?.isTemplate = true
            button.imagePosition = .imageLeading
            button.title = model.downloadSpeed > 0 ? " " + model.speedText : ""
            button.toolTip = "NS2下载加速 · " + model.title
        }
        titleItem.title = model.title
        speedItem.title = "NS2 下载速度：" + model.speedText
        addressItem.title = "服务器：" + (model.status?.address.isEmpty == false ? model.status!.address : "尚未连接")
        portItem.title = "端口：\(model.status?.config.port ?? 8888)"
        let enabled = !model.busy && !model.terminating && model.status != nil
        serviceItem.isEnabled = enabled
        serviceRow.update(enabled: enabled, on: model.serviceEnabled)
        quitItem.isEnabled = !model.terminating
    }
    func menuWillOpen(_ menu: NSMenu) { update() }
    @objc private func toggleService() { model.setService(!model.serviceEnabled) }
    @objc private func showWindow() { model.openWindow() }
    @objc private func quit() { NSApp.terminate(nil) }
}

@MainActor final class Model: ObservableObject {
    @Published var status: ServiceStatus?
    @Published var draft = Configuration.empty
    @Published var portText = "8888"
    @Published var thresholdText = "100"
    @Published var lowTimeText = "60"
    @Published var pauseTimeText = "15"
    @Published var waitTimeText = "180"
    @Published var busy = false
    @Published var error: String?
    @Published var resultText = ""
    @Published var showResult = false
    @Published var tab = 0
    @Published var menuVisible = UserDefaults.standard.object(forKey: "showMenuBar") as? Bool ?? true
    @Published var dockVisible = UserDefaults.standard.object(forKey: "showDock") as? Bool ?? true
    @Published var terminating = false
    @Published var downloadSpeed = 0.0
    @Published var trafficAvailable = false
    private var previousTraffic: TrafficStatus?
    private var initialized = false
    private var refreshing = false
    private var timer: Timer?
    nonisolated static let base = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/NS2Proxy")

    init() {
        do { try Self.prepareBackend() }
        catch { self.error = "无法准备本地服务：" + error.localizedDescription }
        if !menuVisible && !dockVisible { menuVisible = true }
        AppDelegate.model = self
        applyVisibility()
        action("start")
        timer = Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.refresh() }
        }
        if let timer = timer { RunLoop.main.add(timer, forMode: .common) }
    }
    var listening: Bool { status?.listening == true }
    var title: String {
        guard let s = status else { return "正在读取状态" }
        if s.recovery?.phase == "pausing" { return "正在恢复下载" }
        if s.listening { return "下载服务运行中" }
        if s.running && s.address.isEmpty { return "所选网络尚未连接" }
        if s.running && !s.home_network { return "当前网络不在允许网段内" }
        if s.running { return "服务尚未就绪，请查看连接记录" }
        return "下载服务已停止"
    }
    var color: Color { listening ? .teal : (status?.running == true ? .orange : .secondary) }
    var settingsText: String {
        "代理服务器：\(status?.address.isEmpty == false ? status!.address : "尚未连接")\n端口：\(status?.config.port ?? 8888)\n认证：不需要\nDNS：保持自动"
    }

    nonisolated static func prepareBackend() throws {
        let manager = FileManager.default
        try manager.createDirectory(at: base, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        if let resources = Bundle.main.resourceURL {
            for name in ["proxy_accel.py", "control.py", "supervisor.py", "recovery.py", "LICENSE"] {
                let destination = base.appendingPathComponent(name)
                let source = resources.appendingPathComponent(name)
                if !manager.fileExists(atPath: destination.path) && manager.fileExists(atPath: source.path) {
                    try manager.copyItem(at: source, to: destination)
                }
            }
        }
        let config = base.appendingPathComponent("config.json")
        if !manager.fileExists(atPath: config.path) {
            var object = try JSONSerialization.jsonObject(with: JSONEncoder().encode(Configuration.empty)) as! [String: Any]
            object["log"] = manager.homeDirectoryForCurrentUser.appendingPathComponent("Library/Logs/NS2Proxy/proxy.log").path
            object["launch_at_login"] = false
            try JSONSerialization.data(withJSONObject: object, options: .prettyPrinted).write(to: config, options: .atomic)
            try manager.setAttributes([.posixPermissions: 0o600], ofItemAtPath: config.path)
        }
    }
    nonisolated static func invoke(_ action: String, input: Data? = nil) -> (Data, String) {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/opt/homebrew/bin/python3")
        process.arguments = [base.appendingPathComponent("control.py").path, action]
        let output = Pipe(), errors = Pipe()
        process.standardOutput = output
        process.standardError = errors
        let stdinPipe = Pipe()
        process.standardInput = stdinPipe
        var environment = ProcessInfo.processInfo.environment
        environment["NS2_APP_PATH"] = Bundle.main.bundleURL.path
        process.environment = environment
        do {
            try process.run()
            if let input = input { stdinPipe.fileHandleForWriting.write(input) }
            try? stdinPipe.fileHandleForWriting.close()
            let data = output.fileHandleForReading.readDataToEndOfFile()
            let errorData = errors.fileHandleForReading.readDataToEndOfFile()
            process.waitUntilExit()
            return (data, String(data: errorData, encoding: .utf8) ?? "")
        } catch { return (Data(), error.localizedDescription) }
    }
    private func apply(_ state: ServiceStatus, updateDraft: Bool = false) {
        status = state
        let current = state.traffic
        trafficAvailable = state.listening && current?.available == true
        downloadSpeed = 0
        if trafficAvailable, let current = current, let previous = previousTraffic,
           previous.worker_pid == current.worker_pid,
           let now = current.time, let before = previous.time,
           let total = current.total_bytes, let oldTotal = previous.total_bytes,
           now > before, now - before <= 12, total >= oldTotal {
            downloadSpeed = (total-oldTotal)/(now-before)
        }
        previousTraffic = trafficAvailable ? current : nil
        if !initialized || updateDraft {
            draft = state.config
            portText = String(state.config.port)
            thresholdText = String(state.config.recovery_threshold_kbps)
            lowTimeText = String(state.config.recovery_low_seconds)
            pauseTimeText = String(state.config.recovery_pause_seconds)
            waitTimeText = String(state.config.recovery_wait_seconds)
            initialized = true
        }
    }
    func refresh() {
        guard !busy, !refreshing, !terminating else { return }
        refreshing = true
        Task {
            let (data, failure) = await Task.detached { Self.invoke("status") }.value
            defer { refreshing = false }
            do {
                let response = try JSONDecoder().decode(Reply.self, from: data)
                if response.ok, let state = response.result { apply(state) }
                else { error = response.error ?? "无法读取服务状态。" }
            } catch { self.error = failure.isEmpty ? "暂时无法读取服务，请稍后重试。" : failure }
        }
    }
    func action(_ action: String, save: Bool = false) {
        guard !busy, !terminating else { return }
        var input: Data?
        if save {
            guard let port = Int(portText), (1024...65535).contains(port) else {
                error = "端口需要是 1024–65535 之间的数字。"; return
            }
            guard let threshold = Int(thresholdText), (1...1_000_000).contains(threshold),
                  let lowTime = Int(lowTimeText), (1...86400).contains(lowTime),
                  let pauseTime = Int(pauseTimeText), (1...86400).contains(pauseTime),
                  let waitTime = Int(waitTimeText), (1...86400).contains(waitTime) else {
                error = "恢复设置需要填写正整数；阈值单位是 KB/s，时间单位是秒。"; return
            }
            draft.recovery_threshold_kbps = threshold
            draft.recovery_low_seconds = lowTime
            draft.recovery_pause_seconds = pauseTime
            draft.recovery_wait_seconds = waitTime
            draft.port = port
            input = try? JSONEncoder().encode(draft)
        }
        busy = true
        error = nil
        let encoded = input
        Task {
            let (data, failure) = await Task.detached { Self.invoke(action, input: encoded) }.value
            defer { busy = false }
            do {
                if action == "diagnose" || action == "speed" {
                    guard let object = try JSONSerialization.jsonObject(with: data) as? [String: Any] else { throw CocoaError(.fileReadCorruptFile) }
                    guard object["ok"] as? Bool == true, let result = object["result"] as? [String: Any] else {
                        self.error = object["error"] as? String ?? "诊断失败。"; return
                    }
                    resultText = result["message"] as? String ?? "检查完成。"
                    if let stateObject = result["status"], let raw = try? JSONSerialization.data(withJSONObject: stateObject), let state = try? JSONDecoder().decode(ServiceStatus.self, from: raw) { apply(state) }
                    showResult = true
                } else {
                    let response = try JSONDecoder().decode(Reply.self, from: data)
                    if response.ok, let state = response.result { apply(state, updateDraft: save) }
                    else { self.error = response.error ?? "操作失败。" }
                }
            } catch { self.error = failure.isEmpty ? "操作未完成，请查看日志。" : failure }
        }
    }
    var speedText: String {
        guard listening else { return "—" }
        guard trafficAvailable else { return "正在读取" }
        if downloadSpeed >= 1_000_000 { return String(format: "%.2f MB/s", downloadSpeed/1_000_000) }
        return String(format: "%.1f KB/s", downloadSpeed/1000)
    }
    var serviceEnabled: Bool { status?.running == true }
    func setService(_ enabled: Bool) {
        guard enabled != serviceEnabled else { return }
        action(enabled ? "start" : "stop")
    }
    func setMenuVisible(_ visible: Bool) {
        guard visible != menuVisible, visible || dockVisible else { return }
        menuVisible = visible
        applyVisibility()
    }
    func setDockVisible(_ visible: Bool) {
        guard visible != dockVisible, visible || menuVisible else { return }
        dockVisible = visible
        applyVisibility()
    }
    private func applyVisibility() {
        UserDefaults.standard.set(menuVisible, forKey: "showMenuBar")
        UserDefaults.standard.set(dockVisible, forKey: "showDock")
        AppDelegate.shared?.updateVisibility()
    }

    func stopAndQuit() {
        guard !terminating else { return }
        terminating = true
        Task {
            // Serialize with any in-flight Start/Save: it must not revive the job after Quit.
            while busy { try? await Task.sleep(nanoseconds: 100_000_000) }
            let (data, failure) = await Task.detached { Self.invoke("stop") }.value
            if let reply = try? JSONDecoder().decode(Reply.self, from: data), reply.ok,
               let state = reply.result, !state.running && !state.listening {
                NSApp.reply(toApplicationShouldTerminate: true)
            } else {
                terminating = false
                error = failure.isEmpty ? "服务未能停止，App 暂不退出。请查看连接记录后重试。" : failure
                openWindow()
                NSApp.reply(toApplicationShouldTerminate: false)
            }
        }
    }
    func openWindow() { AppDelegate.shared?.showMainWindow() }
}

struct Card<Content: View>: View {
    let content: Content
    init(@ViewBuilder content: () -> Content) { self.content = content() }
    var body: some View {
        VStack(alignment: .leading, spacing: 16) { content }
            .padding(22).frame(maxWidth: .infinity, alignment: .leading)
            .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 18))
            .overlay(RoundedRectangle(cornerRadius: 18).stroke(Color.primary.opacity(0.06)))
    }
}
struct Caption: View {
    let text: String
    var body: some View { Text(text).font(.system(size: 12)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true) }
}
struct ContentView: View {
    @EnvironmentObject var model: Model
    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 13) {
                Image(systemName: "arrow.up.right.circle.fill").font(.system(size: 35)).foregroundStyle(.teal)
                VStack(alignment: .leading, spacing: 3) {
                    Text("NS2下载加速").font(.system(size: 23, weight: .semibold))
                    Text("为 Switch 提供下载加速").font(.system(size: 12)).foregroundStyle(.secondary)
                }
                Spacer()
            }.padding(.horizontal, 28).padding(.top, 24).padding(.bottom, 20)
            Picker("页面", selection: $model.tab) {
                Text("总览").tag(0)
                Text("功能选项").tag(1)
                Text("连接记录").tag(2)
            }.pickerStyle(.segmented).labelsHidden().frame(width: 330).padding(.bottom, 18)
            ScrollView {
                VStack(spacing: 16) {
                    if let error = model.error {
                        HStack(alignment: .top) {
                            Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange)
                            Text(error).font(.system(size: 13)).textSelection(.enabled)
                            Spacer()
                            Button { model.error = nil } label: { Image(systemName: "xmark") }.buttonStyle(.plain)
                        }.padding(14).background(.orange.opacity(0.10), in: RoundedRectangle(cornerRadius: 12))
                    }
                    switch model.tab {
                    case 1: options
                    case 2: records
                    default: overview
                    }
                }.padding(.horizontal, 28).padding(.bottom, 22)
            }
            Divider()
            HStack {
                Image(systemName: "moon").foregroundStyle(.secondary)
                Text("息屏可用；睡眠管理请使用 Amphetamine。")
                Spacer()
                if model.busy { ProgressView().controlSize(.small) }
                Text("1.4.1").foregroundStyle(.tertiary)
            }.font(.system(size: 11)).foregroundStyle(.secondary).padding(.horizontal, 28).padding(.vertical, 13)
        }
        .frame(minWidth: 760, idealWidth: 820, minHeight: 650, idealHeight: 720)
        .background(Color(nsColor: .windowBackgroundColor))
        .sheet(isPresented: $model.showResult) {
            VStack(alignment: .leading, spacing: 20) {
                Label("检查结果", systemImage: "checkmark.circle").font(.title2.weight(.semibold))
                Text(model.resultText).font(.system(size: 14)).lineSpacing(7).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                HStack { Spacer(); Button("完成") { model.showResult = false }.keyboardShortcut(.defaultAction) }
            }.padding(28).frame(width: 570)
        }
    }
    private var overview: some View {
        VStack(spacing: 16) {
            Card {
                HStack(alignment: .center, spacing: 14) {
                    Circle().fill(model.color).frame(width: 10, height: 10)
                    VStack(alignment: .leading, spacing: 5) {
                        Text(model.title).font(.system(size: 19, weight: .semibold))
                        Caption(text: model.listening ? "Switch 与 Mac 连接同一网络，即可使用下方代理设置。" : "启动后，在 Switch 的网络设置中填写下方代理信息。")
                    }
                    Spacer()
                    Toggle("下载服务", isOn: Binding(get: { model.serviceEnabled }, set: { model.setService($0) }))
                        .toggleStyle(.switch).fixedSize().disabled(model.busy || model.terminating || model.status == nil)
                }
                Divider()
                HStack(spacing: 12) {
                    route("gamecontroller", "Switch 2")
                    Image(systemName: "arrow.right").foregroundStyle(.tertiary)
                    route("laptopcomputer", "Mac")
                    Image(systemName: "arrow.right").foregroundStyle(.teal)
                    route("network", "任天堂 CDN")
                    Spacer()
                }
                HStack(alignment: .firstTextBaseline) {
                    Text("NS2 下载速度").font(.system(size: 13))
                    Spacer()
                    Text(model.speedText).font(.system(size: 26, weight: .medium, design: .monospaced))
                }
                Caption(text: "最近约 2 秒内，经此服务转发到 Switch 的游戏下载数据；不额外消耗测速流量。")
                if let recovery = model.status?.recovery {
                    Divider()
                    HStack(alignment: .top) {
                        Label("低速自动恢复", systemImage: "arrow.clockwise")
                            .font(.system(size: 12, weight: .medium))
                        Spacer()
                        Text(recovery.message).font(.system(size: 12)).foregroundStyle(.secondary)
                            .multilineTextAlignment(.trailing)
                    }
                }
            }
            Card {
                HStack {
                    Text("在 Switch 上填写").font(.system(size: 16, weight: .semibold))
                    Spacer()

                }
                HStack(alignment: .firstTextBaseline, spacing: 30) {
                    VStack(alignment: .leading, spacing: 7) {
                        Caption(text: "代理服务器地址")
                        Text(model.status?.address.isEmpty == false ? model.status!.address : "尚未连接局域网")
                            .font(.system(size: 28, weight: .medium, design: .monospaced)).textSelection(.enabled)
                    }
                    VStack(alignment: .leading, spacing: 7) {
                        Caption(text: "端口")
                        Text(String(model.status?.config.port ?? 8888)).font(.system(size: 28, weight: .medium, design: .monospaced)).textSelection(.enabled)
                    }
                    Spacer()
                }
                Caption(text: "代理服务器设为「使用」，无需认证；DNS 保持自动。Switch 与 Mac 需要在同一局域网。")
            }
            HStack(spacing: 12) {
                Button { model.action("diagnose") } label: { Label("检查连接", systemImage: "stethoscope") }
                Button { model.action("speed") } label: { Label("测速 · 30 MiB", systemImage: "speedometer") }
                Spacer()
                Caption(text: "关闭窗口继续运行；退出 App 会停止服务。")
            }.disabled(model.busy || !model.listening)
        }
    }
    private func route(_ icon: String, _ text: String) -> some View {
        Label(text, systemImage: icon).font(.system(size: 13, weight: .medium)).padding(.vertical, 6)
    }
    private var options: some View {
        VStack(spacing: 16) {
            Card {
                Text("显示位置").font(.system(size: 16, weight: .semibold))
                Toggle("在菜单栏显示", isOn: Binding(get: { model.menuVisible }, set: { model.setMenuVisible($0) }))
                    .toggleStyle(.switch).disabled(!model.dockVisible && model.menuVisible)
                Toggle("在 Dock 栏显示", isOn: Binding(get: { model.dockVisible }, set: { model.setDockVisible($0) }))
                    .toggleStyle(.switch).disabled(!model.menuVisible && model.dockVisible)
                Caption(text: "立即生效，自动保存。至少保留一个入口；最后一个入口不能关闭。")
            }
            Card {
                Text("常用功能").font(.system(size: 16, weight: .semibold))
                Toggle(isOn: $model.draft.launch_at_login) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("登录时启动 App")
                        Caption(text: "Mac 登录后自动打开 App 并启动服务，遵循上方显示设置。")
                    }
                }.toggleStyle(.switch)
                Divider()
                Toggle(isOn: $model.draft.optimize_cdn) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("CDN 自动择优")
                        Caption(text: "从多个解析结果中按建连延迟排序；关闭后按解析结果顺序连接。")
                    }
                }.toggleStyle(.switch)
                Divider()
                Toggle(isOn: $model.draft.auto_recovery) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("低速自动恢复")
                        Caption(text: "下载持续低速时自动重启转发，等待 Switch 自行续传。")
                    }
                }.toggleStyle(.switch)
                if model.draft.auto_recovery {
                    VStack(alignment: .leading, spacing: 12) {
                        recoveryField("低速阈值", value: $model.thresholdText, unit: "KB/s")
                        recoveryField("低速持续时间", value: $model.lowTimeText, unit: "秒")
                        recoveryField("停止服务时间", value: $model.pauseTimeText, unit: "秒")
                        recoveryField("恢复观察上限", value: $model.waitTimeText, unit: "秒")
                        Caption(text: "180 秒 = 3 分钟。观察期内出现游戏下载会提前结束等待；超时只记录结果，服务保持开启，不因此再次重启。")
                        Caption(text: "每 30 分钟最多恢复两次。以上设置保存后直接生效，不中断当前下载。")
                    }.padding(.leading, 4)
                }
                Divider()
                VStack(alignment: .leading, spacing: 9) {
                    Text("仅允许这台 Switch（可选）")
                    TextField("留空允许本地网段，例如 192.168.50.50", text: $model.draft.allowed_client).textFieldStyle(.roundedBorder)
                    Caption(text: "填写 Switch 的 IPv4 地址可限制单设备访问；本机诊断也会遵守此限制。")
                }
            }
            Card {
                DisclosureGroup("网络设置") {
                    VStack(alignment: .leading, spacing: 14) {
                        Picker("网络接口", selection: $model.draft.interface) {
                            ForEach(model.status?.interfaces ?? []) { item in
                                Text("\(item.name) · \(item.device)\(item.address.isEmpty ? "（未连接）" : " · " + item.address)").tag(item.device)
                            }
                        }
                        HStack {
                            Text("代理端口").frame(width: 104, alignment: .leading)
                            TextField("8888", text: $model.portText).textFieldStyle(.roundedBorder).frame(width: 110)
                            Spacer()
                        }
                        HStack {
                            Text("允许的局域网").frame(width: 104, alignment: .leading)
                            TextField("192.168.50.0/24", text: $model.draft.allowed_subnet).textFieldStyle(.roundedBorder)
                        }
                        Caption(text: "更换网络时在这里调整；只支持本地 IPv4 网段。修改端口或网络设置后，也要同步修改 Switch。")
                    }.padding(.top, 14)
                }
            }
            HStack {
                Caption(text: "保存网络或择优设置会重新启动正在运行的服务，短暂中断当前下载。")
                Spacer()
                Button("保存设置") { model.action("save", save: true) }.buttonStyle(.borderedProminent).tint(.teal)
            }
        }.disabled(model.busy || model.terminating || model.status == nil)
    }
    private func recoveryField(_ title: String, value: Binding<String>, unit: String) -> some View {
        HStack {
            Text(title).frame(width: 120, alignment: .leading)
            TextField(title, text: value).textFieldStyle(.roundedBorder).frame(width: 100)
                .accessibilityLabel(title)
            Text(unit).foregroundStyle(.secondary)
            Spacer()
        }.font(.system(size: 13))
    }
    private var records: some View {
        Card {
            HStack {
                Text("最近连接记录").font(.system(size: 16, weight: .semibold))
                Spacer()
                Button("打开日志文件夹") { NSWorkspace.shared.open(FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Logs/NS2Proxy")) }
                Button("刷新") { model.refresh() }.disabled(model.busy)
            }
            if let recovery = model.status?.recovery, !recovery.events.isEmpty {
                Text("自动恢复记录").font(.system(size: 13, weight: .semibold))
                Text(recovery.events.suffix(6).joined(separator: "\n"))
                    .font(.system(size: 12)).lineSpacing(5).textSelection(.enabled)
                Divider()
            }
            Caption(text: "显示目标域名、出站地址与连接结果；不记录 HTTPS 内容或账号密码。")
            Text(model.status?.logs.isEmpty == false ? model.status!.logs : "还没有连接记录。启动服务后在 Switch 上运行连接测试。")
                .font(.system(size: 11, design: .monospaced)).textSelection(.enabled).lineSpacing(5)
                .frame(maxWidth: .infinity, minHeight: 300, alignment: .topLeading)
                .padding(14).background(Color.primary.opacity(0.035), in: RoundedRectangle(cornerRadius: 10))
        }
    }
}

// AppKit owns lifecycle and activation policy; SwiftUI renders only the content.
@main enum NS2DirectApp {
    @MainActor static func main() {
        let app = NSApplication.shared
        let delegate = AppDelegate()
        app.delegate = delegate
        withExtendedLifetime(delegate) { app.run() }
    }
}
