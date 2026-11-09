#!/usr/bin/env python3
"""JSON bridge for the NS2 Direct app. No power management or VPN integration."""
from pathlib import Path
import argparse
import datetime
import http.client
import importlib.util
import ipaddress
import json
import os
import plistlib
import re
import socket
import ssl
import subprocess
import sys
import time
from recovery import RECOVERY_DEFAULTS, validate_recovery_options

BASE=Path.home()/'Library/Application Support/NS2Proxy'
CONFIG=BASE/'config.json'
LABEL='com.local.ns2proxy'
DOMAIN=f'gui/{os.getuid()}'
AGENT=Path.home()/'Library/LaunchAgents'/f'{LABEL}.plist'
APP_LABEL='com.local.ns2downloadapp'
RUNTIME=BASE/'service.plist'
PYTHON='/opt/homebrew/bin/python3'


def launch(*args):
    return subprocess.run(['/bin/launchctl',*args],text=True,capture_output=True,timeout=12)


def load():
    data=json.loads(CONFIG.read_text())
    data.setdefault('optimize_cdn',True)
    data.setdefault('allowed_client','')
    data.setdefault('launch_at_login',True)
    data.setdefault('auto_recovery',True)
    for key,value in RECOVERY_DEFAULTS.items():data.setdefault(key,value)
    return data


def proxy_module():
    spec=importlib.util.spec_from_file_location('ns2proxy',BASE/'proxy_accel.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def interfaces():
    result=subprocess.run(['/usr/sbin/networksetup','-listallhardwareports'],capture_output=True,text=True,timeout=5).stdout
    rows=[];module=proxy_module()
    for name,device in re.findall(r'Hardware Port: ([^\n]+)\nDevice: (en\d+)',result):
        try: address=module.lan_ip(device)
        except OSError: address=''
        rows.append({'device':device,'name':name,'address':address})
    return rows


def service_data(data):
    return {'Label':LABEL,'ProgramArguments':[PYTHON,'-u',str(BASE/'supervisor.py'),'--config',str(CONFIG)],
            'RunAtLoad':True,'KeepAlive':True,'ThrottleInterval':10,'ExitTimeOut':5,
            'WorkingDirectory':str(BASE),'StandardOutPath':str(BASE/'launchd.log'),
            'StandardErrorPath':str(BASE/'launchd.log'),'Umask':63}


def write_agents(data):
    raw=plistlib.dumps(service_data(data));RUNTIME.write_bytes(raw);RUNTIME.chmod(0o600)
    # The service job is loaded explicitly; login opens the app so Quit owns its lifetime.
    app_agent=AGENT.with_name(APP_LABEL+'.plist')
    AGENT.unlink(missing_ok=True)
    if data['launch_at_login']:
        app_path=os.environ.get('NS2_APP_PATH','/Applications/NS2下载加速.app')
        app=Path(app_path)
        if app.suffix != '.app' or not (app/'Contents/MacOS/NS2Direct').is_file():
            raise ValueError('无法找到登录时启动的 App，请从已安装的 App 保存设置。')
        app_agent.parent.mkdir(parents=True,exist_ok=True)
        app_agent.write_bytes(plistlib.dumps({'Label':APP_LABEL,'ProgramArguments':['/usr/bin/open','-g',str(app)],'RunAtLoad':True}))
        app_agent.chmod(0o600)
    else:app_agent.unlink(missing_ok=True)


def process_state():
    result=launch('print',f'{DOMAIN}/{LABEL}')
    match=re.search(r'\n\s*pid = (\d+)',result.stdout)
    return result.returncode==0 and 'state = running' in result.stdout, int(match.group(1)) if match else None


def recent_log(data,limit=14):
    path=Path(data['log'])
    if not path.exists():return ''
    with path.open('rb') as f:
        f.seek(max(0,path.stat().st_size-24000))
        return '\n'.join(f.read().decode(errors='replace').splitlines()[-limit:])


def traffic_status(running, worker_pid, *, now=None):
    """Read only game byte counters, independently of recovery on/off; reject stale workers."""
    empty={'available':False}
    if not running or not worker_pid:return empty
    try:
        sample=json.loads((BASE/'runtime/traffic.json').read_text())
        stamp=sample['time'];now=time.monotonic() if now is None else now
        if sample['worker_pid']!=worker_pid or not 0 <= now-stamp <= 20:return empty
        clients=sample['clients'].values()
        total=sum(max(0,item['total_bytes']) for item in clients)
        active=sum(max(0,item.get('active_downloads',0)) for item in sample['clients'].values())
        return {'available':True,'worker_pid':worker_pid,'time':stamp,'total_bytes':total,'active_downloads':active}
    except (OSError,ValueError,KeyError,TypeError):return empty


def status():
    data=load();module=proxy_module();running,pid=process_state()
    try:address=module.lan_ip(data['interface'])
    except OSError:address=''
    home=bool(address and ipaddress.ip_address(address) in ipaddress.ip_network(data['allowed_subnet']))
    recovery = None
    try:
        candidate = json.loads((BASE/'runtime/recovery.json').read_text())
        if running and candidate.get('supervisor_pid') == pid and 0 <= time.monotonic()-candidate['time'] <= 25:
            recovery = candidate
    except (OSError, ValueError, KeyError, TypeError):
        pass
    worker_pid = recovery.get('worker_pid') if recovery else pid
    listening=False
    if running and home and worker_pid:

        # Read-only inspection: polling must not make dummy proxy requests/logs.
        check=subprocess.run(['/usr/sbin/lsof','-nP','-a','-p',str(worker_pid),'-iTCP:'+str(data['port']),'-sTCP:LISTEN'],capture_output=True,text=True,timeout=5)
        listening=f'{address}:{data["port"]}' in check.stdout
    return {'config':data,'running':running,'listening':listening,'pid':pid,'address':address,'home_network':home,
            'interfaces':interfaces(),'logs':recent_log(data),'direct_only':True,'power_management':False,'recovery':recovery,'traffic':traffic_status(listening,worker_pid)}


def start():
    data=load();write_agents(data)
    enabled=launch('enable',f'{DOMAIN}/{LABEL}')
    if enabled.returncode:raise RuntimeError(enabled.stderr.strip())
    if not process_state()[0]:
        # A loaded job may be throttled after failure; bootstrap only if absent.
        exists=launch('print',f'{DOMAIN}/{LABEL}').returncode==0
        result=launch('kickstart',f'{DOMAIN}/{LABEL}') if exists else launch('bootstrap',DOMAIN,str(RUNTIME))
        if result.returncode:raise RuntimeError(result.stderr.strip())
    for _ in range(16):
        state=status()
        if state['listening'] or not state['home_network'] or (state['recovery'] and state['recovery']['phase']=='pausing'):return state
        time.sleep(.2)
    raise RuntimeError('代理未开始监听，请检查端口是否被占用。')


def stop():
    result=launch('bootout',f'{DOMAIN}/{LABEL}')
    if result.returncode and launch('print',f'{DOMAIN}/{LABEL}').returncode==0:
        raise RuntimeError(result.stderr.strip())
    return status()


def validate(data):
    if not isinstance(data.get('port'),int) or isinstance(data['port'],bool) or not 1024<=data['port']<=65535:
        raise ValueError('端口需在 1024–65535 之间。')
    if data.get('interface') not in [row['device'] for row in interfaces()]:
        raise ValueError('请选择本机物理网络接口。')
    network=ipaddress.ip_network(data['allowed_subnet'],strict=False)
    if network.version!=4 or not network.is_private or network.prefixlen<16:
        raise ValueError('允许网段必须是 /16 或更小的本地 IPv4 网段。')
    client=data.get('allowed_client','').strip()
    if client and ipaddress.ip_address(client) not in network:
        raise ValueError('Switch 地址必须位于允许的局域网网段内。')
    data.setdefault('auto_recovery',True)
    for key,value in RECOVERY_DEFAULTS.items():data.setdefault(key,value)
    validate_recovery_options(data)
    for key in ('optimize_cdn','launch_at_login','auto_recovery'):
        if not isinstance(data.get(key),bool):raise ValueError('选项值无效。')
    data['allowed_client']=client
    data['allowed_subnet']=str(network)
    data['log']=str(Path.home()/'Library/Logs/NS2Proxy/proxy.log')
    return data


def save(updates):
    old=load();new=dict(old)
    allowed=('interface','port','allowed_subnet','allowed_client','optimize_cdn','launch_at_login','auto_recovery',*RECOVERY_DEFAULTS)
    if any(key not in allowed for key in updates):raise ValueError('未知配置项。')
    new.update(updates);validate(new)
    active=process_state()[0]
    changed=any(old[key]!=new[key] for key in ('interface','port','allowed_subnet','allowed_client','optimize_cdn'))
    if active and changed:
        address=proxy_module().lan_ip(new['interface'])
        if ipaddress.ip_address(address) not in ipaddress.ip_network(new['allowed_subnet']):
            raise ValueError('所选接口当前地址不在允许网段内。')
        if (new['interface'],new['port'])!=(old['interface'],old['port']):
            with socket.socket() as probe:probe.bind((address,new['port']))
    temporary=BASE/'config.tmp';temporary.write_text(json.dumps(new,ensure_ascii=False,indent=2)+'\n');temporary.chmod(0o600);temporary.replace(CONFIG)
    try:
        write_agents(new)
        if active and changed:
            stop();return start()
        return status()
    except Exception:
        CONFIG.write_text(json.dumps(old,indent=2)+'\n');CONFIG.chmod(0o600);write_agents(old)
        if active:
            try:stop();start()
            except Exception:pass
        raise


def proxy_socket(state,target):
    sock=socket.create_connection((state['address'],state['config']['port']),timeout=12)
    sock.sendall(f'CONNECT {target}:443 HTTP/1.1\r\nHost: {target}:443\r\n\r\n'.encode())
    header=b''
    while not header.endswith(b'\r\n\r\n'):
        part=sock.recv(1)
        if not part or len(header)>16384:
            sock.close();raise OSError('CONNECT 响应不完整')
        header+=part
    if not header.startswith(b'HTTP/1.1 200 '):
        sock.close();raise OSError('CONNECT 请求失败')
    return sock


def diagnose(speed=False):
    state=status()
    if not state['listening']:raise RuntimeError('请先启动代理，并确认 Mac 已连接所选局域网。')
    data=state['config'];module=proxy_module();lines=[]
    addresses=module.doh('ctest.cdn.nintendo.net',module.RESOLVERS[0],data['interface'])
    if not addresses:raise RuntimeError('DNS 查询失败，请检查当前网络。')
    with module.dial(addresses[0][0],80,data['interface']) as proof:
        assert proof.getsockopt(socket.IPPROTO_IP,module.IP_BOUND_IF)==socket.if_nametoindex(data['interface'])
        source=proof.getsockname()[0]
    lines.append(f'网络接口：{data["interface"]}；本机地址：{source}。')
    # A diagnostic is a local client and is not exempt from a single-Switch ACL.
    restricted=bool(data['allowed_client'] and data['allowed_client']!=state['address'])
    if restricted:
        lines.append('已限制为指定 Switch 地址，本机不绕过白名单。网络接口检查通过；请在 Switch 上运行连接测试。')
        if speed:raise RuntimeError('单设备白名单已启用，本机测速不可访问代理。暂时清空 Switch 地址后再测速，或在主机上测试。')
        return {'message':'\n'.join(lines),'status':state}
    con=http.client.HTTPConnection(state['address'],data['port'],timeout=15)
    try:
        con.request('GET','http://ctest.p01.ctest.srv.nintendo.net/',headers={'User-Agent':'libcurl (nnNifm; SDK 20.5.4.0)'})
        response=con.getresponse();body=response.read(1024)
        if response.status!=200 or body!=b'ok' or response.getheader('X-Organization')!='Nintendo':raise OSError('任天堂联网检测响应异常')
        lines.append('Switch 2 联网检测：200 / ok。')
    finally:con.close()
    host='accounts.nintendo.com'
    with proxy_socket(state,host) as raw:
        with ssl.create_default_context().wrap_socket(raw,server_hostname=host) as tls:
            tls.sendall(f'HEAD / HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n'.encode())
            response=http.client.HTTPResponse(tls);response.begin()
            if not 200<=response.status<400:raise OSError('任天堂 HTTPS 响应异常')
            lines.append(f'HTTPS 隧道：{response.status}；证书校验通过。')
    if speed:
        con=http.client.HTTPConnection(state['address'],data['port'],timeout=20)
        start_time=time.monotonic();size=0
        try:
            con.request('GET','http://ctest-dl-lp1.cdn.nintendo.net/30m',headers={'User-Agent':'Nintendo NX'})
            response=con.getresponse()
            if response.status!=200:raise OSError('测速下载响应异常')
            while True:
                chunk=response.read(131072)
                if not chunk:break
                size+=len(chunk)
                if time.monotonic()-start_time>35:raise TimeoutError('测速超时（35 秒）')
            elapsed=time.monotonic()-start_time
            if size!=31457280:raise OSError('测速文件下载不完整')
            lines.append(f'30 MiB 官方样本：{size/elapsed/1_000_000:.2f} MB/s，耗时 {elapsed:.2f} 秒。')
            lines.append('这是 Mac 本机短测，不能等同于 Switch 游戏的持续下载速度。')
        finally:con.close()
    return {'message':'\n'.join(lines),'status':status()}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['status','start','stop','restart','save','diagnose','speed']);args=parser.parse_args()
    if args.action=='save':result=save(json.load(sys.stdin))
    elif args.action=='restart':stop();result=start()
    elif args.action in ('diagnose','speed'):result=diagnose(args.action=='speed')
    else:result=globals()[args.action]()
    print(json.dumps({'ok':True,'result':result},ensure_ascii=False))

if __name__=='__main__':
    try:main()
    except Exception as exc:
        print(json.dumps({'ok':False,'error':str(exc)},ensure_ascii=False));sys.exit(1)
