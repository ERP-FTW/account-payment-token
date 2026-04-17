import importlib.util
import os
import sys
import types
import unittest


CURRENT_DIR = os.path.dirname(__file__)
MODULE_PATH = os.path.abspath(os.path.join(CURRENT_DIR, '..', 'models', 'payment_transaction.py'))


def _install_odoo_stubs():
    if 'odoo' in sys.modules:
        return

    odoo_mod = types.ModuleType('odoo')
    odoo_mod.__path__ = []
    odoo_mod._ = lambda value: value
    odoo_mod.models = types.SimpleNamespace(Model=object)

    exceptions_mod = types.ModuleType('odoo.exceptions')
    exceptions_mod.UserError = Exception

    addons_mod = types.ModuleType('odoo.addons')
    addons_mod.__path__ = []
    payment_mod = types.ModuleType('odoo.addons.payment')
    payment_mod.__path__ = []
    utils_mod = types.ModuleType('odoo.addons.payment.utils')
    utils_mod.generate_access_token = lambda *args, **kwargs: 'stub-token'

    sys.modules['odoo'] = odoo_mod
    sys.modules['odoo.exceptions'] = exceptions_mod
    sys.modules['odoo.addons'] = addons_mod
    sys.modules['odoo.addons.payment'] = payment_mod
    sys.modules['odoo.addons.payment.utils'] = utils_mod


def _load_module():
    _install_odoo_stubs()
    spec = importlib.util.spec_from_file_location('payment_cardpointe_payment_transaction', MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _DummyCountry:
    def __init__(self, code):
        self.code = code


class _DummyState:
    def __init__(self, code):
        self.code = code


class _DummyPartner:
    def __init__(self):
        self.name = 'Jane Customer'
        self.email = 'jane@example.com'
        self.phone = ''
        self.mobile = '+1-555-0000'
        self.street = '123 Main St'
        self.city = 'Austin'
        self.state_id = _DummyState('TX')
        self.country_id = _DummyCountry('US')
        self.zip = '78701'
        self.commercial_partner_id = self


class _DummyCurrency:
    def __init__(self, name):
        self.name = name


class _DummyTx:
    def __init__(self):
        self.partner_id = _DummyPartner()
        self.amount = 42.5
        self.currency_id = _DummyCurrency('USD')
        self.reference = 'SO123-1'

    def ensure_one(self):
        return True


class TestWebsiteCnpPayload(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.module = _load_module()
        cls.Transaction = cls.module.PaymentTransaction

    def test_extracts_best_available_billing_values(self):
        tx = _DummyTx()

        values = self.Transaction._cardpointe_get_cnp_billing_contact_values(tx)

        self.assertEqual(values['name'], 'Jane Customer')
        self.assertEqual(values['email'], 'jane@example.com')
        self.assertEqual(values['phone'], '+1-555-0000')
        self.assertEqual(values['region'], 'TX')
        self.assertEqual(values['country'], 'US')
        self.assertEqual(values['postal'], '78701')

    def test_builds_website_cnp_auth_payload(self):
        tx = _DummyTx()
        tx._cardpointe_get_cnp_billing_contact_values = (
            lambda: self.Transaction._cardpointe_get_cnp_billing_contact_values(tx)
        )

        payload = self.Transaction._cardpointe_build_cnp_auth_payload(
            tx,
            token='9400000000000000',
            mid='123456789012',
            extra_payload={'capture': 'n'},
        )

        self.assertEqual(payload['merchid'], '123456789012')
        self.assertEqual(payload['account'], '9400000000000000')
        self.assertEqual(payload['amount'], '42.50')
        self.assertEqual(payload['currency'], 'USD')
        self.assertEqual(payload['orderid'], 'SO123-1')
        self.assertEqual(payload['ecomind'], 'E')
        self.assertEqual(payload['capture'], 'n')
        self.assertEqual(payload['phone'], '+1-555-0000')


if __name__ == '__main__':
    unittest.main()
