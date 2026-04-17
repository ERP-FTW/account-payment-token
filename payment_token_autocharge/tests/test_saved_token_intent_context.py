import importlib.util
import os
import sys
import types
import unittest

CURRENT_DIR = os.path.dirname(__file__)
MODULE_PATH = os.path.abspath(os.path.join(CURRENT_DIR, '..', 'models', 'account_move.py'))


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
        Selection=lambda *args, **kwargs: None,
        Many2one=lambda *args, **kwargs: None,
    )
    odoo_mod.models = types.SimpleNamespace(Model=object)

    tools_mod = types.ModuleType('odoo.tools')
    tools_mod.plaintext2html = lambda text, _tag='p': text

    sys.modules['odoo'] = odoo_mod
    sys.modules['odoo.tools'] = tools_mod


def _load_module():
    _install_odoo_stubs()
    spec = importlib.util.spec_from_file_location('payment_token_autocharge_account_move', MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _DummyMove:
    def ensure_one(self):
        return True


class TestSavedTokenIntentContext(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.module = _load_module()

    def test_autocharge_sets_mit_scheduled_context(self):
        move = _DummyMove()
        context_vals = self.module.AccountMove._cardpointe_saved_token_charge_context(move)
        self.assertEqual(context_vals['cardpointe_initiator'], 'mit')
        self.assertIs(context_vals['cardpointe_scheduled'], True)


if __name__ == '__main__':
    unittest.main()
