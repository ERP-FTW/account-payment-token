import unittest

from odoo.tests.common import TransactionCase

from ..services.calculation import calculate_markup, calculate_taxed_markup


class _Tax:
    price_include = False


class _Taxes:
    def __iter__(self):
        return iter([_Tax()])

    def flatten_taxes_hierarchy(self):
        return list(self)

    def with_context(self, **_context):
        return self

    def compute_all(self, value, **_kwargs):
        value = round(float(value), 2)
        tax = round(value * 0.10, 2)
        return {'total_excluded': value, 'total_included': round(value + tax, 2)}


class _IncludedTax:
    price_include = True


class _IncludedTaxes(_Taxes):
    def __iter__(self):
        return iter([_IncludedTax()])

    def compute_all(self, value, handle_price_include=True, **_kwargs):
        gross = round(float(value), 2)
        if not handle_price_include:
            tax = round(gross * 0.10, 2)
            return {'total_excluded': gross, 'total_included': round(gross + tax, 2)}
        net = round(gross / 1.10, 2)
        return {'total_excluded': net, 'total_included': gross}


class _Currency:
    def round(self, value):
        return round(float(value), 2)


class TestMarkupCalculation(TransactionCase):
    def setUp(self):
        super().setUp()
        # Odoo 16 currency.round delegates to float_round and requires a
        # float-compatible input; this is intentionally not a Decimal double.
        self.round = lambda value: round(float(value), 2)

    def test_percentage_example(self):
        result = calculate_markup(100, 2.5, self.round, eligible=True)
        self.assertEqual(result['amount'], 2.5)
        self.assertEqual(result['total'], 102.5)

    def test_split_tender_card_allocation(self):
        result = calculate_markup(400, 3, self.round, eligible=True)
        self.assertEqual(result['amount'], 12.0)
        self.assertEqual(result['total'], 412.0)

    def test_disabled_and_unknown_are_unchanged(self):
        self.assertEqual(calculate_markup(100, 2.5, self.round, eligible=False)['amount'], 0.0)




    def test_included_rate_uses_attainable_native_gross_after_rounding(self):
        result = calculate_taxed_markup(
            0.05, _Taxes(), _Currency(), rate_included=True,
        )
        self.assertEqual(result, {
            'net_amount': 0.04,
            'tax_amount': 0.0,
            'gross_amount': 0.04,
        })

    def test_included_rate_preserves_configured_gross_and_native_split(self):
        result = calculate_taxed_markup(
            3.00, _IncludedTaxes(), _Currency(), rate_included=True,
        )
        self.assertEqual(result, {
            'net_amount': 2.73,
            'tax_amount': 0.27,
            'gross_amount': 3.00,
        })

    def test_excluded_rate_uses_compute_all_price_include_switch(self):
        result = calculate_taxed_markup(
            3.00, _IncludedTaxes(), _Currency(), rate_included=False,
        )
        self.assertEqual(result, {
            'net_amount': 3.00,
            'tax_amount': 0.30,
            'gross_amount': 3.30,
        })


if __name__ == '__main__':
    unittest.main()
