"""Compile and test the exact native reader used by the app, without starting any UI."""
from pathlib import Path
import subprocess
import tempfile
source=(Path(__file__).resolve().parents[1]/'src/app/NS2Direct.swift').read_text()
source=source[:source.index('@MainActor final class AppDelegate:')]
cases=r'''
let base = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
try FileManager.default.createDirectory(at: base.appendingPathComponent("runtime"), withIntermediateDirectories: true)
defer { try? FileManager.default.removeItem(at: base) }
var config = Configuration.empty
config.allowed_subnet = "192.168.50.0/24"
try JSONEncoder().encode(config).write(to: base.appendingPathComponent("config.json"))
let previous = ServiceStatus(config: config, running: true, pid: 42, listening: true, address: "192.168.50.1", home_network: true, interfaces: [], logs: "saved log")
func save(_ name: String, _ object: [String: Any]) throws {
    try JSONSerialization.data(withJSONObject: object).write(to: base.appendingPathComponent("runtime/"+name))
}
var recovery: [String: Any] = ["supervisor_pid":42,"worker_pid":43,"time":100.0,"phase":"idle","message":"waiting","speed_mbps":0.0,"low_seconds":0,"attempts":0,"events":[]]
var traffic: [String: Any] = ["worker_pid":43,"time":100.0,"listening":true,"address":"192.168.50.1","clients":["console":["total_bytes":5000000.0,"active_downloads":2]]]
try save("recovery.json",recovery); try save("traffic.json",traffic)
var checks = 0
func check(_ condition: Bool, _ label: String) { precondition(condition,label); checks += 1 }
func read(_ now: Double = 115, alive: @escaping (Int)->Bool = { _ in true }, ip: String = "192.168.50.1") throws -> ServiceStatus {
    try LocalStatus.read(base: base, previous: previous, now: now, isAlive: alive, interfaceAddress: { _ in ip })
}
let current = try read()
check(current.running && current.listening,"fresh 15-second idle heartbeat still valid")
check(current.traffic?.total_bytes == 5000000,"forwarded bytes retained")
check(current.traffic?.active_downloads == 2,"active tunnels trigger fast interval")
check(current == (try read()),"unchanged snapshots compare equal, suppress UI publication")
check(!(try read(121)).listening,"stale worker metrics cannot show listening")
check((try read(126)).recovery == nil,"stale supervisor data rejected")
check(!(try read(alive: { $0 != 42 })).running,"stopped supervisor does not revive")
check(!(try read(alive: { $0 != 43 })).listening,"dead worker cannot appear ready")
check(!(try read(ip:"192.168.50.2")).listening,"old listener IP rejected after address change")
check(!(try read(ip:"192.168.60.1")).home_network,"foreign LAN is not permitted")
check((try read()).logs == "saved log","background reader does not reread log")
traffic["worker_pid"] = 99; try save("traffic.json",traffic)
check(!(try read()).listening,"worker generation mismatch rejected")
traffic["worker_pid"] = 43; traffic["listening"] = false; try save("traffic.json",traffic)
check(!(try read()).listening,"worker waiting for network cannot appear ready")
check(LocalStatus.inSubnet("192.168.50.1","192.168.50.0/24"),"valid subnet")
check(!LocalStatus.inSubnet("192.168.60.1","192.168.50.0/24"),"other subnet")
check(!LocalStatus.inSubnet("999.1.1.1","192.168.50.0/24"),"invalid address")
check(LocalStatus.inSubnet("192.168.50.1","192.168.50.1/32"),"single host subnet")
check(!LocalStatus.inSubnet("192.168.50.2","192.168.50.1/32"),"single host restriction")
print("Native snapshot checks passed:",checks)
'''
with tempfile.TemporaryDirectory(prefix='ns2-native-test-') as temp:
    path=Path(temp)/'Snapshot.swift';path.write_text(source+cases)
    binary=Path(temp)/'snapshot-test'
    subprocess.run(['xcrun','swiftc','-swift-version','5',str(path),'-o',str(binary)],check=True)
    subprocess.run([str(binary)],check=True)
