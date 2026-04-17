import importlib.util
import os
import sys
import types
import unittest

CURRENT_DIR = os.path.dirname(__file__)
MODULE_PATH = os.path.abspath(os.path.join(CURRENT_DIR, '..', 'models', 'account_invoice_token_wizard.py'))


def _install_odoo_stubs():
    if 'odoo' in sys.modules:
        return

    def _identity_decorator(*_args, **_kwargs):
        return lambda func: func

    odoo_mod = types.ModuleType('odoo')
    odoo_mod.__path__ = []
    odoo_mod._ = lambda value: value
    odoo_mod.api = types.SimpleNamespace(model=_identity_decorator, depends=_identity_decorator)
    odoo_mod.fields = types.SimpleNamespace(
        Many2one=lambda *args, **kwargs: None,
        Monetary=lambda *args, **kwargs: None,
    )
    odoo_mod.models = types.SimpleNamespace(TransientModel=object)

    exceptions_mod = types.ModuleType('odoo.exceptions')
    exceptions_mod.UserError = Exception

    sys.modules['odoo'] = odoo_mod
    sys.modules['odoo.exceptions'] = exceptions_mod


def _load_module():
    _install_odoo_stubs()
    spec = importlib.util.spec_from_file_location('payment_token_invoice_wizard', MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _DummyWizard:
    def ensure_one(self):
        return True


class TestSavedTokenIntentContext(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.module = _load_module()

    def test_manual_charge_sets_cit_unscheduled_context(self):
        wizard = _DummyWizard()
        context_vals = self.module.AccountInvoiceTokenWizard._cardpointe_saved_token_charge_context(wizard)
        self.assertEqual(context_vals['cardpointe_initiator'], 'cit')
        self.assertIs(context_vals['cardpointe_scheduled'], False)


if __name__ == '__main__':
    unittest.main()
