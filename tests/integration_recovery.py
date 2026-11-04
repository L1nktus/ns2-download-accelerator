"""Isolated recovery lifecycle test. Does not use the real service or Switch."""
from pathlib import Path
import json
import socket
import tempfile
import threading
import time
from supervisor import Supervisor
from recovery import RecoveryPolicy

with tempfile.TemporaryDirectory(prefix='ns2-recovery-test-') as temp:
    root=Path(temp)
    with socket.socket() as reserve:
        reserve.bind(('127.0.0.1',0));port=reserve.getsockname()[1]
    config=root/'config.json';config.write_text(json.dumps({'auto_recovery':True,'port':port}))
    worker=root/'fake_worker.py'
    worker.write_text('''import json,os,socket,threading,time
from pathlib import Path
base=Path(__file__).parent
cfg=json.loads((base/'config.json').read_text())
s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);s.bind(('127.0.0.1',cfg['port']));s.listen()
def accept():
 while True:
  c,_=s.accept();c.sendall(b'ready');c.close()
threading.Thread(target=accept,daemon=True).start()
while True:
 d={'worker_pid':os.getpid(),'time':time.monotonic(),'clients':{'192.168.50.254':{'total_bytes':2000000,'active_downloads':1}}}
 t=base/'runtime/traffic.tmp';t.write_text(json.dumps(d));t.replace(base/'runtime/traffic.json');time.sleep(.1)
''')
    # Shorten only the qualification period, preserve the requested real 15-second stop.
    policy=RecoveryPolicy(low_seconds=.2,sample_window=.1,wait_seconds=240)
    service=Supervisor(config,worker_script=worker,policy=policy,pause_seconds=15,sample_seconds=.1)
    thread=threading.Thread(target=service.run);thread.start()
    def online():
        try:
            with socket.create_connection(('127.0.0.1',port),.2) as s:return s.recv(5)==b'ready'
        except OSError:return False
    deadline=time.monotonic()+8
    while time.monotonic()<deadline and service.pause_deadline is None:time.sleep(.05)
    assert service.pause_deadline is not None,'Recovery was not triggered'
    assert not online(),'Listener must close during recovery'
    stopped_at=time.monotonic();print('Isolated real-time test: listener stopped; waiting full 15 seconds.',flush=True)
    while time.monotonic()-stopped_at<14:
        assert not online(),'Worker restarted before 15 seconds'
        time.sleep(.25)
    deadline=time.monotonic()+5
    while time.monotonic()<deadline and not online():time.sleep(.05)
    elapsed=time.monotonic()-stopped_at
    assert online() and elapsed>=14.7,(online(),elapsed)
    print(f'Listener returned after {elapsed:.2f}s; supervisor stayed alive.',flush=True)
    # Manually stop during another automatic pause: never restart the worker.
    service.policy.pending=False;service.policy.phase='monitoring';service.policy.reset_worker()
    deadline=time.monotonic()+8
    while time.monotonic()<deadline and service.pause_deadline is None:time.sleep(.05)
    assert service.pause_deadline is not None,'Second recovery was not triggered'
    service.stop_event.set();thread.join(5)
    assert not thread.is_alive() and not online(),'Manual Stop failed to cancel restart'
    print('Manual Stop during recovery cancelled the pending restart; no worker left.',flush=True)
