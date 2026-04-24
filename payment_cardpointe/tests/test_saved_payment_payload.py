import importlib.util
import os
import sys
import types
import unittest

CURRENT_DIR = os.path.dirname(__file__)
MODULE_PATH = os.path.abspath(os.path.join(CURRENT_DIR, '..', 'models', 'payment_transaction.py'))


def _install_odoo_stubs():
    if 'odoo' not in sys.modules:
        odoo_module = types.ModuleType('odoo')
        odoo_module._ = lambda value: value
        odoo_module.models = types.SimpleNamespace(Model=object)
        sys.modules['odoo'] = odoo_module

    if 'odoo.exceptions' not in sys.modules:
        exceptions_module = types.ModuleType('odoo.exceptions')

        class UserError(Exception):
            pass

        exceptions_module.UserError = UserError
        sys.modules['odoo.exceptions'] = exceptions_module

    if 'odoo.addons' not in sys.modules:
        addons_module = types.ModuleType('odoo.addons')
        addons_module.__path__ = []
        sys.modules['odoo.addons'] = addons_module

    if 'odoo.addons.payment' not in sys.modules:
        payment_module = types.ModuleType('odoo.addons.payment')
        payment_module.__path__ = []
        sys.modules['odoo.addons.payment'] = payment_module

    if 'odoo.addons.payment.utils' not in sys.modules:
        utils_module = types.ModuleType('odoo.addons.payment.utils')
        utils_module.generate_access_token = lambda *args, **kwargs: 'test-token'
        sys.modules['odoo.addons.payment.utils'] = utils_module


_install_odoo_stubs()

spec = importlib.util.spec_from_file_location('payment_cardpointe.models.payment_transaction', MODULE_PATH)
payment_transaction = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = payment_transaction
spec.loader.exec_module(payment_transaction)


class _ProviderStub:
    def __init__(self):
        self.cardpointe_mid = '800000009999'
        self.requested_payload = None

    def with_context(self, **kwargs):
        return self

    def _cardpointe_request(self, method, endpoint, payload=None):
        self.requested_payload = dict(payload or {})
        return {
            'ok': True,
            'data': {
                'respstat': 'A',
                'retref': '1234567890',
                'respcode': '000',
                'resptext': 'Approval',
            },
        }


class _TokenStub:
    def __init__(self, token_id=9, provider_ref='profile123:account456'):
        self.id = token_id
        self.provider_ref = provider_ref


class _TxStub:
    def __init__(self, operation='online'):
        self.provider_code = 'cardpointe'
        self.operation = operation
        self.state = 'draft'
        self.provider_reference = None
        self.provider_id = _ProviderStub()
        self.currency_id = types.SimpleNamespace(name='USD')
        self.env = types.SimpleNamespace(context={})
        self.amount = 42.0
        self.reference = 'TX-TEST-001'
        self.token_id = _TokenStub()
        self.done_message = None
        self.error_message = None

    def ensure_one(self):
        return self

    def _set_done(self, state_message=None):
        self.done_message = state_message

    def _set_error(self, message):
        self.error_message = message

    def _cardpointe_get_ecomind(self, flow=None):
        return payment_transaction.PaymentTransaction._cardpointe_get_ecomind(self, flow=flow)

    def _cardpointe_build_saved_token_auth_payload(self, flow=None):
        return payment_transaction.PaymentTransaction._cardpointe_build_saved_token_auth_payload(
            self, flow=flow
        )

    def _cardpointe_set_provider_reference(self, raw):
        return payment_transaction.PaymentTransaction._cardpointe_set_provider_reference(self, raw)

    def _cardpointe_done(self, message=None):
        return payment_transaction.PaymentTransaction._cardpointe_done(self, message=message)

    def _cardpointe_fail(self, message, code=None):
        return payment_transaction.PaymentTransaction._cardpointe_fail(self, message, code=code)


class TestSavedTokenAuthPayload(unittest.TestCase):

    def test_saved_token_auth_payload_for_customer_initiated(self):
        tx = _TxStub(operation='online')

        result = payment_transaction.PaymentTransaction._cardpointe_build_saved_token_auth_payload(tx, flow='web')

        self.assertEqual(result['cof'], 'C')
        self.assertEqual(result['cofscheduled'], 'N')
        self.assertEqual(result['ecomind'], 'E')

    def test_saved_token_auth_payload_for_merchant_initiated_offline(self):
        tx = _TxStub(operation='offline')

        result = payment_transaction.PaymentTransaction._cardpointe_build_saved_token_auth_payload(tx)

        self.assertEqual(result['cof'], 'M')
        self.assertEqual(result['cofscheduled'], 'Y')
        self.assertEqual(result['ecomind'], 'R')

    def test_charge_from_payment_token_uses_saved_token_auth_payload(self):
        tx = _TxStub(operation='offline')
        payment_token = _TokenStub(provider_ref='saved_profile:saved_account')

        result = payment_transaction.PaymentTransaction._cardpointe_charge_from_payment_token(
            tx, payment_token, flow='telephone'
        )

        self.assertTrue(result['ok'])
        payload = tx.provider_id.requested_payload
        self.assertIsNotNone(payload)
        self.assertEqual(payload['profile'], 'saved_profile/saved_account')
        self.assertEqual(payload['cof'], 'M')
        self.assertEqual(payload['cofscheduled'], 'Y')
        self.assertEqual(payload['ecomind'], 'T')


if __name__ == '__main__':
    unittest.main()