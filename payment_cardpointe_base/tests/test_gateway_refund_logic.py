import importlib.util
import os
import sys
import types
import unittest

CURRENT_DIR = os.path.dirname(__file__)
BASE_DIR = os.path.abspath(os.path.join(CURRENT_DIR, '..'))
SERVICES_DIR = os.path.join(BASE_DIR, 'services')


def _load_services_module(module_name, filename):
    full_name = f'payment_cardpointe_base.services.{module_name}'
    path = os.path.join(SERVICES_DIR, filename)
    spec = importlib.util.spec_from_file_location(full_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    spec.loader.exec_module(module)
    return module


if 'payment_cardpointe_base' not in sys.modules:
    pkg = types.ModuleType('payment_cardpointe_base')
    pkg.__path__ = [BASE_DIR]
    sys.modules['payment_cardpointe_base'] = pkg
if 'payment_cardpointe_base.services' not in sys.modules:
    pkg = types.ModuleType('payment_cardpointe_base.services')
    pkg.__path__ = [SERVICES_DIR]
    sys.modules['payment_cardpointe_base.services'] = pkg

_load_services_module('http', 'http.py')
_load_services_module('money', 'money.py')
gateway = _load_services_module('gateway', 'gateway.py')
refunds = _load_services_module('refunds', 'refunds.py')


class _GatewayStub:
    def inquire(self, retref, merchid):
        return {'ok': True, 'data': {'retref': retref}}

    def refund(self, merchid, retref, amount):
        return {'ok': True, 'data': {'respstat': 'D', 'respcode': '28', 'resptext': 'Txn not settled'}}

    def void(self, merchid, retref):
        return {'ok': True, 'data': {'respstat': 'A', 'respcode': '000', 'resptext': 'Approval', 'retref': retref}}


class TestGatewayRefundDecision(unittest.TestCase):
    def test_refund_not_settled_falls_back_to_void(self):
        client = _GatewayStub()

        result = refunds.execute_void_or_refund(
            gw_client=client,
            merchid='800000009875',
            retref='343005123105',
            amount='1.00',
            orderid='POS/001',
        )
        self.assertTrue(result['ok'])
        self.assertEqual(result['operation'], 'void')
        self.assertEqual(result['respcode'], '000')

    def test_choose_operation_default_void_when_no_setlstat(self):
        self.assertEqual(refunds.choose_operation_from_inquire({'retref': 'abc'}), 'void')

    def test_inquire_voided_short_circuits_as_success(self):
        class _VoidedClient:
            def inquire(self, retref, merchid):
                return {'ok': True, 'data': {'retref': retref, 'setlstat': 'Voided', 'authcode': 'REVERS'}}

            def void(self, merchid, retref):
                raise AssertionError('void should not be called for already voided txns')

            def refund(self, merchid, retref, amount):
                raise AssertionError('refund should not be called for already voided txns')

        result = refunds.execute_void_or_refund(
            gw_client=_VoidedClient(),
            merchid='800000009875',
            retref='062727076309',
            amount='1.00',
            orderid='POS/002',
        )
        self.assertTrue(result['ok'])
        self.assertEqual(result['operation'], 'void')
        self.assertEqual(result['respcode'], '000')

    def test_gateway_logging_sanitizes_signature_and_receipt(self):
        payload = {
            'signature': 'abcdef',
            'receipt': 'huge-text',
            'emvTagData': 'XYZ',
            'userfields': '{"receipt":"Y","receiptData":"SECRET"}',
            'resptext': 'A' * 500,
        }
        sanitized = gateway.sanitize_for_log(payload)
        self.assertNotIn('signature', sanitized)
        self.assertNotIn('receipt', sanitized)
        self.assertNotIn('emvTagData', sanitized)
        self.assertNotIn('userfields', sanitized)
        self.assertEqual(len(sanitized['resptext']), 303)


if __name__ == '__main__':
    unittest.main()
