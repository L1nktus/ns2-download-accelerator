#!/usr/bin/env python3
"""NS2 macOS proxy, adapted from kasimu/ns2-dns-accel (MIT).
No TLS interception. Direct outbound IPv4 is bound to the configured interface.
"""
import argparse
import concurrent.futures
import fcntl
import http.client
import ipaddress
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import re
import secrets
import select
import socket
import socketserver
import ssl
import struct
import threading
import time
from pathlib import Path
from recovery import game_content_host
from urllib.parse import urlsplit

LOG = logging.getLogger('ns2proxy')
IP_BOUND_IF = 25  # Darwin netinet/in.h
MAX_HEADER = 16384
ROOTS = ('nintendo.net', 'nintendo.com', 'nintendowifi.net', 'nintendo.co.jp', 'nintendo.jp')
RESOLVERS = [('dns.alidns.com', '223.5.5.5'), ('doh.pub', '1.12.12.12')]


def public_ip(value):
    try:
        ip = ipaddress.IPv4Address(value)
        return ip.is_global and not (ip.is_multicast or ip.is_reserved or ip.is_unspecified)
    except ValueError:
        return False


def valid_host(value):
    host = value.lower().rstrip('.')
    if len(host) > 253 or not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', p) for p in host.split('.')):
        raise ValueError('Invalid hostname')
    if not any(host == root or host.endswith('.' + root) for root in ROOTS):
        raise PermissionError('Nintendo hosts only')
    return host


# All remote sockets (DoH, probes, actual downloads) MUST use this bound-interface
# helper. Never add an unbound or system-proxy fallback to "fix" a failed connection.
def dial(ip, port, interface, timeout=5):
    if not public_ip(ip):
        raise PermissionError('Non-public destination')
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout)
        sock.setsockopt(socket.IPPROTO_IP, IP_BOUND_IF, socket.if_nametoindex(interface))
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        sock.connect((ip, port))
        return sock
    except BaseException:
        sock.close()
        raise


def dns_query(host):
    tid = secrets.randbelow(65536)
    labels = b''.join(bytes([len(p)]) + p.encode('ascii') for p in host.split('.'))
    return struct.pack('!6H', tid, 0x100, 1, 0, 0, 0) + labels + b'\0\0\1\0\1'


def skip_name(data, offset):
    for _ in range(128):
        n = data[offset]
        if n & 0xc0 == 0xc0:
            if offset + 1 >= len(data):
                raise ValueError('Short DNS pointer')
            return offset + 2
        if n & 0xc0:
            raise ValueError('Invalid DNS name')
        offset += 1
        if not n:
            return offset
        offset += n
        if offset >= len(data):
            raise ValueError('Short DNS name')
    raise ValueError('Long DNS name')


def dns_answers(query, data):
    if len(data) < 12 or data[:2] != query[:2]:
        raise ValueError('DNS transaction mismatch')
    _, flags, qd, an, _, _ = struct.unpack('!6H', data[:12])
    if not flags & 0x8000 or flags & 0x020f or qd != 1:
        raise ValueError('DNS failure or truncation')
    end = skip_name(data, 12) + 4
    if data[12:end] != query[12:]:
        raise ValueError('DNS question mismatch')
    offset, result = end, []
    for _ in range(min(an, 128)):
        offset = skip_name(data, offset)
        kind, cls, ttl, length = struct.unpack_from('!HHIH', data, offset)
        offset += 10
        raw = data[offset:offset + length]
        if len(raw) != length:
            raise ValueError('Short DNS answer')
        if kind == 1 and cls == 1 and length == 4:
            ip = socket.inet_ntoa(raw)
            if public_ip(ip):
                result.append((ip, min(ttl, 600)))
        offset += length
    return result


def doh(host, resolver, interface):
    name, ip = resolver
    raw = tls = None
    try:
        query = dns_query(host)
        raw = dial(ip, 443, interface, 3)
        tls = ssl.create_default_context().wrap_socket(raw, server_hostname=name)
        tls.sendall((f'POST /dns-query HTTP/1.1\r\nHost: {name}\r\n'
                     'Content-Type: application/dns-message\r\nAccept: application/dns-message\r\n'
                     f'Content-Length: {len(query)}\r\nConnection: close\r\n\r\n').encode() + query)
        response = http.client.HTTPResponse(tls)
        response.begin()
        if response.status != 200:
            raise OSError('DoH HTTP ' + str(response.status))
        return dns_answers(query, response.read(65536))
    except (OSError, ValueError, IndexError, struct.error, http.client.HTTPException) as exc:
        LOG.warning('dns_failed resolver=%s host=%s reason=%s', name, host, type(exc).__name__)
        return []
    finally:
        if tls is not None:
            tls.close()
        if raw is not None:
            raw.close()


class Resolver:
    def __init__(self, interface, optimize=True):
        self.interface = interface
        self.optimize = optimize
        self.cache = {}
        self.lock = threading.Lock()
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=12)

    def candidates(self, host, port):
        key = (host, port)
        with self.lock:
            hit = self.cache.get(key)
            if hit and hit[0] > time.monotonic():
                return hit[1]
        futures = [self.pool.submit(doh, host, up, self.interface) for up in RESOLVERS]
        answers = [item for future in futures for item in future.result()]
        ips = list(dict.fromkeys(ip for ip, _ in answers))[:16]
        if not ips:
            raise OSError('No public DNS answers (no VPN/system-DNS fallback)')

        def measure(ip):
            start = time.monotonic()
            try:
                with dial(ip, port, self.interface, 2):
                    return (time.monotonic() - start) * 1000, ip
            except OSError:
                return float('inf'), ip
        ranked = list(self.pool.map(measure, ips))
        if self.optimize:
            ranked.sort()
        ordered = [ip for ms, ip in ranked if ms != float('inf')]
        if not ordered:
            raise OSError('All CDN connection probes failed')
        ttl = min(ttl for _, ttl in answers)
        LOG.info('resolve host=%s port=%d candidates=%s ttl=%d', host, port,
                 ','.join(f'{ip}:{ms:.0f}ms' for ms, ip in ranked if ms != float('inf')), ttl)
        with self.lock:
            if len(self.cache) >= 256:
                self.cache.clear()
            self.cache[key] = (time.monotonic() + ttl, ordered)
        return ordered

    def connect(self, host, port):
        for ip in self.candidates(host, port):
            try:
                return dial(ip, port, self.interface), ip
            except OSError:
                LOG.warning('connect_retry host=%s ip=%s', host, ip)
        with self.lock:
            self.cache.pop((host, port), None)
        raise OSError('All resolved candidates failed')


def read_request(client):
    data = b''
    while b'\r\n\r\n' not in data:
        part = client.recv(4096)
        if not part:
            raise ValueError('Incomplete request')
        data += part
        if len(data) > MAX_HEADER:
            raise ValueError('Header too large')
    head, tail = data.split(b'\r\n\r\n', 1)
    lines = head.decode('iso-8859-1').split('\r\n')
    method, target, version = lines[0].split()
    if version not in ('HTTP/1.0', 'HTTP/1.1'):
        raise ValueError('Invalid HTTP version')
    headers = []
    for line in lines[1:]:
        key, sep, value = line.partition(':')
        if not sep or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", key) or any(ord(c) < 32 and c != '\t' for c in value):
            raise ValueError('Invalid HTTP header')
        headers.append((key, value.strip()))
    if method == 'CONNECT':
        host, sep, port = target.rpartition(':')
        if not sep or not port.isdigit():
            raise ValueError('CONNECT requires hostname:port')
        host, port = valid_host(host), int(port)
        if port not in (80, 443):
            raise PermissionError('Ports 80/443 only')
        return method, host, port, tail
    if method not in ('GET', 'HEAD', 'POST', 'OPTIONS'):
        raise PermissionError('Unsupported method')
    url = urlsplit(target)
    if url.scheme != 'http' or url.username is not None or url.password is not None or url.fragment:
        raise ValueError('Absolute HTTP URL required')
    host, port = valid_host(url.hostname or ''), url.port or 80
    if port != 80:
        raise PermissionError('HTTP port 80 only')
    values = {}
    for key, value in headers:
        values.setdefault(key.lower(), []).append(value)
    if 'transfer-encoding' in values or len(values.get('content-length', [])) > 1:
        raise ValueError('Unsupported or ambiguous request framing')
    length = values.get('content-length', ['0'])[0]
    if not length.isdigit() or int(length) > 16 * 1024 * 1024:
        raise ValueError('Invalid request size')
    # Exactly one plaintext request per connection; downstream EOF is explicit.
    if len(tail) > int(length):
        raise ValueError('HTTP pipelining not supported')
    excluded = {'host', 'connection', 'proxy-connection', 'proxy-authorization', 'keep-alive', 'upgrade', 'te', 'trailer'}
    excluded.update(token.strip().lower() for v in values.get('connection', []) for token in v.split(','))
    if 'content-length' in excluded:
        raise ValueError('Invalid Connection header')
    path = url.path or '/'
    if url.query:
        path += '?' + url.query
    first = f'{method} {path} {version}\r\nHost: {host}\r\nConnection: close\r\n'
    forwarded = ''.join(f'{k}: {v}\r\n' for k, v in headers if k.lower() not in excluded)
    return method, host, port, (first + forwarded + '\r\n').encode('iso-8859-1') + tail



class TrafficMeter:
    """Aggregate only known game-content hosts, count after delivery to client."""
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.clients = {}
        self.connections = {}
        self.next_id = 0
        self.pid = os.getpid()
        self.changed = threading.Event()
        self.listener_address = ''
        self.listening = False

    def set_listener(self, address, listening):
        with self.lock:
            state = (address or '', bool(listening))
            if state == (self.listener_address, self.listening):
                return
            self.listener_address, self.listening = state
        self.changed.set()

    def open(self, client, host):
        if not game_content_host(host):
            return None
        with self.lock:
            self.next_id += 1
            token = self.next_id
            item = self.clients.setdefault(client, {'total_bytes': 0, 'active_downloads': 0})
            item['active_downloads'] += 1
            self.connections[token] = client
        self.changed.set()
        return token

    def add(self, token, count):
        if token is not None:
            with self.lock:
                client = self.connections.get(token)
                if client:
                    self.clients[client]['total_bytes'] += count

    def close(self, token):
        with self.lock:
            client = self.connections.pop(token, None)
            if client:
                self.clients[client]['active_downloads'] -= 1
        if client:
            self.changed.set()

    def snapshot(self):
        with self.lock:
            return {'worker_pid': self.pid, 'time': time.monotonic(),
                    'listening': self.listening, 'address': self.listener_address,
                    'clients': {k: dict(v) for k, v in self.clients.items()}}

    def publish(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.snapshot()))
        temporary.replace(self.path)

    def sample_interval(self):
        with self.lock:
            return 2 if any(c['active_downloads'] > 0 for c in self.clients.values()) else 15

    def run(self):
        while True:
            # Clear before snapshot so a new tunnel can never lose its wake-up.
            self.changed.clear()
            try:
                self.publish()
            except OSError:
                LOG.warning('telemetry_write_failed')
            self.changed.wait(self.sample_interval())


def splice(a, b, idle=300, on_receive=None):
    readers, counts = [a, b], [0, 0]
    a.settimeout(30)
    b.settimeout(30)
    while readers:
        ready, _, _ = select.select(readers, [], [], idle)
        if not ready:
            break
        for src in ready:
            dst = b if src is a else a
            data = src.recv(131072)
            if not data:
                readers.remove(src)
                try:
                    dst.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
                continue
            dst.sendall(data)
            counts[0 if src is a else 1] += len(data)
            if src is b and on_receive:
                on_receive(len(data))
    return counts


def error(client, status):
    try:
        client.sendall(f'HTTP/1.1 {status}\r\nConnection: close\r\nContent-Length: 0\r\n\r\n'.encode())
    except OSError:
        pass


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        client, upstream, established = self.request, None, False
        token = None
        start = time.monotonic()
        try:
            client.settimeout(10)
            method, host, port, payload = read_request(client)
            upstream, ip = self.server.resolver.connect(host, port)
            LOG.info('open client=%s method=%s host=%s port=%d peer=%s local=%s interface=%s',
                     self.client_address[0], method, host, port, ip, upstream.getsockname()[0], self.server.interface)
            if method == 'CONNECT':
                client.sendall(b'HTTP/1.1 200 Connection Established\r\n\r\n')
            established = True
            if payload:
                upstream.sendall(payload)
            token = self.server.meter.open(self.client_address[0], host)
            sent, received = splice(client, upstream, on_receive=lambda count: self.server.meter.add(token, count))
            LOG.info('close client=%s host=%s sent=%d received=%d seconds=%.2f',
                     self.client_address[0], host, sent + len(payload), received, time.monotonic() - start)
        except PermissionError:
            error(client, '403 Forbidden')
            LOG.warning('denied client=%s reason=target_policy', self.client_address[0])
        except (ValueError, UnicodeError):
            error(client, '400 Bad Request')
        except (OSError, http.client.HTTPException) as exc:
            if not established:
                error(client, '502 Bad Gateway')
            LOG.warning('connection_failed client=%s reason=%s', self.client_address[0], type(exc).__name__)
        finally:
            self.server.meter.close(token)
            if upstream is not None:
                upstream.close()


class Server(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def verify_request(self, request, address):
        allowed = (ipaddress.ip_address(address[0]) in self.network
                   and (not getattr(self, 'allowed_client', '') or address[0] == self.allowed_client))
        if not allowed:
            error(request, '403 Forbidden')
        return allowed

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            error(request, '503 Service Unavailable')
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()


def lan_ip(interface):
    # SIOCGIFADDR on Darwin, never asks a VPN-routed remote destination.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        data = fcntl.ioctl(sock, 0xc0206921, struct.pack('256s', interface.encode()[:15]))
        return socket.inet_ntoa(data[20:24])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    with open(args.config) as f:
        config = json.load(f)
    interface = config['interface']
    network = ipaddress.ip_network(config['allowed_subnet'])
    os.makedirs(os.path.dirname(config['log']), exist_ok=True)
    handler = RotatingFileHandler(config['log'], maxBytes=2_000_000, backupCount=3)
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    LOG.addHandler(handler)
    LOG.setLevel(logging.INFO)
    resolver = Resolver(interface, config.get('optimize_cdn', True))
    meter = TrafficMeter(Path(args.config).parent / 'runtime' / 'traffic.json')
    threading.Thread(target=meter.run, daemon=True).start()
    server, previous = None, None
    try:
        while True:
            try:
                address = lan_ip(interface)
                if ipaddress.ip_address(address) not in network:
                    address = None
            except OSError:
                address = None
            if address != previous or (address and server is None):
                if server:
                    server.server_close()
                    server = None
                meter.set_listener(address, False)
                LOG.info('network interface=%s address=%s allowed_subnet=%s', interface, address, network)
                previous = address
                if address:
                    try:
                        server = Server((address, config['port']), Handler)
                        server.interface, server.network, server.resolver = interface, network, resolver
                        server.meter = meter
                        server.allowed_client = config.get('allowed_client', '')
                        server.slots = threading.BoundedSemaphore(32)
                        server.timeout = 5
                        meter.set_listener(address, True)
                        LOG.info('listening address=%s port=%d outbound=physical-interface-direct', address, config['port'])
                    except OSError as exc:
                        LOG.error('listen_failed reason=%s', type(exc).__name__)
                        time.sleep(5)
            if server:
                server.handle_request()
            else:
                time.sleep(5)
    finally:
        meter.set_listener('', False)
        if server:
            server.server_close()
        resolver.pool.shutdown(wait=False, cancel_futures=True)


if __name__ == '__main__':
    main()
