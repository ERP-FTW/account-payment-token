from odoo import models


class ProductProduct(models.Model):
    _inherit = 'product.product'

    def _add_missing_products(self, products, config_id, data):
        super()._add_missing_products(products, config_id, data)
        config = self.env['pos.config'].browse(config_id)
        methods = config.payment_method_ids.filtered(lambda m: m.company_id == config.company_id)
        fee_products = methods.mapped('markup_product_id')
        missing = set(fee_products.ids) - {product['id'] for product in products}
        if missing:
            products.extend(self._load_product_with_domain([('id', 'in', list(missing))], config_id))
