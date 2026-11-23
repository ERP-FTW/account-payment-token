from odoo.tests import new_test_user
from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase


class TestPosSessionMarkupLoader(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.pos_user = new_test_user(
            cls.env,
            login='pos_markup_loader_user',
            groups='base.group_user,point_of_sale.group_pos_user',
            company_id=cls.company.id,
            company_ids=[(6, 0, cls.company.ids)],
        )
        cls.pos_config = cls.env['pos.config'].create({
            'name': 'Markup loader regression',
            'company_id': cls.company.id,
        })
        cls.income_account = cls.env['account.account'].create({
            'name': 'POS markup loader income',
            'code': 'PMLI',
            'account_type': 'income',
            'company_ids': [(6, 0, cls.company.ids)],
        })
        cls.markup_product = cls.env['product.product'].create({
            'name': 'POS markup loader fee',
            'type': 'service',
            'available_in_pos': False,
            'property_account_income_id': cls.income_account.id,
            'company_id': cls.company.id,
        })
        cls.provider = cls.env['payment.provider'].search([
            ('company_id', '=', cls.company.id),
        ], limit=1)
        cls.method = cls.env['pos.payment.method'].create({
            'name': 'Loader fee method', 'company_id': cls.company.id,
            'markup_provider_id': cls.provider.id, 'markup_card_type': 'credit',
        })
        cls.pos_config.payment_method_ids = cls.method
        cls.provider.write({
            'markup_enabled': True,
            'markup_percent': 1.25,
            'markup_product_id': cls.markup_product.id,
        })

    def test_ordinary_pos_user_gets_company_fee_product_without_provider_acl(self):
        self.assertFalse(self.pos_user.has_group('base.group_system'))
        products = []
        self.env['product.product'].with_user(self.pos_user)._add_missing_products(
            products, self.pos_config.id, {'pos.order.line': {'data': []}},
        )
        self.assertIn(self.markup_product.id, [p['id'] for p in products])

    def test_cashier_payment_validation_projects_fee_without_provider_access(self):
        self.provider.markup_percent = 2
        self.markup_product.taxes_id = [(5, 0, 0)]
        method = self.env['pos.payment.method'].create({
            'name': 'Mapped manual card', 'company_id': self.company.id,
            'markup_provider_id': self.provider.id, 'markup_card_type': 'credit',
        })
        cashier_method = method.with_user(self.pos_user)
        with self.assertRaises(AccessError):
            self.provider.with_user(self.pos_user).read(['name'])
        preview = cashier_method._markup_preview(60)
        self.assertEqual((preview['amount'], preview['total']), (1.2, 61.2))
        order = self.env['pos.order'].new({
            'session_id': self.env['pos.session'].new({'config_id': self.pos_config.id}),
            'pricelist_id': self.pos_config.pricelist_id.id,
        })
        order.lines = [(0, 0, {
            'product_id': self.markup_product.id, 'qty': 1, 'price_unit': 1.2,
            'price_subtotal': 1.2, 'price_subtotal_incl': 1.2,
            'markup_generated': True, 'markup_payment_reference': 'synthetic-payment',
        })]
        order.payment_ids = [(0, 0, {
            'amount': 61.2, 'payment_method_id': method.id, 'uuid': 'synthetic-payment',
            'markup_base_amount': 60, 'markup_amount': 1.2,
            'markup_total_amount': 61.2, 'markup_amount_source': 'local',
        })]
        order.with_user(self.pos_user)._validate_markup_evidence()
        # The projection is not a provider read grant.
        with self.assertRaises(AccessError):
            self.provider.with_user(self.pos_user).read(['name'])
        method.markup_card_type = 'unknown'
        self.assertFalse(cashier_method._markup_preview(60)['eligible'])
        method.write({'markup_card_type': 'credit', 'markup_provider_id': False})
        self.assertFalse(cashier_method._markup_preview(60)['eligible'])
        method.markup_provider_id = self.provider
        cash = self.env['account.journal'].create({
            'name': 'Mapping cash control', 'code': 'PMCC', 'type': 'cash',
            'company_id': self.company.id,
        })
        method.journal_id = cash
        self.assertFalse(cashier_method._markup_preview(60)['eligible'])
        method.journal_id = False
        other_company = self.env['res.company'].create({'name': 'Other fee company'})
        other_provider = self.env['payment.provider'].create({
            'name': 'Other fee provider', 'code': 'none', 'company_id': other_company.id,
        })
        method.markup_provider_id = other_provider
        self.assertFalse(cashier_method._markup_preview(60)['eligible'])

    def test_limited_product_loader_includes_fee_product(self):
        # The target hook is called after both full and limited native loading.
        products = []
        self.env['product.product']._add_missing_products(
            products, self.pos_config.id, {'pos.order.line': {'data': []}},
        )
        self.assertIn(self.markup_product.id, [product['id'] for product in products])
