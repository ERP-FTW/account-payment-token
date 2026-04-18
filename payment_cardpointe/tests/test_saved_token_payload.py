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
    def __init__(
        self,
        operation='online',
        initiator=None,
        schedule=None,
        partner_values=None,
        token=None,
    ):
        self.provider_code = 'cardpointe'
        self.operation = operation
        self.state = 'draft'
        self.provider_reference = None
        self.provider_id = _ProviderStub()
        self.currency_id = types.SimpleNamespace(name='USD')
        self.env = types.SimpleNamespace(context={})
        self.amount = 42.0
        self.reference = 'TX-TEST-001'
        self.token_id = token if token is not None else _TokenStub()
        self.done_message = None
        self.error_message = None
        self.stored_credential_initiator = initiator
        self.stored_credential_schedule = schedule
        self._fields = {
            'stored_credential_initiator': object(),
            'stored_credential_schedule': object(),
        }

        partner_values = partner_values or {}
        self.partner_id = types.SimpleNamespace(
            commercial_partner_id=types.SimpleNamespace(
                name=partner_values.get('name', 'Ada Lovelace'),
                email=partner_values.get('email', 'ada@example.com'),
                phone=partner_values.get('phone', '555-0100'),
                street=partner_values.get('address', '123 Main St'),
                city=partner_values.get('city', 'Austin'),
                zip=partner_values.get('postal', '78701'),
                state_id=types.SimpleNamespace(code=partner_values.get('region', 'TX')),
                country_id=types.SimpleNamespace(code=partner_values.get('country', 'US')),
            )
        )

    def ensure_one(self):
        return self

    def write(self, vals):
        for key, value in vals.items():
            setattr(self, key, value)
        return True

    def _set_done(self, state_message=None):
        self.done_message = state_message

    def _set_error(self, message):
        self.error_message = message

    def _get_stored_credential_semantics(self):
        return {
            'initiator': self.stored_credential_initiator,
            'schedule': self.stored_credential_schedule,
            'operation': self.operation,
        }

    def _cardpointe_get_ecomind(self, flow=None):
        return payment_transaction.PaymentTransaction._cardpointe_get_ecomind(self, flow=flow)

    def _cardpointe_get_cnp_contact_payload(self, meta=None):
        return payment_transaction.PaymentTransaction._cardpointe_get_cnp_contact_payload(self, meta=meta)

    def _cardpointe_get_stored_credential_payload_fields(self):
        return payment_transaction.PaymentTransaction._cardpointe_get_stored_credential_payload_fields(self)

    def _cardpointe_set_provider_reference(self, raw):
        return payment_transaction.PaymentTransaction._cardpointe_set_provider_reference(self, raw)

    def _cardpointe_done(self, message=None):
        return payment_transaction.PaymentTransaction._cardpointe_done(self, message=message)

    def _cardpointe_fail(self, message, code=None):
        return payment_transaction.PaymentTransaction._cardpointe_fail(self, message, code=code)


class TestCardPointeStoredCredentialPayload(unittest.TestCase):

    def test_saved_token_customer_unscheduled_maps_to_c_and_n(self):
        tx = _TxStub(operation='online_token', initiator='customer', schedule='unscheduled')

        fields = payment_transaction.PaymentTransaction._cardpointe_get_stored_credential_payload_fields(tx)

        self.assertEqual(fields['cof'], 'C')
        self.assertEqual(fields['cofscheduled'], 'N')

    def test_saved_token_merchant_unscheduled_maps_to_m_and_n(self):
        tx = _TxStub(operation='online_token', initiator='merchant', schedule='unscheduled')

        fields = payment_transaction.PaymentTransaction._cardpointe_get_stored_credential_payload_fields(tx)

        self.assertEqual(fields['cof'], 'M')
        self.assertEqual(fields['cofscheduled'], 'N')

    def test_saved_token_merchant_scheduled_maps_to_m_and_y(self):
        tx = _TxStub(operation='offline', initiator='merchant', schedule='scheduled')

        fields = payment_transaction.PaymentTransaction._cardpointe_get_stored_credential_payload_fields(tx)

        self.assertEqual(fields['cof'], 'M')
        self.assertEqual(fields['cofscheduled'], 'Y')

    def test_saved_token_charge_uses_semantics_mapping(self):
        tx = _TxStub(operation='online_token', initiator='customer', schedule='unscheduled')
        payment_token = _TokenStub(provider_ref='saved_profile:saved_account')

        result = payment_transaction.PaymentTransaction._cardpointe_charge_from_payment_token(
            tx, payment_token, flow='web'
        )

        self.assertTrue(result['ok'])
        payload = tx.provider_id.requested_payload
        self.assertEqual(payload['profile'], 'saved_profile/saved_account')
        self.assertEqual(payload['cof'], 'C')
        self.assertEqual(payload['cofscheduled'], 'N')
        self.assertEqual(payload['ecomind'], 'E')


class TestCardPointeWebsiteNewCardPayload(unittest.TestCase):

    def test_new_card_payload_includes_ecomind_and_contact_fields(self):
        tx = _TxStub(operation='online', token=False)

        result = payment_transaction.PaymentTransaction._cardpointe_charge_from_token(
            tx,
            token='TOK123456789',
            meta={'address': '500 Market', 'city': 'San Francisco'},
            flow='web',
        )

        self.assertTrue(result['ok'])
        payload = tx.provider_id.requested_payload
        self.assertEqual(payload['ecomind'], 'E')
        self.assertEqual(payload['name'], 'Ada Lovelace')
        self.assertEqual(payload['email'], 'ada@example.com')
        self.assertEqual(payload['phone'], '555-0100')
        self.assertEqual(payload['address'], '500 Market')
        self.assertEqual(payload['city'], 'San Francisco')
        self.assertEqual(payload['region'], 'TX')
        self.assertEqual(payload['country'], 'US')
        self.assertEqual(payload['postal'], '78701')


if __name__ == '__main__':
    unittest.main()
