import concurrent.futures
import socket
import struct
import threading
import unittest
from unittest.mock import patch
import proxy_accel as p


def parse(request):
    a,b=socket.socketpair()
    try:
        a.sendall(request)
        a.shutdown(socket.SHUT_WR)
        return p.read_request(b)
    finally:
        a.close(); b.close()

class PolicyTests(unittest.TestCase):
    def test_private_fake_and_special_addresses_blocked(self):
        for ip in ('127.0.0.1','192.168.50.1','10.0.0.1','169.254.169.254','198.18.0.7','100.64.0.1','224.0.0.1','0.0.0.0','255.255.255.255'):
            self.assertFalse(p.public_ip(ip),ip)
            with self.assertRaises(PermissionError):p.dial(ip,80,'en0')
    def test_target_boundary(self):
        for host in ('nintendo.net.attacker.com','evilnintendo.net','127.0.0.1','ctest..nintendo.net'):
            with self.assertRaises((ValueError,PermissionError)):p.valid_host(host)
        self.assertEqual(p.valid_host('CTEST.CDN.NINTENDO.NET.'),'ctest.cdn.nintendo.net')
    def test_connect_policy_and_buffered_tls(self):
        tail=b'\x16\x03\x01hello'
        self.assertEqual(parse(b'CONNECT accounts.nintendo.com:443 HTTP/1.1\r\n\r\n'+tail)[3],tail)
        for target in (b'127.0.0.1:443',b'accounts.nintendo.com:22',b'example.com:443'):
            with self.assertRaises((PermissionError,ValueError)):parse(b'CONNECT '+target+b' HTTP/1.1\r\n\r\n')
    def test_http_rewrite_and_secret_header_removal(self):
        result=parse(b'GET http://ctest.cdn.nintendo.net/a?b=1 HTTP/1.1\r\nHost: wrong\r\nProxy-Authorization: secret\r\nUser-Agent: NX NIFM/00\r\nConnection: keep-alive, x-hop\r\nx-hop: foo\r\n\r\n')
        request=result[3]
        self.assertTrue(request.startswith(b'GET /a?b=1 HTTP/1.1\r\nHost: ctest.cdn.nintendo.net\r\n'))
        self.assertIn(b'Connection: close',request)
        self.assertNotIn(b'secret',request);self.assertNotIn(b'x-hop',request)
    def test_malformed_http_framing(self):
        for header in (b'Content-Length: 1\r\nContent-Length: 2',b'Transfer-Encoding: chunked',b'Content-Length: -1',b' Bad: header'):
            with self.assertRaises(ValueError):parse(b'POST http://ctest.cdn.nintendo.net/ HTTP/1.1\r\n'+header+b'\r\n\r\n')
    def test_dns_transaction_ttl_and_public_filter(self):
        q=p.dns_query('ctest.cdn.nintendo.net')
        data=q[:2]+struct.pack('!5H',0x8180,1,2,0,0)+q[12:]
        for ttl,ip in ((31,'23.1.2.3'),(600,'198.18.0.1')):
            data+=b'\xc0\x0c'+struct.pack('!HHIH',1,1,ttl,4)+socket.inet_aton(ip)
        self.assertEqual(p.dns_answers(q,data),[('23.1.2.3',31)])
        with self.assertRaises(ValueError):p.dns_answers(q,b'xx'+data[2:])
    def test_dial_binds_physical_interface(self):
        with patch.object(p.socket,'socket') as factory,patch.object(p.socket,'if_nametoindex',return_value=14):
            p.dial('23.1.2.3',443,'en0')
            factory.return_value.setsockopt.assert_any_call(socket.IPPROTO_IP,25,14)
            factory.return_value.connect.assert_called_once_with(('23.1.2.3',443))
    def test_no_unbound_fallback(self):
        with patch.object(p.socket,'socket') as factory,patch.object(p.socket,'if_nametoindex',side_effect=OSError('down')):
            with self.assertRaises(OSError):p.dial('23.1.2.3',443,'en0')
            factory.return_value.close.assert_called_once()
            factory.return_value.connect.assert_not_called()
    def test_cache_key_has_port_and_failover(self):
        resolver=p.Resolver('en0')
        try:
            with patch.object(p,'doh',return_value=[('23.1.2.3',30)]),patch.object(p,'dial') as dial:
                self.assertEqual(resolver.candidates('ctest.cdn.nintendo.net',443),['23.1.2.3'])
                n=dial.call_count; resolver.candidates('ctest.cdn.nintendo.net',443)
                self.assertEqual(dial.call_count,n)
                resolver.candidates('ctest.cdn.nintendo.net',80)
                self.assertGreater(dial.call_count,n)
            with patch.object(resolver,'candidates',return_value=['23.1.2.3','23.1.2.4']),patch.object(p,'dial',side_effect=[OSError('fail'),object()]):
                self.assertEqual(resolver.connect('ctest.cdn.nintendo.net',443)[1],'23.1.2.4')
        finally:resolver.pool.shutdown()
    def test_half_close_preserves_response(self):
        client,a=socket.socketpair(); b,origin=socket.socketpair()
        pool=concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            result=pool.submit(p.splice,a,b,2)
            client.sendall(b'request');client.shutdown(socket.SHUT_WR)
            self.assertEqual(origin.recv(1024),b'request');self.assertEqual(origin.recv(1024),b'')
            origin.sendall(b'response');origin.shutdown(socket.SHUT_WR)
            self.assertEqual(client.recv(1024),b'response')
            self.assertEqual(result.result(timeout=3),[7,8])
        finally:
            for sock in (client,a,b,origin):sock.close()
            pool.shutdown()
    def test_client_network_restriction(self):
        import ipaddress
        class Fake:network=ipaddress.ip_network('192.168.50.0/24')
        with patch.object(p,'error') as err:
            self.assertFalse(p.Server.verify_request(Fake(),None,('10.0.0.2',1)))
            err.assert_called_once()
            self.assertTrue(p.Server.verify_request(Fake(),None,('192.168.50.50',1)))

if __name__=='__main__':unittest.main(verbosity=2)
