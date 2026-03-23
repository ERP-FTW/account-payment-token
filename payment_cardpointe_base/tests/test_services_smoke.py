import importlib.util
import os
import unittest

CURRENT_DIR = os.path.dirname(__file__)
SERVICES_DIR = os.path.abspath(os.path.join(CURRENT_DIR, '..', 'services'))


def _load_module(name, filename):
    path = os.path.join(SERVICES_DIR, filename)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


http = _load_module('cardpointe_http', 'http.py')
money = _load_module('cardpointe_money', 'money.py')


class TestCardPointeServicesSmoke(unittest.TestCase):

    def test_dollars_to_implied_cents(self):
        self.assertEqual(money.dollars_to_implied_cents('1.00'), '100')
        self.assertEqual(money.dollars_to_implied_cents('10.235'), '1024')

    def test_safe_log_headers(self):
        headers = {
            'Authorization': 'Basic abc1234567890',
            'X-CardConnect-SessionKey': 'supersecretkeyvalue',
            'Accept': 'application/json',
        }
        safe = http.safe_log_headers(headers)
        self.assertTrue(str(safe['Authorization']).startswith('masked:'))
        self.assertTrue(str(safe['X-CardConnect-SessionKey']).startswith('masked:'))
        self.assertEqual(safe['Accept'], 'application/json')


if __name__ == '__main__':
    unittest.main()
