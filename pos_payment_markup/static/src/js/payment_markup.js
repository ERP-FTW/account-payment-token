/** @odoo-module **/

import { patch } from '@web/core/utils/patch';
import { roundPrecision } from '@web/core/utils/numbers';
import { PosPayment } from '@point_of_sale/app/models/pos_payment';
import { PosOrder } from '@point_of_sale/app/models/pos_order';
import { PaymentScreen } from '@point_of_sale/app/screens/payment_screen/payment_screen';

patch(PosPayment.prototype, {
    set_amount(amount) {
        const order = this.pos_order_id;
        const method = this.payment_method_id;
        const value = Number(amount || 0);
        if (!order || !method) return super.set_amount(value);
        order.assert_editable();
        const oldLines = order.lines.filter(l => l.markup_generated && l.markup_payment_reference === this.uuid);
        if (!this._markupCashierInput && method.markup_enabled && oldLines.length === 1
                && this.markup_amount_source === 'local' && value === this.amount) {
            return super.set_amount(value); // Native retries already carry the disclosed gross.
        }
        oldLines.forEach(l => l.delete({ backend: true }));
        this.markup_base_amount = 0;
        this.markup_rate_applied = 0;
        this.markup_amount = 0;
        this.markup_total_amount = 0;
        this.markup_amount_source = 'none';
        if (!method.markup_enabled || value <= 0 || order.lines.some(l => l.refunded_orderline_id)) {
            return super.set_amount(value);
        }
        const round = x => roundPrecision(x, order.currency.rounding);
        const principal = order.lines.filter(l => !l.markup_generated)
            .reduce((sum, l) => sum + l.get_price_with_tax(), 0);
        const allocated = order.payment_ids.filter(p => p !== this)
            .reduce((sum, p) => sum + (p.markup_amount_source === 'local' ? p.markup_base_amount : p.amount), 0);
        const base = round(Math.max(0, Math.min(value, principal - allocated)));
        if (!base) return super.set_amount(value);
        const target = round(method.markup_mode === 'fixed' ? method.markup_fixed_amount : base * method.markup_percent / 100);
        if (!target) return super.set_amount(value);
        const product = method.markup_product_id;
        if (!product) throw new Error('Configured fee product is not loaded');
        const line = this.models['pos.order.line'].create({
            order_id: order, product_id: product, qty: 1, price_unit: target,
            price_type: 'manual', discount: 0, tax_ids: product.taxes_id.map(t => ['link', t]),
            markup_generated: true, markup_payment_reference: this.uuid,
        });
        // Invert the native line tax calculation, preserving price-included and mixed taxes.
        const measure = () => {
            const prices = line.get_all_prices();
            return method.markup_tax_included ? prices.priceWithTax : prices.priceWithoutTax;
        };
        let low = 0;
        let high = Math.max(target * 2, order.currency.rounding);
        line.price_unit = high;
        for (let i = 0; i < 12 && measure() < target; i++) {
            high *= 2;
            line.price_unit = high;
        }
        for (let i = 0; i < 40; i++) {
            const mid = (low + high) / 2;
            line.price_unit = mid;
            if (measure() < target) low = mid;
            else high = mid;
        }
        line.price_unit = round(high);
        const prices = line.get_all_prices();
        this.markup_base_amount = base;
        this.markup_rate_applied = method.markup_mode === 'fixed' ? 0 : method.markup_percent;
        this.markup_amount = round(prices.priceWithoutTax);
        this.markup_total_amount = round(base + prices.priceWithTax);
        this.markup_amount_source = 'local';
        this.markup_payment_reference = this.uuid;
        return super.set_amount(this.markup_total_amount);
    },
});

patch(PaymentScreen.prototype, {
    updateSelectedPaymentline(amount = false) {
        const payment = this.selectedPaymentLine;
        if (payment) payment._markupCashierInput = true;
        try {
            return super.updateSelectedPaymentline(amount);
        } finally {
            if (payment) payment._markupCashierInput = false;
        }
    },
});

patch(PosOrder.prototype, {
    remove_paymentline(payment) {
        this.assert_editable();
        this.lines.filter(l => l.markup_generated && l.markup_payment_reference === payment.uuid)
            .forEach(l => l.delete({ backend: true }));
        return super.remove_paymentline(payment);
    },
});
