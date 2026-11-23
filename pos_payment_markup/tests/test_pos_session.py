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
            groups='point_of_sale.group_pos_user',
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
            'company_id': cls.company.id,
        })
        cls.markup_product = cls.env['product.product'].create({
            'name': 'POS markup loader fee',
            'detailed_type': 'service',
            'available_in_pos': False,
            'property_account_income_id': cls.income_account.id,
            'company_id': cls.company.id,
        })
        cls.provider = cls.env['payment.provider'].search([
            ('company_id', '=', cls.company.id),
        ], limit=1)
        cls.provider.write({
            'markup_enabled': True,
            'markup_percent': 1.25,
            'markup_product_id': cls.markup_product.id,
        })

    def test_ordinary_pos_user_gets_company_fee_product_without_provider_acl(self):
        self.assertFalse(self.pos_user.has_group('base.group_system'))
        session = self.env['pos.session'].new({'config_id': self.pos_config.id})

        params = session.with_user(self.pos_user)._loader_params_product_product()

        products = self.env['product.product'].with_user(self.pos_user).search(
            params['search_params']['domain']
        )
        self.assertIn(self.markup_product, products)

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
        values = self.env['pos.order'].with_user(self.pos_user)._payment_fields(order, {
            'name': '2026-01-01 12:00:00', 'amount': 61.2,
            'payment_method_id': method.id, 'markup_base_amount': 60,
            'markup_rate_applied': 2, 'markup_amount': 1.2,
            'markup_total_amount': 61.2, 'markup_amount_source': 'local',
        })
        self.assertEqual(values['markup_amount'], 1.2)
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
        self.pos_config.limited_products_loading = True
        session = self.env['pos.session'].new({'config_id': self.pos_config.id})
        params = session._loader_params_product_product()

        products = session._get_pos_ui_product_product(params)

        self.assertIn(self.markup_product.id, [product['id'] for product in products])
