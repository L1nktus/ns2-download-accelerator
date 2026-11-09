import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import recovery
from recovery import RecoveryPolicy
import supervisor
import importlib.util
spec=importlib.util.spec_from_file_location('proxy',Path(__file__).resolve().parents[1]/'src/backend/proxy_accel.py')
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)

CLIENT='192.168.50.254'

def snapshot(total,active=1):
    return {CLIENT:{'total_bytes':total,'active_downloads':active}}

class RecoveryTests(unittest.TestCase):
    def test_requested_defaults_are_100kb_60s_15s_180s(self):
        policy=RecoveryPolicy()
        self.assertEqual(policy.threshold,100000);self.assertEqual(policy.low_seconds,60)
        self.assertEqual(policy.wait_seconds,180);self.assertEqual(recovery.STOP_SECONDS,15)
    def test_custom_threshold_and_duration_are_applied(self):
        policy=RecoveryPolicy(threshold=1000000,low_seconds=60,sample_window=1)
        policy.configure({'recovery_threshold_kbps':50,'recovery_low_seconds':20,'recovery_wait_seconds':90})
        self.assertEqual((policy.threshold,policy.low_seconds,policy.wait_seconds),(50000,20,90))
        policy.worker_started(0)
        for t in range(21):self.assertIsNone(policy.tick(t,snapshot(2_000_000+10000*t)))
        self.assertIsNotNone(policy.tick(21,snapshot(2_210_000)))
    def test_rule_change_resets_old_low_speed_timer(self):
        policy=RecoveryPolicy(threshold=1000000)
        for t in range(0,60,5):policy.tick(t,snapshot(2_000_000))
        policy.configure({'recovery_threshold_kbps':100,'recovery_low_seconds':60})
        self.assertEqual(policy.clients,{})
        self.assertIsNone(policy.tick(60,snapshot(2_000_000)))
    def test_wait_deadline_updates_from_original_worker_start(self):
        policy=RecoveryPolicy();policy.begin_restart(0,CLIENT,'test');policy.worker_started(15)
        policy.configure({'recovery_wait_seconds':90})
        self.assertEqual(policy.wait_deadline,105)
        policy.tick(104,{})
        self.assertTrue(policy.pending)
        policy.tick(105,{})
        self.assertFalse(policy.pending);self.assertEqual(len(policy.attempts),1)
        self.assertEqual(policy.last_result,'未观察到续传')
    def test_running_pause_deadline_updates_without_worker_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);conf=root/'config.json'
            conf.write_text(json.dumps({'recovery_pause_seconds':45}))
            sup=supervisor.Supervisor(conf)
            sup.pause_started=100;sup.pause_deadline=130
            sup.apply_config()
            self.assertEqual(sup.pause_deadline,145)
            self.assertIsNone(sup.child)
    def test_recovery_setting_validation(self):
        for key in recovery.RECOVERY_DEFAULTS:
            for bad in (0,-1,True,'15',1.5,None):
                with self.assertRaises(ValueError):recovery.validate_recovery_options({key:bad})
            recovery.validate_recovery_options({key:1})
    def test_full_minute_required_and_ten_second_window(self):
        policy=RecoveryPolicy(threshold=1_000_000,wait_seconds=240)
        policy.worker_started(0)
        for t in range(0,70,5):self.assertIsNone(policy.tick(t,snapshot(2_000_000+100_000*t)))
        self.assertEqual(policy.low_elapsed,55)
        self.assertIsNotNone(policy.tick(70,snapshot(9_000_000)))
        self.assertEqual(policy.phase,'pausing');self.assertEqual(len(policy.attempts),1)
    def test_parallel_game_connections_aggregate(self):
        policy=RecoveryPolicy(threshold=1_000_000,wait_seconds=240);policy.worker_started(0)
        # Each content connection could be <1 MB/s; total on this console is 1.4 MB/s.
        for t in range(0,150,5):
            self.assertIsNone(policy.tick(t,snapshot(2_000_000+1_400_000*t,active=2)))
        self.assertEqual(policy.attempts,[])
    def test_zero_speed_active_download_triggers_but_idle_does_not(self):
        for active,expected in ((1,True),(0,False)):
            policy=RecoveryPolicy(threshold=1_000_000,wait_seconds=240);policy.worker_started(0);trigger=None
            for t in range(0,75,5):trigger=policy.tick(t,snapshot(2_000_000,active)) or trigger
            self.assertEqual(bool(trigger),expected)
    def test_short_gaps_between_chunks_do_not_reset_low_timer(self):
        policy=RecoveryPolicy(threshold=1_000_000,wait_seconds=240);policy.worker_started(0);trigger=None
        for t in range(0,75,5):
            trigger=policy.tick(t,snapshot(2_000_000+100000*t,active=0 if t in (25,50) else 1)) or trigger
        self.assertTrue(trigger)
    def test_handshake_only_never_arms(self):
        policy=RecoveryPolicy(threshold=1_000_000,wait_seconds=240);policy.worker_started(0)
        for t in range(0,300,5):self.assertIsNone(policy.tick(t,snapshot(20000)))
    def test_good_speed_resets_low_timer(self):
        policy=RecoveryPolicy(threshold=1_000_000,wait_seconds=240);policy.worker_started(0)
        total=2_000_000
        for t in range(0,150,5):
            rate=2_000_000 if 40<=t<=60 else 100_000
            total+=rate*5
            self.assertIsNone(policy.tick(t,snapshot(total)))
            if t==120:break
        self.assertLess(policy.low_elapsed,60)
    def test_four_minute_grace_without_repeated_restarts(self):
        policy=RecoveryPolicy(threshold=1_000_000,wait_seconds=240);policy.begin_restart(0,CLIENT,'test');policy.worker_started(30)
        for t in range(30,270,5):self.assertIsNone(policy.tick(t,{}))
        self.assertEqual(len(policy.attempts),1)
        self.assertIsNone(policy.tick(270,{}));self.assertEqual(len(policy.attempts),1)
        self.assertFalse(policy.pending);self.assertEqual(policy.last_result,'未观察到续传')
    def test_recovery_success_is_sustained_data_not_connected(self):
        policy=RecoveryPolicy(threshold=1_000_000,wait_seconds=240);policy.begin_restart(0,CLIENT,'test');policy.worker_started(30)
        self.assertIsNone(policy.tick(35,snapshot(0)))
        self.assertEqual(policy.phase,'waiting')
        for t in range(40,85,5):policy.tick(t,snapshot(2_000_000+2_000_000*(t-40)))
        self.assertFalse(policy.pending);self.assertEqual(policy.last_result,'速度已恢复')
    def test_completed_transfer_after_recovery_never_forces_another_restart(self):
        policy=RecoveryPolicy(threshold=1_000_000,wait_seconds=240);policy.begin_restart(0,CLIENT,'test');policy.worker_started(30)
        policy.tick(40,snapshot(2_000_000))
        policy.tick(45,snapshot(2_000_000,active=0))
        policy.tick(60,snapshot(2_000_000,active=0))
        self.assertFalse(policy.pending)
        for t in range(50,600,5):self.assertIsNone(policy.tick(t,snapshot(2_000_000,active=0)))
        self.assertEqual(len(policy.attempts),1)
    def test_rolling_attempt_budget_keeps_forwarding(self):
        policy=RecoveryPolicy(threshold=1_000_000,wait_seconds=240);self.assertTrue(policy.begin_restart(0,CLIENT,'1'))
        policy.worker_started(30);self.assertTrue(policy.begin_restart(100,CLIENT,'2'))
        policy.worker_started(130)
        for t in range(130,220,5):self.assertIsNone(policy.tick(t,snapshot(2_000_000)))
        self.assertEqual(policy.phase,'limited');self.assertEqual(len(policy.attempts),2)
        self.assertTrue(policy.begin_restart(1801,CLIENT,'budget freed'))
    def test_disabling_clears_pending_low_state(self):
        policy=RecoveryPolicy(threshold=1_000_000,wait_seconds=240);policy.begin_restart(0,CLIENT,'test');policy.worker_started(30)
        self.assertIsNone(policy.tick(40,snapshot(2_000_000),False))
        self.assertEqual(policy.phase,'disabled');self.assertFalse(policy.pending)
    def test_worker_generation_resets_samples(self):
        policy=RecoveryPolicy(threshold=1_000_000,wait_seconds=240);policy.worker_started(0)
        for t in range(0,60,5):policy.tick(t,snapshot(2_000_000))
        policy.worker_started(60)
        for t in range(60,125,5):self.assertIsNone(policy.tick(t,snapshot(2_000_000)))
    def test_only_content_hosts_are_monitored(self):
        for host in ('atum-5a8.p01.lp1.d4c.srv.nintendo.net','atum.hac.lp1.d4c.nintendo.net','scsi-download.lp1.scsi.srv.nintendo.net'):
            self.assertTrue(recovery.game_content_host(host))
        for host in ('accounts.nintendo.com','ctest-dl-lp1.cdn.nintendo.net','p01.lp1.savanna.srv.nintendo.net','atum-5a8.attacker.net'):
            self.assertFalse(recovery.game_content_host(host))
    def test_meter_sums_connections_and_ignores_accounts(self):
        with tempfile.TemporaryDirectory() as temp:
            meter=p.TrafficMeter(Path(temp)/'traffic.json')
            a=meter.open(CLIENT,'atum-a.p01.lp1.d4c.srv.nintendo.net')
            b=meter.open(CLIENT,'atum-b.p01.lp1.d4c.srv.nintendo.net')
            other=meter.open(CLIENT,'accounts.nintendo.com')
            meter.add(a,600);meter.add(b,700);meter.add(other,1000000)
            meter.close(a);meter.publish()
            data=json.loads(meter.path.read_text())['clients'][CLIENT]
            self.assertEqual(data,{'total_bytes':1300,'active_downloads':1})
    def test_meter_callback_counts_successful_socket_transfer(self):
        client,a=socket.socketpair();b,origin=socket.socketpair();seen=[]
        thread=threading.Thread(target=p.splice,args=(a,b,1),kwargs={'on_receive':seen.append})
        try:
            thread.start();origin.sendall(b'payload');self.assertEqual(client.recv(7),b'payload')
            origin.shutdown(socket.SHUT_WR);client.shutdown(socket.SHUT_WR);thread.join(2)
            self.assertFalse(thread.is_alive());self.assertEqual(sum(seen),7)
        finally:
            for sock in (client,a,b,origin):sock.close()
    def test_idle_heartbeat_is_sparse_but_changes_publish_immediately(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);conf=root/'config.json';conf.write_text('{}')
            sup=supervisor.Supervisor(conf)
            sup.publish(100)
            self.assertEqual(json.loads(sup.state_path.read_text())['time'],100)
            sup.publish(105)
            self.assertEqual(json.loads(sup.state_path.read_text())['time'],100)
            sup.publish(115)
            self.assertEqual(json.loads(sup.state_path.read_text())['time'],115)
            sup.policy.phase='stopped';sup.publish(116)
            self.assertEqual(json.loads(sup.state_path.read_text())['phase'],'stopped')
    def test_meter_wakes_on_tunnel_and_listener_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            meter=p.TrafficMeter(Path(temp)/'traffic.json')
            self.assertEqual(meter.sample_interval(),15)
            token=meter.open(CLIENT,'atum-a.p01.lp1.d4c.srv.nintendo.net')
            self.assertTrue(meter.changed.is_set());self.assertEqual(meter.sample_interval(),2)
            meter.changed.clear();meter.close(token)
            self.assertTrue(meter.changed.is_set());self.assertEqual(meter.sample_interval(),15)
            meter.changed.clear();meter.set_listener('192.168.50.1',True)
            self.assertTrue(meter.changed.is_set())
            self.assertTrue(meter.snapshot()['listening'])
            self.assertEqual(meter.snapshot()['address'],'192.168.50.1')
            meter.changed.clear();meter.set_listener('192.168.50.1',True)
            self.assertFalse(meter.changed.is_set())
            meter.set_listener('',False)
            self.assertFalse(meter.snapshot()['listening'])
    def test_idle_worker_heartbeat_fifteen_seconds_old_is_fresh(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);conf=root/'config.json';conf.write_text('{}')
            sup=supervisor.Supervisor(conf)
            class Worker:pid=1234
            sup.child=Worker()
            sup.traffic_path.write_text(json.dumps({'worker_pid':1234,'time':100,'clients':snapshot(2_000_000,0)}))
            self.assertEqual(sup.traffic(115),snapshot(2_000_000,0))
            self.assertEqual(sup.traffic(121),{})

    def test_attempt_budget_persisted_on_supervisor_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);conf=root/'config.json';conf.write_text('{}')
            first=supervisor.Supervisor(conf);first.policy.begin_restart(time.monotonic(),CLIENT,'test');first.publish(time.monotonic())
            second=supervisor.Supervisor(conf)
            self.assertEqual(len(second.policy.attempts),1)
    def test_stale_worker_metrics_are_ignored(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);conf=root/'config.json';conf.write_text('{}')
            sup=supervisor.Supervisor(conf)
            class Worker:pid=1234
            sup.child=Worker()
            for pid,stamp in ((1235,time.monotonic()),(1234,time.monotonic()-26)):
                sup.traffic_path.write_text(json.dumps({'worker_pid':pid,'time':stamp,'clients':snapshot(2_000_000)}))
                self.assertEqual(sup.traffic(time.monotonic()),{})

if __name__=='__main__':unittest.main(verbosity=2)
