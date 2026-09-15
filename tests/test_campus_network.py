import socket
import struct
import unittest
from unittest import mock

import campus_network as network
from netlogin import Netlogin


def dns_packet(address='222.30.148.40', transaction=42, hostname='auth1.ysu.edu.cn'):
    question = b''.join(bytes([len(part)]) + part.encode('ascii') for part in hostname.split('.'))
    question += b'\x00\x00\x01\x00\x01'
    answer = b'\xc0\x0c' + struct.pack('!HHIH', 1, 1, 30, 4) + socket.inet_aton(address)
    return struct.pack('!6H', transaction, 0x8180, 1, 1, 0, 0) + question + answer


def adapter(name='WLAN', kind='wifi', up=True, index=25):
    return {'name': name, 'index': index, 'kind': kind, 'up': up,
            'addresses': ['10.80.62.217'], 'dns_servers': ['202.206.240.12'],
            'gateways': ['10.80.32.1']}


class DnsTests(unittest.TestCase):
    def test_valid_compressed_answer(self):
        self.assertEqual(['222.30.148.40'], network.dns_answers(
            dns_packet(), 42, 'auth1.ysu.edu.cn'))

    def test_proxy_fake_ip_is_rejected(self):
        with self.assertRaises(ValueError):
            network.dns_answers(dns_packet('198.18.0.5'), 42, 'auth1.ysu.edu.cn')

    def test_wrong_transaction_or_question_is_rejected(self):
        for transaction, hostname in [(43, 'auth1.ysu.edu.cn'), (42, 'other.ysu.edu.cn')]:
            with self.assertRaises(ValueError):
                network.dns_answers(dns_packet(), transaction, hostname)

    def test_truncated_packet_and_pointer_cycle_are_rejected(self):
        with self.assertRaises((ValueError, struct.error)):
            network.dns_answers(dns_packet()[:-3], 42, 'auth1.ysu.edu.cn')
        with self.assertRaises(ValueError):
            network.dns_name(b'\xc0\x00', 0)

    def test_cname_chain_is_followed(self):
        host = 'auth1.ysu.edu.cn'
        target = 'portal.ysu.edu.cn'
        qname = b''.join(bytes([len(p)]) + p.encode() for p in host.split('.')) + b'\x00'
        cname = b''.join(bytes([len(p)]) + p.encode() for p in target.split('.')) + b'\x00'
        packet = struct.pack('!6H', 42, 0x8180, 1, 2, 0, 0) + qname + b'\x00\x01\x00\x01'
        packet += b'\xc0\x0c' + struct.pack('!HHIH', 5, 1, 30, len(cname)) + cname
        packet += cname + struct.pack('!HHIH', 1, 1, 30, 4) + socket.inet_aton('222.30.148.40')
        self.assertEqual(['222.30.148.40'], network.dns_answers(packet, 42, host))


class BoundSocketTests(unittest.TestCase):
    def test_dns_uses_adapter_server_and_never_system_resolver(self):
        transport = network.BoundTransport(adapter(), '10.80.62.217')
        sock = mock.MagicMock()
        sock.__enter__.return_value = sock
        sock.recv.return_value = dns_packet()
        with mock.patch.object(transport, 'new_socket', return_value=sock), \
                mock.patch.object(network.secrets, 'randbits', return_value=42), \
                mock.patch.object(socket, 'getaddrinfo', side_effect=AssertionError('system DNS forbidden')):
            self.assertEqual(['222.30.148.40'], transport.resolve('auth1.ysu.edu.cn'))
        sock.connect.assert_called_once_with(('202.206.240.12', 53))

    def test_dns_failure_does_not_fall_back_to_proxy_dns(self):
        transport = network.BoundTransport(adapter(), '10.80.62.217')
        with mock.patch.object(transport, 'new_socket', side_effect=OSError('blocked')), \
                mock.patch.object(socket, 'getaddrinfo') as system_dns:
            with self.assertRaises(OSError):
                transport.resolve('auth1.ysu.edu.cn')
        system_dns.assert_not_called()

    def test_socket_is_bound_to_nic_and_source_address(self):
        transport = network.BoundTransport(adapter(), '10.80.62.217')
        with mock.patch.object(socket, 'socket') as factory:
            transport.new_socket(socket.SOCK_STREAM, 4)
        factory.return_value.setsockopt.assert_called_once_with(
            socket.IPPROTO_IP, 31, struct.pack('!I', 25))
        factory.return_value.bind.assert_called_once_with(('10.80.62.217', 0))

    def test_binding_failure_closes_socket_and_is_not_ignored(self):
        transport = network.BoundTransport(adapter(), '10.80.62.217')
        with mock.patch.object(socket, 'socket') as factory:
            factory.return_value.setsockopt.side_effect = OSError('interface unavailable')
            with self.assertRaises(OSError):
                transport.new_socket(socket.SOCK_STREAM, 4)
        factory.return_value.close.assert_called_once_with()


class SelectionTests(unittest.TestCase):
    def inspect(self, adapters, probe=None, scan=False):
        with mock.patch.object(network.sys, 'platform', 'win32'), \
                mock.patch.object(network, 'adapter_inventory', return_value=adapters), \
                mock.patch.object(network.BoundTransport, 'probe',
                                  side_effect=probe or (lambda: {'portal_reachable': True})):
            return network.inspect_network(dict(network.DEFAULTS), scan=scan)

    def test_healthy_wired_is_preferred_to_healthy_wifi(self):
        report, transport = self.inspect([adapter(), adapter('Ethernet', 'ethernet', index=3)])
        self.assertTrue(report['ok'])
        self.assertEqual('Ethernet', transport.adapter['name'])

    def test_disconnected_ethernet_is_not_reported_as_validated(self):
        report, transport = self.inspect([adapter(), adapter('Ethernet', 'ethernet', up=False)])
        self.assertEqual('WLAN', transport.adapter['name'])
        self.assertFalse(report['adapters'][1]['checked'])
        self.assertFalse(report['adapters'][1]['campus_direct'])

    def test_failed_campus_probes_never_choose_default_route(self):
        report, transport = self.inspect([adapter()], probe=OSError('no campus session'))
        self.assertFalse(report['ok'])
        self.assertIsNone(transport)

    def test_wifi_scan_failure_does_not_block_wired_authentication(self):
        with mock.patch('wifi_scan.scan_wifi', side_effect=OSError('location denied')):
            report, transport = self.inspect([adapter('Ethernet', 'ethernet')], scan=True)
        self.assertTrue(report['ok'])
        self.assertIn('location denied', report['wifi']['error'])

    def test_auth_opener_uses_selected_transport(self):
        client = Netlogin()
        client._transport_ready = True
        client._transport = mock.Mock()
        client._transport.handlers.return_value = []
        with mock.patch('netlogin.sys.platform', 'win32'), \
                mock.patch('netlogin.urllib.request.build_opener'):
            client._new_cookie_openers()
        self.assertEqual(2, client._transport.handlers.call_count)

    def test_failed_preflight_does_not_start_authentication(self):
        client = Netlogin()
        with mock.patch('netlogin.sys.platform', 'win32'), \
                mock.patch.object(network, 'inspect_network', return_value=(
                    {'ok': False, 'message': 'no campus NIC'}, None)), \
                mock.patch.object(client, '_login_auth1') as login:
            state, message = client.ensure_login('student', 'secret', '1')
        self.assertFalse(state)
        self.assertEqual('no campus NIC', message)
        login.assert_not_called()


if __name__ == '__main__':
    unittest.main()
