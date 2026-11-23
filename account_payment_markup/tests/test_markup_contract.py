import ast
import pathlib

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase

from ..services.calculation import calculate_markup


ROOT = pathlib.Path(__file__).parents[1]


class MarkupContractTest(TransactionCase):
    def test_disabled_and_manual_provider_decisions(self):
        r = lambda value: round(float(value), 2)
        self.assertEqual(calculate_markup(100, 3, r, eligible=False)['total'], 100.0)
        self.assertEqual(calculate_markup(100, 3, r, eligible=True)['total'], 103.0)

    def test_authoritative_zero_processor_amount(self):
        r = lambda value: round(float(value), 2)
        result = calculate_markup(100, 3, r, eligible=True, processor_amount=0.0)
        self.assertEqual(result['source'], 'processor')
        self.assertEqual(result['amount'], 0.0)

    def test_manual_and_processor_evidence_contracts_exist(self):
        source = (ROOT / 'models' / 'payment_provider.py').read_text()
        self.assertIn('Accept configured manual fees or verified processor evidence', source)
        self.assertIn('server_trusted_markup_evidence', source)
        self.assertIn("return False", source)

    def test_processing_values_use_disclosed_server_amount(self):
        tree = ast.parse((ROOT / 'models' / 'payment_transaction.py').read_text())
        names = {node.name for node in tree.body if isinstance(node, ast.ClassDef)
                 for node in node.body if isinstance(node, ast.FunctionDef)}
        self.assertIn('_get_processing_values', names)
        source = (ROOT / 'models' / 'payment_transaction.py').read_text()
        self.assertIn("'amount': details['total']", source)

    def test_original_evidence_refund_cap_is_integrated(self):
        source = (ROOT / 'models' / 'payment_transaction.py').read_text()
        self.assertIn('def _send_refund_request', source)
        self.assertGreaterEqual(source.count('_markup_refund_cap('), 3)

    def test_refund_tax_base_uses_company_currency_precision(self):
        source = (ROOT / 'models' / 'account_payment.py').read_text()
        self.assertIn('target_base = company_currency.round(original_base * ratio)', source)
        self.assertIn('line_base = company_currency.round((target_base - prior_base) if sign < 0 else max(target_base - prior_base, 0.0))', source)

    def test_retry_rejects_changed_accounting_configuration(self):
        source = (ROOT / 'models' / 'payment_transaction.py').read_text()
        self.assertIn('markup_accounting_fingerprint', source)
        self.assertIn('payment must be restarted', source)

    def test_portal_disclosure_is_server_computed_and_manifest_loaded(self):
        manifest = ast.literal_eval((ROOT / '__manifest__.py').read_text())
        self.assertIn('views/payment_portal_templates.xml', manifest['data'])
        provider = (ROOT / 'models' / 'payment_provider.py').read_text()
        template = (ROOT / 'views' / 'payment_portal_templates.xml').read_text()
        self.assertIn('def _markup_portal_disclosure', provider)
        self.assertIn('data-markup-disclosure="server"', template)


class MarkupRuntimeTest(TransactionCase):
    """Small installed-module regressions; these must run in Odoo's test loader."""

    def test_accounting_retry_guard_field_is_installed(self):
        tx_model = self.env['payment.transaction']
        self.assertIn('markup_accounting_fingerprint', tx_model._fields)
        self.assertIn('markup_net_amount', tx_model._fields)

    def test_manual_eligibility_does_not_require_endpoint_evidence(self):
        provider = self.env['payment.provider'].search([], limit=1)
        self.assertTrue(provider._markup_card_eligible())
        self.assertFalse(provider._markup_card_eligible(
            card_type='credit', evidence_verified=True,
            processor_surcharge_present=False, evidence_trusted=True,
        ))
        trusted = provider.with_context(server_trusted_markup_evidence=True)
        self.assertTrue(trusted._markup_card_eligible(
            card_type='credit', evidence_verified=True,
            processor_surcharge_present=False, evidence_trusted=True,
        ))

    def test_portal_disclosure_view_is_installed(self):
        view = self.env.ref(
            'account_payment_markup.payment_checkout_markup_disclosure',
            raise_if_not_found=False,
        )
        self.assertTrue(view)
        self.assertIn("t-foreach='providers'", view.arch_db)
        self.assertIn("t-foreach='tokens'", view.arch_db)
        template = self.env.ref(
            'account_payment_markup.payment_markup_disclosure',
            raise_if_not_found=False,
        )
        self.assertTrue(template)
        self.assertIn('data-markup-disclosure="server"', template.arch_db)
    def test_portal_disclosure_breakdown_matches_transaction_matrix(self):
        company = self.env.company
        company.country_id = self.env.ref('base.us')
        tax = self.env['account.tax'].create({
            'name': 'Markup matrix tax', 'amount': 10, 'amount_type': 'percent',
            'type_tax_use': 'sale', 'company_id': company.id,
            'country_id': company.country_id.id,
        })
        account = self.env['account.account'].create({
            'name': 'Markup matrix income', 'code': 'MKTMATRIX',
            'account_type': 'income', 'company_id': company.id,
        })
        product = self.env['product.product'].create({
            'name': 'Markup matrix fee', 'detailed_type': 'service',
            'property_account_income_id': account.id, 'taxes_id': [(6, 0, tax.ids)],
        })
        provider = self.env['payment.provider'].search([('name', '=', 'Wire Transfer')], limit=1)
        tx = self.env['payment.transaction'].create({
            'provider_id': provider.id, 'reference': 'MKT-MATRIX',
            'amount': 100, 'currency_id': company.currency_id.id,
            'partner_id': self.env.user.partner_id.id, 'operation': 'online_redirect',
        })
        for product_included, rate_included in ((False, False), (False, True),
                                                (True, False), (True, True)):
            tax.price_include = product_included
            provider.write({
                'markup_enabled': True, 'markup_percent': 3,
                'markup_product_id': product.id, 'markup_tax_included': rate_included,
            })
            disclosure = provider._markup_portal_disclosure(
                100.0, company.currency_id, self.env.user.partner_id,
            )
            details = tx._markup_details(base_amount=100)
            self.assertEqual(disclosure['net_amount'], details['net_amount'])
            self.assertEqual(disclosure['tax_amount'], details['tax_amount'])
            self.assertEqual(disclosure['gross_amount'], details['gross_amount'])
            self.assertEqual(disclosure['total_amount'], details['total'])
            if rate_included:
                expected = (2.73, 0.27, 3.0)
            else:
                expected = (3.0, 0.3, 3.3)
            for actual, target in zip(
                    (disclosure['net_amount'], disclosure['tax_amount'], disclosure['gross_amount']),
                    expected):
                self.assertAlmostEqual(actual, target, places=2)

    def test_posted_fee_invoice_matches_mixed_tax_decision(self):
        company = self.env.company
        company.country_id = self.env.ref('base.us')
        account = self.env['account.account'].create({
            'name': 'Mixed fee income', 'code': 'MKTMIXI',
            'account_type': 'income', 'company_id': company.id,
        })
        receivable = self.env['account.account'].create({
            'name': 'Mixed receivable', 'code': 'MKTMIXR',
            'account_type': 'asset_receivable', 'reconcile': True,
            'company_id': company.id,
        })
        partner = self.env['res.partner'].create({
            'name': 'Mixed fee customer', 'property_account_receivable_id': receivable.id,
        })
        product = self.env['product.product'].create({
            'name': 'Mixed fee product', 'detailed_type': 'service',
            'property_account_income_id': account.id,
        })
        provider = self.env['payment.provider'].search([('name', '=', 'Wire Transfer')], limit=1)
        provider.write({
            'state': 'test', 'markup_enabled': True, 'markup_percent': 3,
            'markup_product_id': product.id,
        })
        sale_journal = self.env['account.journal'].create({
            'name': 'Mixed fee sales', 'code': 'MKS', 'type': 'sale',
            'company_id': company.id,
        })
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': partner.id,
            'journal_id': sale_journal.id,
            'invoice_line_ids': [(0, 0, {
                'name': 'Mixed tax base', 'quantity': 1, 'price_unit': 100,
                'account_id': account.id,
            })],
        })
        invoice.action_post()
        for base in (100.0, 1.67):
            for product_included, rate_included in ((False, False), (False, True),
                                                    (True, False), (True, True)):
                for grouped in (False, True):
                    included = self.env['account.tax'].create({
                        'name': 'Mixed included', 'amount': 10, 'amount_type': 'percent',
                        'type_tax_use': 'sale', 'price_include': product_included,
                        'company_id': company.id,
                    })
                    excluded = self.env['account.tax'].create({
                        'name': 'Mixed excluded', 'amount': 5, 'amount_type': 'percent',
                        'type_tax_use': 'sale', 'price_include': False,
                        'company_id': company.id,
                    })
                    if grouped:
                        tax = self.env['account.tax'].create({
                            'name': 'Mixed tax group', 'amount': 0, 'amount_type': 'group',
                            'type_tax_use': 'sale', 'company_id': company.id,
                            'children_tax_ids': [(6, 0, (included | excluded).ids)],
                        })
                        product.taxes_id = [(6, 0, tax.ids)]
                    else:
                        product.taxes_id = [(6, 0, (included | excluded).ids)]
                    provider.markup_tax_included = rate_included
                    tx = self.env['payment.transaction'].create({
                        'provider_id': provider.id,
                        'reference': 'MIX-%s-%s-%s-%s' % (
                            base, product_included, rate_included, grouped),
                        'amount': base, 'currency_id': company.currency_id.id,
                        'partner_id': partner.id, 'operation': 'online_redirect',
                        'invoice_ids': [(6, 0, invoice.ids)],
                    })
                    details = tx._markup_prepare(base_amount=base)
                    fee = tx.markup_fee_move_id
                    fee.action_post()
                    self.assertAlmostEqual(fee.amount_untaxed, details['net_amount'], places=2)
                    self.assertAlmostEqual(fee.amount_tax, details['tax_amount'], places=2)
                    self.assertAlmostEqual(fee.amount_total, details['gross_amount'], places=2)
                    fee_receivable = fee.line_ids.filtered(
                        lambda line: line.account_id.account_type == 'asset_receivable')
                    self.assertAlmostEqual(
                        sum(fee_receivable.mapped('amount_currency')),
                        details['gross_amount'], places=2,
                    )

    def test_grouped_and_flat_all_included_taxes_keep_fee_accounting_identical(self):
        company = self.env.company
        company.country_id = self.env.ref('base.us')
        account = self.env['account.account'].create({
            'name': 'All included fee income', 'code': 'MKTALLI',
            'account_type': 'income', 'company_id': company.id,
        })
        receivable = self.env['account.account'].create({
            'name': 'All included receivable', 'code': 'MKTALLR',
            'account_type': 'asset_receivable', 'reconcile': True,
            'company_id': company.id,
        })
        partner = self.env['res.partner'].create({
            'name': 'All included fee customer',
            'property_account_receivable_id': receivable.id,
        })
        product = self.env['product.product'].create({
            'name': 'All included fee product', 'detailed_type': 'service',
            'property_account_income_id': account.id,
        })
        provider = self.env['payment.provider'].search([('name', '=', 'Wire Transfer')], limit=1)
        provider.write({
            'state': 'test', 'markup_enabled': True, 'markup_percent': 3,
            'markup_product_id': product.id,
        })
        sale_journal = self.env['account.journal'].create({
            'name': 'All included fee sales', 'code': 'MKAI', 'type': 'sale',
            'company_id': company.id,
        })
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': partner.id,
            'journal_id': sale_journal.id,
            'invoice_line_ids': [(0, 0, {
                'name': 'All included base', 'quantity': 1, 'price_unit': 100,
                'account_id': account.id,
            })],
        })
        invoice.action_post()
        for base in (100.0, 1.67):
            for rate_included in (False, True):
                leaves = self.env['account.tax'].create([
                    {'name': 'All included ten', 'amount': 10, 'amount_type': 'percent',
                     'type_tax_use': 'sale', 'price_include': True, 'company_id': company.id},
                    {'name': 'All included five', 'amount': 5, 'amount_type': 'percent',
                     'type_tax_use': 'sale', 'price_include': True, 'company_id': company.id},
                ])
                group = self.env['account.tax'].create({
                    'name': 'All included group', 'amount': 0, 'amount_type': 'group',
                    'type_tax_use': 'sale', 'company_id': company.id,
                    'children_tax_ids': [(6, 0, leaves.ids)],
                })
                results = []
                for label, tax_ids in (('flat', leaves), ('group', group)):
                    product.taxes_id = [(6, 0, tax_ids.ids)]
                    provider.markup_tax_included = rate_included
                    tx = self.env['payment.transaction'].create({
                        'provider_id': provider.id,
                        'reference': 'MKT-ALL-%s-%s-%s' % (base, rate_included, label),
                        'amount': base, 'currency_id': company.currency_id.id,
                        'partner_id': partner.id, 'operation': 'online_redirect',
                        'invoice_ids': [(6, 0, invoice.ids)],
                    })
                    details = tx._markup_prepare(base_amount=base)
                    disclosure = provider._markup_portal_disclosure(
                        base, company.currency_id, partner,
                    )
                    self.assertEqual(
                        (disclosure['net_amount'], disclosure['tax_amount'],
                         disclosure['gross_amount'], disclosure['total_amount']),
                        (details['net_amount'], details['tax_amount'],
                         details['gross_amount'], details['total']),
                    )
                    fee = tx.markup_fee_move_id
                    fee.action_post()
                    fee_receivable = fee.line_ids.filtered(
                        lambda line: line.account_id.account_type == 'asset_receivable')
                    posted = (fee.amount_untaxed, fee.amount_tax, fee.amount_total,
                              sum(fee_receivable.mapped('amount_currency')))
                    expected = (details['net_amount'], details['tax_amount'],
                                details['gross_amount'], details['gross_amount'])
                    self.assertEqual(posted, expected)
                    results.append((details['net_amount'], details['tax_amount'],
                                    details['gross_amount'], details['total']))
                self.assertEqual(results[0], results[1])

    def test_recursive_group_tax_fingerprint_changes_with_child_semantics(self):
        company = self.env.company
        company.country_id = self.env.ref('base.us')
        child = self.env['account.tax'].create({
            'name': 'Markup child tax', 'amount': 10, 'amount_type': 'percent',
            'type_tax_use': 'sale', 'company_id': company.id,
            'country_id': company.country_id.id,
        })
        group = self.env['account.tax'].create({
            'name': 'Markup group tax', 'amount': 0, 'amount_type': 'group',
            'type_tax_use': 'sale', 'company_id': company.id,
            'country_id': company.country_id.id, 'children_tax_ids': [(6, 0, child.ids)],
        })
        account = self.env['account.account'].create({
            'name': 'Markup group income', 'code': 'MKTGROUP',
            'account_type': 'income', 'company_id': company.id,
        })
        product = self.env['product.product'].create({
            'name': 'Markup group fee', 'detailed_type': 'service',
            'property_account_income_id': account.id, 'taxes_id': [(6, 0, group.ids)],
        })
        provider = self.env['payment.provider'].search([('name', '=', 'Wire Transfer')], limit=1)
        provider.write({'markup_enabled': True, 'markup_percent': 3, 'markup_product_id': product.id})
        tx = self.env['payment.transaction'].create({
            'provider_id': provider.id, 'reference': 'MKT-GROUP', 'amount': 100,
            'currency_id': company.currency_id.id, 'partner_id': self.env.user.partner_id.id,
            'operation': 'online_redirect',
        })
        original = tx._markup_current_accounting_fingerprint()
        child.amount = 20
        changed = tx._markup_current_accounting_fingerprint()
        self.assertNotEqual(original, changed)
        child.amount = 10
        self.assertEqual(original, tx._markup_current_accounting_fingerprint())

    def test_retry_rejects_changed_tax_computation_type_runtime(self):
        company = self.env.company
        company.country_id = self.env.ref('base.us')
        tax = self.env['account.tax'].create({
            'name': 'Retry test tax', 'amount': 10, 'amount_type': 'percent',
            'type_tax_use': 'sale', 'company_id': company.id,
            'country_id': company.country_id.id,
        })
        account = self.env['account.account'].create({
            'name': 'Retry test income', 'code': 'MKTRETRY',
            'account_type': 'income', 'company_id': company.id,
        })
        product = self.env['product.product'].create({
            'name': 'Retry test fee', 'detailed_type': 'service',
            'property_account_income_id': account.id, 'taxes_id': [(6, 0, tax.ids)],
        })
        provider = self.env['payment.provider'].search([('name', '=', 'Wire Transfer')], limit=1)
        provider.write({
            'markup_enabled': True, 'markup_percent': 3,
            'markup_product_id': product.id,
        })
        tx = self.env['payment.transaction'].create({
            'provider_id': provider.id, 'reference': 'MKT-RETRY-TEST',
            'amount': 100, 'currency_id': company.currency_id.id,
            'partner_id': self.env.user.partner_id.id,
            'operation': 'online_redirect',
        })
        tx._markup_prepare(base_amount=100)
        tx.markup_amount_source = 'local'
        tax.amount_type = 'fixed'
        self.env.flush_all()
        self.env.invalidate_all()
        with self.assertRaises(ValidationError):
            tx._markup_prepare(base_amount=100)
    def test_register_payment_posts_markup_tax_with_native_metadata_and_fallback(self):
        company = self.env.company
        company.country_id = self.env.ref('base.us')
        income = self.env['account.account'].create({
            'name': 'Register markup income', 'code': 'RPI01',
            'account_type': 'income', 'company_id': company.id,
        })
        receivable = self.env['account.account'].create({
            'name': 'Register markup receivable', 'code': 'RPR01',
            'account_type': 'asset_receivable', 'reconcile': True,
            'company_id': company.id,
        })
        partner = self.env['res.partner'].create({
            'name': 'Register markup customer',
            'property_account_receivable_id': receivable.id,
        })
        product = self.env['product.product'].create({
            'name': 'Register markup fee', 'detailed_type': 'service',
            'property_account_income_id': income.id,
        })
        provider = self.env['payment.provider'].search(
            [('name', '=', 'Wire Transfer')], limit=1,
        )
        bank = self.env['account.journal'].create({
            'name': 'Register markup bank', 'code': 'RPB01',
            'type': 'bank', 'company_id': company.id,
        })
        method = bank.inbound_payment_method_line_ids[:1]
        method.payment_account_id = self.env['account.account'].create({
            'name': 'Register markup outstanding', 'code': 'RPO01',
            'account_type': 'asset_current', 'reconcile': True,
            'company_id': company.id,
        })
        sale = self.env['account.journal'].create({
            'name': 'Register markup sale', 'code': 'RPS01',
            'type': 'sale', 'company_id': company.id,
        })
        tag = self.env['account.account.tag'].create({
            'name': 'Register markup tax tag', 'applicability': 'taxes',
            'country_id': company.account_fiscal_country_id.id,
        })
        provider.write({
            'state': 'test', 'markup_enabled': True, 'markup_percent': 3,
            'markup_product_id': product.id, 'markup_tax_included': False,
        })
        for assigned in (False, True):
            tax = self.env['account.tax'].create({
                'name': 'Register markup tax %s' % assigned,
                'amount': 10, 'amount_type': 'percent',
                'type_tax_use': 'sale', 'company_id': company.id,
            })
            tax_line = tax.invoice_repartition_line_ids.filtered(
                lambda line: line.repartition_type == 'tax',
            )
            if assigned:
                tax_line.account_id = self.env['account.account'].create({
                    'name': 'Register markup tax account', 'code': 'RPT01',
                    'account_type': 'liability_current', 'company_id': company.id,
                })
            tax_line.tag_ids = [(6, 0, tag.ids)]
            product.taxes_id = [(6, 0, tax.ids)]
            invoice = self.env['account.move'].create({
                'move_type': 'out_invoice', 'partner_id': partner.id,
                'journal_id': sale.id,
                'invoice_line_ids': [(0, 0, {
                    'name': 'Register markup source', 'quantity': 1,
                    'price_unit': 100, 'account_id': income.id,
                })],
            })
            invoice.action_post()
            wizard = self.env['account.payment.register'].with_context(
                active_model='account.move', active_ids=invoice.ids,
            ).create({
                'journal_id': bank.id,
                'payment_method_line_id': method.id,
                'markup_provider_id': provider.id,
            })
            self.assertAlmostEqual(wizard.markup_net_amount, 3.0, places=2)
            self.assertAlmostEqual(wizard.markup_tax_amount, 0.3, places=2)
            self.assertAlmostEqual(wizard.markup_gross_amount, 3.3, places=2)
            self.assertAlmostEqual(wizard.markup_total_amount, 103.3, places=2)
            payment = wizard._create_payments()
            self.assertEqual(payment.markup_amount_source, 'manual')
            self.assertEqual(payment.markup_provider_id, provider)
            self.assertAlmostEqual(payment.markup_base_amount, 100.0, places=2)
            self.assertAlmostEqual(payment.markup_net_amount, 3.0, places=2)
            self.assertAlmostEqual(payment.markup_tax_amount, 0.3, places=2)
            self.assertAlmostEqual(payment.markup_gross_amount, 3.3, places=2)
            self.assertAlmostEqual(payment.markup_total_amount, 103.3, places=2)
            self.assertTrue(payment.move_id.line_ids.filtered(
                lambda line: line.markup_decision_source == 'manual'
            ))
            self.assertFalse(payment.move_id.markup_source_payment_id)
            self.assertEqual(payment.move_id.markup_provider_id, provider)
            self.assertAlmostEqual(payment.move_id.markup_total_amount, 103.3, places=2)
            self.assertAlmostEqual(payment.amount, 103.30, places=2)
            self.assertAlmostEqual(invoice.amount_residual, 0.0, places=2)
            payment_receivable = payment.move_id.line_ids.filtered(
                lambda line: line.account_id == receivable,
            )
            self.assertAlmostEqual(
                sum(payment_receivable.mapped('amount_residual')), 0.0, places=2,
            )
            tax_lines = payment.move_id.line_ids.filtered(
                lambda line: line.tax_repartition_line_id,
            )
            self.assertEqual(len(tax_lines), 1)
            tax_move_line = tax_lines[0]
            self.assertEqual(tax_move_line.tax_repartition_line_id, tax_line)
            self.assertIn(tag, tax_move_line.tax_tag_ids)
            expected_account = tax_line.account_id or income
            self.assertEqual(tax_move_line.account_id, expected_account)
            # Refunds must retain the posted fee account/tax evidence even if
            # the provider is reconfigured after the payment was posted.
            replacement_code = 'RPRX'
            while self.env['account.account'].search_count([
                ('code', '=', replacement_code), ('company_id', '=', company.id),
            ]):
                replacement_code += 'X'
            replacement_income = self.env['account.account'].create({
                'name': 'Replacement markup income', 'code': replacement_code,
                'account_type': 'income', 'company_id': company.id,
            })
            replacement_product = self.env['product.product'].create({
                'name': 'Replacement markup product', 'detailed_type': 'service',
                'property_account_income_id': replacement_income.id,
                'taxes_id': [(6, 0, tax.ids)],
            })
            provider.markup_product_id = replacement_product
            # Match native Odoo16 tax-line metadata: the payment write-off
            # represents a sale-tax base, so the inverted entry reports the
            # positive taxable base in company currency.
            self.assertAlmostEqual(tax_move_line.tax_base_amount, 3.0, places=2)
            refund_one = payment.action_refund_markup(1.0)
            refund_two = payment.action_refund_markup(2.3)
            refund_fee_lines = (refund_one | refund_two).line_ids.filtered(
                lambda line: line.markup_decision_source == 'refund'
                and line.account_id in (income, expected_account)
            )
            self.assertTrue(refund_fee_lines)
            self.assertFalse(refund_one.line_ids.filtered(
                lambda line: line.account_id == replacement_income
            ))
            self.assertFalse(refund_two.line_ids.filtered(
                lambda line: line.account_id == replacement_income
            ))
            self.assertEqual(refund_one.markup_source_payment_id, payment)
            self.assertEqual(refund_two.markup_source_payment_id, payment)
            self.assertAlmostEqual(payment.markup_refunded_amount, 3.3, places=2)
            with self.assertRaises(ValidationError):
                payment.action_refund_markup(0.01)
            provider.markup_product_id = product

            # An included surcharge rate with a price-excluded product tax
            # must report the native fee-line base (2.73), not the gross fee.
            provider.markup_tax_included = True
            included_invoice = invoice.copy()
            included_invoice.action_post()
            included_wizard = self.env['account.payment.register'].with_context(
                active_model='account.move', active_ids=included_invoice.ids,
            ).create({
                'journal_id': bank.id,
                'payment_method_line_id': method.id,
                'markup_provider_id': provider.id,
            })
            self.assertAlmostEqual(included_wizard.markup_net_amount, 2.73, places=2)
            self.assertAlmostEqual(included_wizard.markup_tax_amount, 0.27, places=2)
            self.assertAlmostEqual(included_wizard.markup_gross_amount, 3.0, places=2)
            self.assertAlmostEqual(included_wizard.markup_total_amount, 103.0, places=2)
            included_payment = included_wizard._create_payments()
            included_tax_line = included_payment.move_id.line_ids.filtered(
                lambda line: line.tax_repartition_line_id,
            )
            self.assertAlmostEqual(included_payment.amount, 103.0, places=2)
            self.assertAlmostEqual(
                included_tax_line.tax_base_amount, 2.73, places=2,
            )
            provider.markup_tax_included = False

    def test_partial_refund_rounding_allocates_to_requested_gross(self):
        """Each posted refund must stay balanced, including cent-sized splits."""
        company = self.env.company
        company.country_id = self.env.ref('base.us')
        income = self.env['account.account'].create({
            'name': 'Partial refund income', 'code': 'MKTPRI',
            'account_type': 'income', 'company_id': company.id,
        })
        receivable = self.env['account.account'].create({
            'name': 'Partial refund receivable', 'code': 'MKTPRR',
            'account_type': 'asset_receivable', 'reconcile': True,
            'company_id': company.id,
        })
        partner = self.env['res.partner'].create({
            'name': 'Partial refund customer',
            'property_account_receivable_id': receivable.id,
        })
        tax = self.env['account.tax'].create({
            'name': 'Partial refund tax', 'amount': 20, 'amount_type': 'percent',
            'type_tax_use': 'sale', 'company_id': company.id,
        })
        product = self.env['product.product'].create({
            'name': 'Partial refund fee', 'detailed_type': 'service',
            'property_account_income_id': income.id, 'taxes_id': [(6, 0, tax.ids)],
        })
        provider = self.env['payment.provider'].search(
            [('name', '=', 'Wire Transfer')], limit=1,
        )
        provider.write({
            'state': 'test', 'markup_enabled': True, 'markup_percent': 3,
            'markup_product_id': product.id, 'markup_tax_included': False,
        })
        bank = self.env['account.journal'].create({
            'name': 'Partial refund bank', 'code': 'MKTPRB',
            'type': 'bank', 'company_id': company.id,
        })
        method = bank.inbound_payment_method_line_ids[:1]
        method.payment_account_id = self.env['account.account'].create({
            'name': 'Partial refund outstanding', 'code': 'MKTPRO',
            'account_type': 'asset_current', 'reconcile': True,
            'company_id': company.id,
        })
        sale = self.env['account.journal'].create({
            'name': 'Partial refund sale', 'code': 'MKRFS',
            'type': 'sale', 'company_id': company.id,
        })
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': partner.id,
            'journal_id': sale.id,
            'invoice_line_ids': [(0, 0, {
                'name': 'Partial refund source', 'quantity': 1,
                'price_unit': 100, 'account_id': income.id,
            })],
        })
        invoice.action_post()
        wizard = self.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=invoice.ids,
        ).create({
            'journal_id': bank.id, 'payment_method_line_id': method.id,
            'markup_provider_id': provider.id,
        })
        payment = wizard._create_payments()
        self.assertEqual(
            (payment.markup_net_amount, payment.markup_tax_amount,
             payment.markup_gross_amount), (3.0, 0.6, 3.6),
        )
        first = payment.action_refund_markup(0.03)
        self.assertAlmostEqual(sum(first.line_ids.mapped('debit')), 0.03, places=2)
        self.assertAlmostEqual(sum(first.line_ids.mapped('credit')), 0.03, places=2)
        self.assertEqual(
            (payment.markup_refunded_net_amount, payment.markup_refunded_tax_amount),
            (0.02, 0.01),
        )
        second = payment.action_refund_markup(0.03)
        self.assertAlmostEqual(sum(second.line_ids.mapped('debit')), 0.03, places=2)
        self.assertAlmostEqual(sum(second.line_ids.mapped('credit')), 0.03, places=2)
        self.assertAlmostEqual(payment.markup_refunded_amount, 0.06, places=2)
        self.assertAlmostEqual(
            payment.markup_refunded_net_amount + payment.markup_refunded_tax_amount,
            payment.markup_refunded_amount, places=2,
        )
        # The final refund must use the original posted tax bucket exactly;
        # re-flooring the cumulative tax would leave the income/tax residuals
        # while the gross cap is already exhausted.
        final = payment.action_refund_markup(3.54)
        self.assertAlmostEqual(sum(final.line_ids.mapped('debit')), 3.54, places=2)
        self.assertAlmostEqual(sum(final.line_ids.mapped('credit')), 3.54, places=2)
        original_tax_base = sum(
            line.tax_base_amount for line in payment.move_id.line_ids
            if line.tax_repartition_line_id
        )
        refunded_tax_base = sum(
            line.tax_base_amount for move in (first | second | final)
            for line in move.line_ids if line.tax_repartition_line_id
        )
        self.assertAlmostEqual(refunded_tax_base, original_tax_base, places=2)
        self.assertAlmostEqual(payment.markup_refunded_amount, 3.6, places=2)
        self.assertAlmostEqual(payment.markup_refunded_net_amount, 3.0, places=2)
        self.assertAlmostEqual(payment.markup_refunded_tax_amount, 0.6, places=2)

    def test_unequal_tax_refunds_keep_bucket_allocations_monotonic(self):
        """Sequential cent refunds must remain balanced per native tax bucket."""
        company = self.env.company
        company.country_id = self.env.ref('base.us')
        income = self.env['account.account'].create({
            'name': 'Unequal refund income', 'code': 'MKTURI',
            'account_type': 'income', 'company_id': company.id,
        })
        receivable = self.env['account.account'].create({
            'name': 'Unequal refund receivable', 'code': 'MKTURR',
            'account_type': 'asset_receivable', 'reconcile': True,
            'company_id': company.id,
        })
        partner = self.env['res.partner'].create({
            'name': 'Unequal refund customer',
            'property_account_receivable_id': receivable.id,
        })
        taxes = self.env['account.tax'].create([
            {'name': 'Unequal ten', 'amount': 10, 'amount_type': 'percent',
             'type_tax_use': 'sale', 'company_id': company.id},
            {'name': 'Unequal thirty', 'amount': 30, 'amount_type': 'percent',
             'type_tax_use': 'sale', 'company_id': company.id},
        ])
        tax_accounts = self.env['account.account'].create([
            {'name': 'Unequal tax ten account', 'code': 'MKTUT1',
             'account_type': 'liability_current', 'company_id': company.id},
            {'name': 'Unequal tax thirty account', 'code': 'MKTUT2',
             'account_type': 'liability_current', 'company_id': company.id},
        ])
        for tax, account in zip(taxes, tax_accounts):
            tax.invoice_repartition_line_ids.filtered(
                lambda line: line.repartition_type == 'tax',
            ).account_id = account
        product = self.env['product.product'].create({
            'name': 'Unequal refund fee', 'detailed_type': 'service',
            'property_account_income_id': income.id,
            'taxes_id': [(6, 0, taxes.ids)],
        })
        provider = self.env['payment.provider'].search(
            [('name', '=', 'Wire Transfer')], limit=1,
        )
        provider.write({
            'state': 'test', 'markup_enabled': True, 'markup_percent': 3,
            'markup_product_id': product.id, 'markup_tax_included': False,
        })
        bank = self.env['account.journal'].create({
            'name': 'Unequal refund bank', 'code': 'MKTURB',
            'type': 'bank', 'company_id': company.id,
        })
        method = bank.inbound_payment_method_line_ids[:1]
        method.payment_account_id = self.env['account.account'].create({
            'name': 'Unequal refund outstanding', 'code': 'MKTURO',
            'account_type': 'asset_current', 'reconcile': True,
            'company_id': company.id,
        })
        sale = self.env['account.journal'].create({
            'name': 'Unequal refund sale', 'code': 'UFS01',
            'type': 'sale', 'company_id': company.id,
        })
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': partner.id,
            'journal_id': sale.id,
            'invoice_line_ids': [(0, 0, {
                'name': 'Unequal refund source', 'quantity': 1,
                'price_unit': 100, 'account_id': income.id,
            })],
        })
        invoice.action_post()
        wizard = self.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=invoice.ids,
        ).create({
            'journal_id': bank.id, 'payment_method_line_id': method.id,
            'markup_provider_id': provider.id,
        })
        payment = wizard._create_payments()
        self.assertEqual(
            (payment.markup_net_amount, payment.markup_tax_amount,
             payment.markup_gross_amount), (3.0, 1.2, 4.2),
        )
        original_tax_lines = payment.move_id.line_ids.filtered(
            lambda line: line.tax_repartition_line_id,
        )
        original_by_account = {
            line.account_id: abs(line.amount_currency) for line in original_tax_lines
        }
        refund_moves = []
        for amount in (0.07, 0.01, 4.12):
            refund = payment.action_refund_markup(amount)
            refund_moves.append(refund)
            self.assertAlmostEqual(
                sum(refund.line_ids.mapped('debit')),
                sum(refund.line_ids.mapped('credit')), places=2,
            )
        for account, original_amount in original_by_account.items():
            refunded = sum(
                line.amount_currency
                for move in refund_moves
                for line in move.line_ids
                if line.account_id == account
                and line.tax_repartition_line_id
            )
            self.assertAlmostEqual(refunded, original_amount, places=2)
        self.assertAlmostEqual(payment.markup_refunded_amount, 4.2, places=2)
        self.assertAlmostEqual(payment.markup_refunded_net_amount, 3.0, places=2)
        self.assertAlmostEqual(payment.markup_refunded_tax_amount, 1.2, places=2)

        # A second repeated-cent sequence exercises the same monotonic rule
        # with a different split, rather than relying on one lucky ordering.
        invoice_two = self.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': partner.id,
            'journal_id': sale.id,
            'invoice_line_ids': [(0, 0, {
                'name': 'Unequal refund second source', 'quantity': 1,
                'price_unit': 100, 'account_id': income.id,
            })],
        })
        invoice_two.action_post()
        wizard_two = self.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=invoice_two.ids,
        ).create({
            'journal_id': bank.id, 'payment_method_line_id': method.id,
            'markup_provider_id': provider.id,
        })
        payment_two = wizard_two._create_payments()
        for amount in (0.01, 0.01, 4.18):
            refund = payment_two.action_refund_markup(amount)
            self.assertAlmostEqual(
                sum(refund.line_ids.mapped('debit')),
                sum(refund.line_ids.mapped('credit')), places=2,
            )
        self.assertAlmostEqual(payment_two.markup_refunded_amount, 4.2, places=2)
        self.assertAlmostEqual(payment_two.markup_refunded_net_amount, 3.0, places=2)
        self.assertAlmostEqual(payment_two.markup_refunded_tax_amount, 1.2, places=2)

    def test_manual_refund_foreign_currency_preserves_native_balances(self):
        """Refunds keep company balances and payment-currency evidence distinct."""
        company = self.env.company
        company.country_id = self.env.ref('base.us')
        foreign = self.env.ref('base.USD')
        foreign.active = True
        self.env['res.currency.rate'].create({
            'name': fields.Date.context_today(self.env.user), 'currency_id': foreign.id,
            'company_id': company.id, 'rate': 2.0,
        })
        income = self.env['account.account'].create({
            'name': 'FX refund income', 'code': 'MZFXI',
            'account_type': 'income', 'company_id': company.id,
        })
        receivable = self.env['account.account'].create({
            'name': 'FX refund receivable', 'code': 'MZFXR',
            'account_type': 'asset_receivable', 'reconcile': True,
            'company_id': company.id,
        })
        partner = self.env['res.partner'].create({
            'name': 'FX refund customer',
            'property_account_receivable_id': receivable.id,
        })
        taxes = self.env['account.tax'].create([
            {'name': 'FX ten', 'amount': 10, 'amount_type': 'percent',
             'type_tax_use': 'sale', 'company_id': company.id},
            {'name': 'FX thirty', 'amount': 30, 'amount_type': 'percent',
             'type_tax_use': 'sale', 'company_id': company.id},
        ])
        tax_accounts = self.env['account.account'].create([
            {'name': 'FX tax ten account', 'code': 'MZFXT1',
             'account_type': 'liability_current', 'company_id': company.id},
            {'name': 'FX tax thirty account', 'code': 'MZFXT2',
             'account_type': 'liability_current', 'company_id': company.id},
        ])
        for tax, account in zip(taxes, tax_accounts):
            tax.invoice_repartition_line_ids.filtered(
                lambda line: line.repartition_type == 'tax',
            ).account_id = account
        product = self.env['product.product'].create({
            'name': 'FX refund fee', 'detailed_type': 'service',
            'property_account_income_id': income.id,
            'taxes_id': [(6, 0, taxes.ids)],
        })
        provider = self.env['payment.provider'].search(
            [('name', '=', 'Wire Transfer')], limit=1,
        )
        provider.write({
            'state': 'test', 'markup_enabled': True, 'markup_percent': 3,
            'markup_product_id': product.id, 'markup_tax_included': False,
        })
        bank = self.env['account.journal'].create({
            'name': 'FX refund bank', 'code': 'MZFXB',
            'type': 'bank', 'company_id': company.id,
        })
        method = bank.inbound_payment_method_line_ids[:1]
        method.payment_account_id = self.env['account.account'].create({
            'name': 'FX refund outstanding', 'code': 'MZFXO',
            'account_type': 'asset_current', 'reconcile': True,
            'company_id': company.id,
        })
        sale = self.env['account.journal'].create({
            'name': 'FX refund sale', 'code': 'MZFXS',
            'type': 'sale', 'company_id': company.id,
        })
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice', 'currency_id': foreign.id,
            'partner_id': partner.id, 'journal_id': sale.id,
            'invoice_line_ids': [(0, 0, {
                'name': 'FX refund source', 'quantity': 1,
                'price_unit': 100, 'account_id': income.id,
            })],
        })
        invoice.action_post()
        wizard = self.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=invoice.ids,
        ).create({
            'journal_id': bank.id, 'payment_method_line_id': method.id,
            'markup_provider_id': provider.id,
        })
        payment = wizard._create_payments()
        original = payment.move_id.line_ids.filtered(
            lambda line: line.markup_decision_source == 'manual',
        )
        original_by_account = {
            line.account_id: (abs(line.balance), abs(line.amount_currency))
            for line in original
        }
        first = payment.action_refund_markup(0.01)
        final = payment.action_refund_markup(4.19)
        refunds = first | final
        self.assertAlmostEqual(
            sum(refunds.line_ids.mapped('debit')),
            sum(refunds.line_ids.mapped('credit')), places=2,
        )
        for account, (balance, amount_currency) in original_by_account.items():
            lines = refunds.line_ids.filtered(
                lambda line: line.account_id == account
                and line.markup_decision_source == 'refund'
                and not line.tax_repartition_line_id,
            )
            if not lines:
                lines = refunds.line_ids.filtered(
                    lambda line: line.account_id == account
                    and line.markup_decision_source == 'refund'
                    and line.tax_repartition_line_id,
                )
            self.assertAlmostEqual(
                sum(lines.mapped('amount_currency')), amount_currency, places=2,
            )
        receivable_lines = refunds.line_ids.filtered(
            lambda line: line.account_id == receivable,
        )
        self.assertAlmostEqual(sum(receivable_lines.mapped('amount_currency')), -4.2, places=2)
        for account, (balance, _amount_currency) in original_by_account.items():
            account_lines = refunds.line_ids.filtered(
                lambda line: line.account_id == account
                and line.markup_decision_source == 'refund'
            )
            if account_lines:
                self.assertAlmostEqual(
                    sum(account_lines.mapped('debit')), balance, places=2,
                )

        # The same original evidence must also survive a three-part FX split
        # whose first two requests are both below one company-currency cent.
        invoice_two = self.env['account.move'].create({
            'move_type': 'out_invoice', 'currency_id': foreign.id,
            'partner_id': partner.id, 'journal_id': sale.id,
            'invoice_line_ids': [(0, 0, {
                'name': 'FX refund second source', 'quantity': 1,
                'price_unit': 100, 'account_id': income.id,
            })],
        })
        invoice_two.action_post()
        wizard_two = self.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=invoice_two.ids,
        ).create({
            'journal_id': bank.id, 'payment_method_line_id': method.id,
            'markup_provider_id': provider.id,
        })
        payment_two = wizard_two._create_payments()
        original_two = payment_two.move_id.line_ids.filtered(
            lambda line: line.markup_decision_source == 'manual'
        )
        original_two_by_account = {
            line.account_id: abs(line.balance) for line in original_two
        }
        refunds_two = self.env['account.move']
        for amount in (0.07, 0.01, 4.12):
            refunds_two |= payment_two.action_refund_markup(amount)
        self.assertAlmostEqual(
            sum(refunds_two.line_ids.mapped('debit')),
            sum(refunds_two.line_ids.mapped('credit')), places=2,
        )
        for account, balance in original_two_by_account.items():
            account_lines = refunds_two.line_ids.filtered(
                lambda line: line.account_id == account
                and line.markup_decision_source == 'refund'
            )
            if account_lines:
                self.assertAlmostEqual(
                    sum(account_lines.mapped('debit')), balance, places=2,
                )
        self.assertAlmostEqual(payment_two.markup_refunded_amount, 4.2, places=2)
