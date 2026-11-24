"""Keep POS-only source contracts with the module that owns them."""
import pathlib
from odoo.tests.common import TransactionCase


class PosMappingContractTest(TransactionCase):
    def test_pos_mapping_is_explicit_and_cash_cannot_map(self):
        root = pathlib.Path(__file__).parents[1]
        source = (root / 'models' / 'pos_payment_method.py').read_text()
        self.assertIn('markup_provider_id = fields.Many2one', source)
        self.assertIn("self.markup_card_type != 'credit'", source)
        self.assertNotIn('self.use_payment_terminal', source)
        self.assertNotIn('cardpointe_config_id', source)
