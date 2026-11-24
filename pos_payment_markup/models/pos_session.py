from odoo import models
from odoo.osv.expression import OR


class PosSession(models.Model):
    _inherit = 'pos.session'

    def _markup_product_ids(self):
        return self.env['payment.provider'].sudo().search([
            ('company_id', '=', self.config_id.company_id.id),
            ('markup_enabled', '=', True),
            ('markup_product_id', '!=', False),
        ]).mapped('markup_product_id').ids

    def _loader_params_pos_payment_method(self):
        params = super()._loader_params_pos_payment_method()
        fields = params['search_params']['fields']
        for field in ('markup_provider_id', 'markup_card_type', 'markup_enabled', 'markup_percent', 'markup_product_id', 'markup_tax_included'):
            if field not in fields:
                fields.append(field)
        return params

    def _loader_params_product_product(self):
        params = super()._loader_params_product_product()
        # POS users intentionally have no general access to payment providers.
        # Elevate only this company-scoped projection of configured fee product
        # ids; the provider records and their configuration are never exposed.
        product_ids = self._markup_product_ids()
        if product_ids:
            domain = params['search_params']['domain']
            params['search_params']['domain'] = OR([
                domain, [('id', 'in', product_ids)]
            ])
        return params

    def _get_pos_ui_product_product(self, params):
        products = super()._get_pos_ui_product_product(params)
        # Limited product loading bypasses the loader domain entirely. Ensure
        # the narrowly projected fee products are still present so their taxes
        # and the generated fee orderline remain available in the live POS.
        loaded_ids = {product['id'] for product in products}
        missing_ids = set(self._markup_product_ids()) - loaded_ids
        if missing_ids:
            session = self.with_context(**params['context'])
            extra_products = session.env['product.product'].search_read(
                [('id', 'in', list(missing_ids))],
                fields=params['search_params']['fields'],
            )
            session._process_pos_ui_product_product(extra_products)
            products.extend(extra_products)
        return products
