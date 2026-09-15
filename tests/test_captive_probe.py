import unittest
from unittest import mock

from campus_network import BoundTransport


def response(url, body=b''):
    result = mock.MagicMock()
    result.__enter__.return_value = result
    result.geturl.return_value = url
    result.read.return_value = body
    return result


class CaptiveProbeTests(unittest.TestCase):
    def setUp(self):
        self.transport = BoundTransport({'index': 15}, '10.20.31.134')

    def run_probe(self, responses):
        opener = mock.Mock()
        opener.open.side_effect = responses
        with mock.patch('campus_network.urllib.request.build_opener', return_value=opener), \
                mock.patch.object(self.transport, 'resolve', return_value=['222.30.148.40']):
            result = self.transport.probe()
        return result, opener

    def test_wired_script_redirect_uses_browser_header_and_bound_opener(self):
        result, opener = self.run_probe([
            response('http://124.124.124.124/', b"<script>top.self.location.href='https://auth1.ysu.edu.cn/eportal/index.jsp?a=1&amp;b=2'</script>"),
            response('https://auth1.ysu.edu.cn/portal/portal-main?sessionId=test&userIp=10.20.31.134')])
        self.assertTrue(result['campus_session_available'])
        self.assertIn('Mozilla/5.0', opener.addheaders[0][1])
        self.assertEqual(opener.open.call_args_list[1].args[0],
                         'https://auth1.ysu.edu.cn/eportal/index.jsp?a=1&b=2')

    def test_existing_direct_session_remains_supported(self):
        result, opener = self.run_probe([response('https://auth1.ysu.edu.cn/?sessionId=test')])
        self.assertTrue(result['portal_reachable'])
        self.assertEqual(opener.open.call_count, 1)

    def test_script_cannot_redirect_probe_to_other_host_or_plain_http(self):
        for target in ['https://example.com/', 'http://auth1.ysu.edu.cn/',
                       'https://auth1.ysu.edu.cn:444/', 'https://user@auth1.ysu.edu.cn/']:
            with self.subTest(target=target), self.assertRaises(OSError):
                self.run_probe([response('http://124.124.124.124/',
                    ('location.href="%s"' % target).encode())])

    def test_wrong_exit_ip_is_rejected(self):
        with self.assertRaisesRegex(OSError, 'IP'):
            self.run_probe([response('https://auth1.ysu.edu.cn/?sessionId=test&userIp=10.20.32.13')])

    def test_page_without_session_or_redirect_fails(self):
        with self.assertRaises(OSError):
            self.run_probe([response('https://auth1.ysu.edu.cn/', b'ordinary webpage')])

    def test_redirect_cycle_is_bounded(self):
        page = response('http://124.124.124.124/', b'location.href="https://auth1.ysu.edu.cn/"')
        with self.assertRaises(OSError):
            self.run_probe([page] * 4)
