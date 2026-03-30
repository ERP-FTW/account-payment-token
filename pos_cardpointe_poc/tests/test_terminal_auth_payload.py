import importlib.util
import pathlib
import sys
import types
import unittest


def _load_terminal_module():
    http_module = types.ModuleType('odoo.addons.payment_cardpointe_base.services.http')
    http_module.CardPointeRequestError = Exception
    http_module.redact_payload = lambda payload: payload
    http_module.request_json = lambda *args, **kwargs: (200, {}, '{}', {})
    http_module.safe_log_headers = lambda headers: headers
    http_module.safe_truncate = lambda value, limit=200: value

    money_module = types.ModuleType('odoo.addons.payment_cardpointe_base.services.money')
    money_module.dollars_to_implied_cents = lambda amount: str(int(round(float(amount) * 100)))

    normalize_module = types.ModuleType('odoo.addons.payment_cardpointe_base.services.normalize')
    normalize_module.normalize_terminal_authcard_response = (
        lambda http_status, data: {'ok': True, 'status': 'ok', 'resptext': 'ok'}
    )

    sys.modules.setdefault('odoo', types.ModuleType('odoo'))
    sys.modules.setdefault('odoo.addons', types.ModuleType('odoo.addons'))
    sys.modules.setdefault('odoo.addons.payment_cardpointe_base', types.ModuleType('odoo.addons.payment_cardpointe_base'))
    sys.modules.setdefault(
        'odoo.addons.payment_cardpointe_base.services',
        types.ModuleType('odoo.addons.payment_cardpointe_base.services'),
    )
    sys.modules['odoo.addons.payment_cardpointe_base.services.http'] = http_module
    sys.modules['odoo.addons.payment_cardpointe_base.services.money'] = money_module
    sys.modules['odoo.addons.payment_cardpointe_base.services.normalize'] = normalize_module

    module_path = pathlib.Path(__file__).resolve().parents[1] / 'services' / 'cardpointe_terminal.py'
    spec = importlib.util.spec_from_file_location('cardpointe_terminal', module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Config:
    def __init__(self, device_type, print_receipt_on_terminal):
        self.base_url = 'https://bolt-uat.cardpointe.com/api'
        self.auth_key = 'secret'
        self.merchant_id = 'mid'
        self.device_serial = 'hsn'
        self.request_timeout_seconds = 30
        self.device_type = device_type
        self.print_receipt_on_terminal = print_receipt_on_terminal


class TestTerminalAuthPayload(unittest.TestCase):
    def setUp(self):
        self.module = _load_terminal_module()

    def test_auth_payload_sends_print_receipt_for_flex(self):
        client = self.module.CardPointeTerminalClient(_Config('clover_flex', True))
        seen = {}

        def fake_request(method, path, payload=None, session_key=None, timeout=30):
            seen['payload'] = dict(payload or {})
            return {'ok': False, 'status': 'error', 'message': 'stop'}

        client._request = fake_request
        client.auth_card_with_session(1.00, 'ORDER-1', 'SESSION-1', include_signature=True)

        self.assertIn('printReceipt', seen['payload'])
        self.assertTrue(seen['payload']['printReceipt'])

    def test_auth_payload_omits_print_receipt_for_pocket(self):
        client = self.module.CardPointeTerminalClient(_Config('clover_pocket', True))
        seen = {}

        def fake_request(method, path, payload=None, session_key=None, timeout=30):
            seen['payload'] = dict(payload or {})
            return {'ok': False, 'status': 'error', 'message': 'stop'}

        client._request = fake_request
        client.auth_card_with_session(1.00, 'ORDER-1', 'SESSION-1', include_signature=False)

        self.assertNotIn('printReceipt', seen['payload'])


if __name__ == '__main__':
    unittest.main()