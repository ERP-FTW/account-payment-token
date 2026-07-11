import importlib.util
import pathlib
import sys
import types
import unittest

TIP_PATH = pathlib.Path(__file__).resolve().parents[1] / 'services' / 'cardpointe_terminal.py'

for name in ('odoo', 'odoo.addons', 'odoo.addons.pos_cardpointe_poc', 'odoo.addons.pos_cardpointe_poc.services'):
    if name not in sys.modules:
        pkg = types.ModuleType(name)
        pkg.__path__ = []
        sys.modules[name] = pkg

base_stub = types.ModuleType('odoo.addons.pos_cardpointe_poc.services.cardpointe_terminal')

class _Client:
    pass


def _dollars_to_implied_cents(amount):
    return str(int(round(float(amount) * 100)))

base_stub.CardPointeTerminalClient = _Client
base_stub.dollars_to_implied_cents = _dollars_to_implied_cents
sys.modules['odoo.addons.pos_cardpointe_poc.services.cardpointe_terminal'] = base_stub

tip_spec = importlib.util.spec_from_file_location('tip_terminal_service', TIP_PATH)
module = importlib.util.module_from_spec(tip_spec)
tip_spec.loader.exec_module(module)


class TestTipPayload(unittest.TestCase):
    def _config(self, **overrides):
        values = {
            'merchant_id': '800000009875',
            'device_serial': 'TESTSERIAL',
            'tip_prompt': 'Select tip amount',
            'tip_include_amount_display': True,
            'tip_allow_custom': True,
            'tip_percent_1': 15,
            'tip_percent_2': 18,
            'tip_percent_3': 20,
            'tip_percent_4': 25,
            'tip_presets_count': '3',
        }
        values.update(overrides)
        return types.SimpleNamespace(**values)

    def test_payload_uses_tip_percent_presets(self):
        payload = module.build_tip_payload(self._config(tip_presets_count='3'), 12.34)
        self.assertEqual(payload['tipPercentPresets'], ['15', '18', '20'])
        self.assertTrue(payload['includeCustomTipAmount'])
        self.assertTrue(payload['includeAmountDisplay'])

    def test_custom_tip_caps_presets_to_three(self):
        payload = module.build_tip_payload(self._config(tip_presets_count='4', tip_allow_custom=True), 12.34)
        self.assertEqual(payload['tipPercentPresets'], ['15', '18', '20'])

    def test_four_presets_allowed_without_custom_tip(self):
        payload = module.build_tip_payload(self._config(tip_presets_count='4', tip_allow_custom=False), 12.34)
        self.assertEqual(payload['tipPercentPresets'], ['15', '18', '20', '25'])


if __name__ == '__main__':
    unittest.main()
