#!/usr/bin/env python3
"""Run the proxy worker and recover slow downloads without a UI or power assertions."""
import argparse
import datetime
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from recovery import RecoveryPolicy, STOP_SECONDS, validate_recovery_options

LOG = logging.getLogger('recovery')


class Supervisor:
    # launchd owns this supervisor; the proxy is its child. Keep the supervisor alive
    # while stopping the child, otherwise KeepAlive would defeat the requested pause.
    # User Stop terminates this supervisor and cancels pending automatic startup.
    def __init__(self, config, *, worker_script=None, policy=None, pause_seconds=STOP_SECONDS, sample_seconds=5):
        self.config_path = Path(config)
        self.base = self.config_path.parent
        self.runtime = self.base / 'runtime'
        self.runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.state_path = self.runtime / 'recovery.json'
        self.traffic_path = self.runtime / 'traffic.json'
        self.worker_script = Path(worker_script) if worker_script else self.base / 'proxy_accel.py'
        self.policy = policy or RecoveryPolicy()
        self.pause_seconds = pause_seconds
        self.sample_seconds = sample_seconds
        self.stop_event = threading.Event()
        self.child = None
        self.pause_started = None
        self.pause_deadline = None
        self.next_sample = 0
        self.last_worker_start = 0
        self.records = []
        self.event_offset = 0
        self.enabled = True
        self.settings_stamp = None
        self.last_publication = None
        self.last_publication_time = 0
        self.load_history()

    def load_history(self):
        try:
            saved = json.loads(self.state_path.read_text())
            now, wall = time.monotonic(), time.time()
            self.policy.attempts = [now-(wall-t) for t in saved.get('attempts_epoch', []) if 0 <= wall-t < self.policy.budget_seconds]
            self.records = saved.get('events', [])[-12:]
        except (OSError, ValueError, TypeError):
            pass

    def config(self):
        return json.loads(self.config_path.read_text())

    def apply_config(self):
        stamp = self.config_path.stat().st_mtime_ns
        if stamp == self.settings_stamp:
            return
        self.settings_stamp = stamp
        data = self.config()
        validate_recovery_options(data)
        self.enabled = data.get('auto_recovery', True)
        self.policy.configure(data)
        self.pause_seconds = data.get('recovery_pause_seconds', self.pause_seconds)
        if self.pause_deadline is not None and self.pause_started is not None:
            self.pause_deadline = self.pause_started+self.pause_seconds

    def record(self, text):
        line = datetime.datetime.now().astimezone().strftime('%m-%d %H:%M:%S') + '  ' + text
        self.records.append(line)
        self.records = self.records[-12:]
        LOG.info(text)

    def start_worker(self):
        # The supervisor and launchd remain alive through the entire stop interval.
        self.traffic_path.unlink(missing_ok=True)
        self.child = subprocess.Popen([sys.executable, '-u', str(self.worker_script), '--config', str(self.config_path)], cwd=self.base)
        self.last_worker_start = time.monotonic()
        self.policy.worker_started(self.last_worker_start)
        self.record('转发服务已启动。')

    def stop_worker(self):
        if self.child is not None:
            if self.child.poll() is None:
                self.child.terminate()
                try:
                    self.child.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    self.child.kill()
                    self.child.wait(timeout=3)
            self.child = None

    def traffic(self, now):
        try:
            data = json.loads(self.traffic_path.read_text())
            if self.child is None or data.get('worker_pid') != self.child.pid or not 0 <= now-data['time'] <= 20:
                return {}
            return data['clients']
        except (OSError, ValueError, KeyError, TypeError):
            return {}

    def publish(self, now):
        for event in self.policy.events[self.event_offset:]:
            self.record(event)
        self.event_offset = len(self.policy.events)
        phase = self.policy.phase
        remaining = max(0, int((self.pause_deadline or now)-now)+1) if self.pause_deadline else 0
        messages = {
            'idle': ('观察期已满，未观察到续传；服务保持开启' if self.policy.last_result == '未观察到续传' else '等待游戏下载'), 'disabled': '已关闭',
            'monitoring': f'监控中 · {self.policy.speed/1_000_000:.2f} MB/s',
            'checking': f'检查恢复效果 · {self.policy.speed/1_000_000:.2f} MB/s',
            'pausing': f'正在恢复 · {remaining} 秒后重新启动',
            'waiting': f'服务已启动，等待 Switch 自动续传（观察上限 {self.policy.wait_seconds:g} 秒）',
            'limited': '已达到恢复次数上限，保留当前连接继续下载',
        }
        if phase in ('monitoring', 'checking') and self.policy.low_elapsed:
            messages[phase] += f' · 已低速 {int(self.policy.low_elapsed)} 秒'
        state = {'supervisor_pid': os.getpid(), 'worker_pid': self.child.pid if self.child and self.child.poll() is None else None,
                 'time': now, 'phase': phase, 'message': messages.get(phase, phase),
                 'speed_mbps': self.policy.speed/1_000_000, 'low_seconds': int(self.policy.low_elapsed),
                 'attempts': len(self.policy.attempts), 'events': self.records, 'enabled': self.enabled, 'last_result': self.policy.last_result,
                 'rule': {'threshold_kbps': self.policy.threshold/1000, 'low_seconds': self.policy.low_seconds, 'pause_seconds': self.pause_seconds, 'wait_seconds': self.policy.wait_seconds},
                 'attempts_epoch': [time.time()-(now-t) for t in self.policy.attempts]}
        # Epoch budget timestamps are persisted only with an actual publication.
        signature = {k: v for k, v in state.items() if k not in ('time', 'attempts_epoch')}
        if signature == self.last_publication and 0 <= now-self.last_publication_time < 15:
            return
        self.last_publication = signature
        self.last_publication_time = now
        tmp = self.state_path.with_suffix('.tmp')
        tmp.write_text(json.dumps(state, ensure_ascii=False))
        tmp.replace(self.state_path)

    def run(self):
        try:
            self.apply_config()
            self.start_worker()
            while not self.stop_event.is_set():
                now = time.monotonic()
                try:
                    self.apply_config()
                except (OSError, ValueError):
                    LOG.warning('configuration_read_failed')
                if self.pause_deadline is not None:
                    if now >= self.pause_deadline:
                        self.pause_deadline = None
                        self.start_worker()
                        self.next_sample = 0
                elif self.child is None or self.child.poll() is not None:
                    self.record('转发进程退出，重新启动。')
                    # Bound crash loops; user Stop sets the event and never starts again.
                    if now-self.last_worker_start >= 10:
                        self.start_worker()
                elif now >= self.next_sample:
                    self.next_sample = now+self.sample_seconds
                    try:
                        self.apply_config()
                    except (OSError, ValueError):
                        LOG.warning('configuration_read_failed')
                    reason = self.policy.tick(now, self.traffic(now), self.enabled)
                    if reason:
                        self.stop_worker()
                        self.pause_started = time.monotonic()
                        self.pause_deadline = self.pause_started+self.pause_seconds
                        self.record(f'自动恢复：{reason}；停止 {self.pause_seconds:g} 秒后启动。')
                self.publish(time.monotonic())
                # Policy/config stays on a 5s cadence; only the stop countdown needs 1s.
                self.stop_event.wait(1 if self.pause_deadline is not None else min(5, self.sample_seconds))
        finally:
            self.stop_worker()
            self.policy.phase = 'stopped'
            self.record('监控与转发已停止。')
            self.publish(time.monotonic())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    base = Path(args.config).parent
    handler = RotatingFileHandler(base/'recovery.log', maxBytes=1_000_000, backupCount=2)
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    LOG.addHandler(handler)
    LOG.setLevel(logging.INFO)
    supervisor = Supervisor(args.config)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: supervisor.stop_event.set())
    supervisor.run()


if __name__ == '__main__':
    main()
