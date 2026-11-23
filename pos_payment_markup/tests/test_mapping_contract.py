"""Keep POS-only source contracts with the module that owns them."""
import pathlib
from odoo.tests.common import TransactionCase


class PosMappingContractTest(TransactionCase):
    def test_pos_mapping_is_explicit_and_cash_cannot_map(self):
        root = pathlib.Path(__file__).parents[1]
        source = (root / 'models' / 'pos_payment_method.py').read_text()
        self.assertIn('markup_provider_id = fields.Many2one', source)
        self.assertIn("self.markup_card_type != 'credit'", source)
        # Provider mapping remains explicit; a terminal config may only suppress
        # a local fee to avoid stacking, never implicitly select its provider.
        mapping = source.split('    def _markup_provider(self):', 1)[1].split('\n    def ', 1)[0]
        self.assertNotIn('self.use_payment_terminal', mapping)
        self.assertNotIn('cardpointe_config_id', mapping)
        self.assertIn('self.is_cash_count', mapping)
