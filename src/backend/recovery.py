"""Pure per-client download monitoring policy. Does not open sockets or stop jobs."""
from collections import deque

THRESHOLD = 100_000
LOW_SECONDS = 60
STOP_SECONDS = 15
WAIT_SECONDS = 180
BUDGET_SECONDS = 1800
MAX_ATTEMPTS = 2
ARM_BYTES = 1024 * 1024

RECOVERY_DEFAULTS = {
    'recovery_threshold_kbps': 100,
    'recovery_low_seconds': 60,
    'recovery_pause_seconds': 15,
    'recovery_wait_seconds': 180,
}


def validate_recovery_options(data):
    for key in RECOVERY_DEFAULTS:
        if key not in data:
            continue
        value = data[key]
        maximum = 1_000_000 if key == 'recovery_threshold_kbps' else 86400
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
            label = {'recovery_threshold_kbps': '低速阈值（KB/s）', 'recovery_low_seconds': '低速持续时间',
                     'recovery_pause_seconds': '停止服务时间', 'recovery_wait_seconds': '恢复观察时间'}[key]
            raise ValueError(f'{label}需要是 1–{maximum} 之间的整数。')


def game_content_host(host):
    first = host.lower().split('.')[0]
    return host.endswith('.nintendo.net') and (first == 'atum' or first.startswith('atum-') or first == 'scsi-download')


class RecoveryPolicy:
    # State machine: idle -> monitoring -> pausing -> waiting -> checking.
    # "waiting" expiry only records a result; it must never be another restart trigger.
    # Speed is the SUM of eligible download tunnels for one console, not per tunnel.
    # Pure state transitions: tests use simulated time; this class never stops processes.
    def __init__(self, *, threshold=THRESHOLD, low_seconds=LOW_SECONDS,
                 wait_seconds=WAIT_SECONDS, budget_seconds=BUDGET_SECONDS,
                 max_attempts=MAX_ATTEMPTS, arm_bytes=ARM_BYTES, success_seconds=30,
                 sample_window=10):
        self.threshold = threshold
        self.low_seconds = low_seconds
        self.wait_seconds = wait_seconds
        self.budget_seconds = budget_seconds
        self.max_attempts = max_attempts
        self.arm_bytes = arm_bytes
        self.success_seconds = success_seconds
        self.sample_window = sample_window
        self.attempts = []
        self.clients = {}
        self.phase = 'idle'
        self.target = None
        self.wait_started = None
        self.wait_deadline = None
        self.pending = False
        self.events = []
        self.speed = 0.0
        self.low_elapsed = 0.0
        self.last_result = ''

    def configure(self, data):
        validate_recovery_options(data)
        threshold = data.get('recovery_threshold_kbps', self.threshold/1000)*1000
        low_seconds = data.get('recovery_low_seconds', self.low_seconds)
        wait_seconds = data.get('recovery_wait_seconds', self.wait_seconds)
        if (threshold, low_seconds) != (self.threshold, self.low_seconds):
            self.reset_worker()
        self.threshold, self.low_seconds, self.wait_seconds = threshold, low_seconds, wait_seconds
        if self.pending and self.phase == 'waiting' and self.wait_started is not None:
            self.wait_deadline = self.wait_started+self.wait_seconds

    def event(self, text):
        self.events.append(text)

    def reset_worker(self):
        self.clients.clear()
        self.speed = self.low_elapsed = 0.0

    def begin_restart(self, now, client, reason):
        self.attempts = [t for t in self.attempts if 0 <= now-t < self.budget_seconds]
        if len(self.attempts) >= self.max_attempts:
            if self.phase != 'limited':
                self.event('已达到恢复次数上限，保留当前连接继续下载。')
            self.phase = 'limited'
            self.last_result = '次数上限'
            return False
        self.attempts.append(now)
        self.target = client
        self.pending = True
        self.phase = 'pausing'
        self.reset_worker()
        self.event(reason)
        return True

    def worker_started(self, now):
        self.reset_worker()
        if self.pending:
            self.phase = 'waiting'
            self.wait_started = now
            self.wait_deadline = now + self.wait_seconds
        else:
            self.phase = 'idle'

    def tick(self, now, clients, enabled=True):
        self.attempts = [t for t in self.attempts if 0 <= now-t < self.budget_seconds]
        if not enabled:
            self.reset_worker()
            self.phase = 'disabled'
            self.pending = False
            self.wait_deadline = None
            return None
        if self.phase == 'pausing':
            return None
        waiting = self.pending and self.phase == 'waiting'
        target_bulk = clients.get(self.target, {}).get('total_bytes', 0) if self.target else 0
        if waiting and target_bulk < self.arm_bytes:
            if now >= self.wait_deadline:
                self.pending = False
                self.phase = 'idle'
                self.last_result = '未观察到续传'
                self.event('恢复等待超时，尚未观察到游戏数据；保留当前服务，不重复断线。')
            return None
        if waiting:
            self.phase = 'checking'
            self.event('主机已重新传输游戏数据，正在检查速度。')
        any_active = False
        for client in list(self.clients):
            if client not in clients:
                del self.clients[client]
        for client, item in clients.items():
            total = item['total_bytes']
            active = item.get('active_downloads', 0) > 0
            state = self.clients.get(client)
            if total < self.arm_bytes or (not active and (state is None or now-state['last_active'] >= 15)):
                self.clients.pop(client, None)
                continue
            any_active = True
            if active and state is not None:
                state['last_active'] = now
            if state is None or total < state['samples'][-1][1]:
                self.clients[client] = {'samples': deque([(now, total)]), 'low': None, 'good': None, 'speed': 0, 'last_active': now}
                continue
            samples = state['samples']
            samples.append((now, total))
            while len(samples) > 2 and samples[1][0] <= now - self.sample_window:
                samples.popleft()
            dt = now-samples[0][0]
            if dt < self.sample_window:
                continue
            speed = max(0, total-samples[0][1])/dt
            state['speed'] = speed
            if speed < self.threshold:
                state['good'] = None
                if state['low'] is None:
                    state['low'] = now
                elapsed = now-state['low']
                if elapsed >= self.low_seconds and active:
                    if self.begin_restart(now, client, f'低速持续 {self.low_seconds:g} 秒，准备恢复下载。'):
                        return '游戏下载持续低速'
            else:
                state['low'] = None
                if state['good'] is None:
                    state['good'] = now
                if self.pending and client == self.target and now-state['good'] >= self.success_seconds:
                    self.pending = False
                    self.last_result = '速度已恢复'
                    self.event('恢复有效：游戏下载速度持续达到阈值。')
                    self.phase = 'monitoring'
        self.speed = sum(s['speed'] for s in self.clients.values())
        self.low_elapsed = max((now-s['low'] for s in self.clients.values() if s['low'] is not None), default=0)
        if not any_active:
            if self.pending and self.phase == 'waiting':
                pass
            else:
                if self.pending:
                    self.pending = False
                    self.last_result = '已续传，当前没有下载连接'
                    self.event('已观察到续传，当前没有活动游戏下载；停止低速计时。')
                self.phase = 'idle'
                self.speed = self.low_elapsed = 0
        elif self.phase not in ('limited','checking'):
            self.phase = 'checking' if self.pending else 'monitoring'
        if self.phase == 'limited' and len(self.attempts) < self.max_attempts:
            self.phase = 'monitoring' if any_active else 'idle'
        return None
