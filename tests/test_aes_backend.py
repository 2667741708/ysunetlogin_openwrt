import base64
import builtins
import unittest
from unittest import mock

from netlogin import Netlogin


class CryptographyBackendTests(unittest.TestCase):
    def test_windows_fallback_matches_aes_known_answer(self):
        original_import = builtins.__import__

        def without_pycrypto(name, *args, **kwargs):
            if name == 'Crypto' or name.startswith('Crypto.'):
                raise ImportError('Exercise cryptography fallback')
            return original_import(name, *args, **kwargs)

        with mock.patch('builtins.__import__', side_effect=without_pycrypto):
            with mock.patch('netlogin.subprocess.Popen') as openssl:
                key = base64.b64encode(b'Thats my Kung Fu').decode('ascii')
                encoded = Netlogin()._aes_encrypt_b64(key, 'Two One Nine Two')
                encrypted = base64.b64decode(encoded)
                self.assertEqual(encrypted[:16].hex(), '29c3505f571420f6402299b31a02d73a')
                self.assertEqual(len(encrypted), 32)
                openssl.assert_not_called()


if __name__ == '__main__':
    unittest.main()
