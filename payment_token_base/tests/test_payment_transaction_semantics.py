import importlib.util
import os
import sys
import types
import unittest

CURRENT_DIR = os.path.dirname(__file__)
MODULE_PATH = os.path.abspath(os.path.join(CURRENT_DIR, '..', 'models', 'payment_transaction.py'))


if 'odoo' not in sys.modules:
    odoo_module = types.ModuleType('odoo')

    class _FieldFactory:
        @staticmethod
        def Selection(*args, **kwargs):
            return None

    odoo_module.fields = _FieldFactory()
    odoo_module.models = types.SimpleNamespace(Model=object)
    sys.modules['odoo'] = odoo_module

spec = importlib.util.spec_from_file_location('payment_token_base.models.payment_transaction', MODULE_PATH)
payment_transaction = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = payment_transaction
spec.loader.exec_module(payment_transaction)


class _TxStub:
    def __init__(self, initiator, schedule, operation='online_token'):
        self.stored_credential_initiator = initiator
        self.stored_credential_schedule = schedule
        self.operation = operation

    def ensure_one(self):
        return self


class TestPaymentTransactionSemantics(unittest.TestCase):
    def test_helper_returns_generic_semantics(self):
        tx = _TxStub('merchant', 'scheduled', operation='offline')

        semantics = payment_transaction.PaymentTransaction._get_stored_credential_semantics(tx)

        self.assertEqual(semantics, {
            'initiator': 'merchant',
            'schedule': 'scheduled',
            'operation': 'offline',
        })


if __name__ == '__main__':
    unittest.main()
