odoo.define('pos_payment_markup.payment_markup', function (require) {
    'use strict';

    const models = require('point_of_sale.models');
    const Registries = require('point_of_sale.Registries');
    const utils = require('web.utils');
    const roundPrecision = utils.round_precision;

    const MarkupPayment = (Payment) => class extends Payment {
        init_from_JSON(json) {
            super.init_from_JSON(json);
            this.markup_base_amount = Number(json.markup_base_amount || 0);
            this.markup_rate_applied = Number(json.markup_rate_applied || 0);
            this.markup_amount = Number(json.markup_amount || 0);
            this.markup_total_amount = Number(json.markup_total_amount || 0);
            this.markup_amount_source = json.markup_amount_source || 'none';
            this.markup_payment_reference = json.markup_payment_reference || '';
            this.markup_authorized = Boolean(json.markup_authorized);
            this.markup_authorized_amount = json.markup_authorized_amount === undefined
                ? null : Number(json.markup_authorized_amount);
            this.markup_evidence = json.markup_evidence || null;
        }

        _clear_markup() {
            this.markup_base_amount = 0;
            this.markup_rate_applied = 0;
            this.markup_amount = 0;
            this.markup_total_amount = 0;
            this.markup_amount_source = 'none';
        }

        set_markup_evidence(evidence) {
            if (this.markup_authorized) return;
            // A browser supplied boolean is not processor provenance.  The
            // CardPointe server adapter must stamp the response explicitly;
            // otherwise the POS remains a normal, fee-free payment.
            this.markup_evidence = evidence || null;
            // Recalculate from the unpaid allocation, never from an existing gross total.
            const base = this.markup_base_amount || this.amount || 0;
            if (!evidence) {
                this._clear_markup();
                return super.set_amount(base);
            }
            return this.set_amount(base);
        }

        set_markup_authorized(amount, evidence) {
            const finalAmount = Number(amount || 0);
            const trustedEvidence = evidence && evidence.provenance === 'cardpointe-server' &&
                evidence.server_trusted_markup_evidence === true && evidence.server_verified &&
                evidence.verified && ['credit', 'credit_card'].includes(String(evidence.card_type || '').toLowerCase()) &&
                evidence.surcharge_present === false;
            const currency = this.pos.currency;
            const tolerance = Number(currency && currency.rounding || 0.01) / 2;
            const manualEligible = !evidence && this.payment_method &&
                this.payment_method.markup_enabled && this.markup_amount_source === 'local' &&
                Math.abs(finalAmount - Number(this.markup_total_amount || 0)) < tolerance;
            this.markup_authorized = true;
            this.markup_authorized_amount = finalAmount;
            this.markup_evidence = trustedEvidence ? evidence : null;
            if (!trustedEvidence && !manualEligible) {
                // Processor evidence must be trusted.  Manual mode is accepted
                // only when the approved amount exactly matches the already
                // disclosed provider-configured total.
                this._clear_markup();
            }
            // The approved amount is authoritative and already includes the
            // processor or configured manual fee.
            if (trustedEvidence || manualEligible) {
                this.markup_total_amount = finalAmount;
            }
            const result = super.set_amount(finalAmount);
            this._sync_markup_orderline();
            return result;
        }

        authorize_markup() {
            return this.set_markup_authorized(this.amount);
        }

        set_amount(amount) {
            if (this.markup_authorized && this.markup_authorized_amount !== null) {
                return super.set_amount(this.markup_authorized_amount);
            }
            const value = Number(amount || 0);
            const method = this.payment_method;
            const evidence = this.markup_evidence || {};
            const hasTerminalEvidence = Boolean(evidence.provenance);
            const eligible = Boolean(method && method.markup_enabled &&
                (!hasTerminalEvidence || (evidence.provenance === 'cardpointe-server' &&
                 evidence.server_trusted_markup_evidence === true && evidence.server_verified &&
                 evidence.verified && ['credit', 'credit_card'].includes(String(evidence.card_type || '').toLowerCase()) &&
                 evidence.surcharge_present === false)));
            if (!eligible) {
                this._clear_markup();
                const result = super.set_amount(value);
                this._sync_markup_orderline();
                return result;
            }
            const currency = this.pos.currency;
            const base = roundPrecision(value, currency.rounding);
            const processorAmount = evidence.processor_markup_amount;
            const hasProcessorAmount = processorAmount !== undefined && processorAmount !== null;
            let fee = hasProcessorAmount
                ? roundPrecision(Number(processorAmount || 0), currency.rounding)
                : roundPrecision(base * Number(method.markup_percent || 0) / 100, currency.rounding);
            // Keep the shared decision net of tax, but make the tender include
            // the configured fee-product taxes exactly once.  Odoo's POS
            // compute_all mirrors account.tax.compute_all for both exclusive
            // and price-included taxes.
            const feeProduct = method.markup_product_id &&
                this.pos.db.get_product_by_id(method.markup_product_id[0]);
            const taxIds = feeProduct && feeProduct.taxes_id || [];
            const taxes = this.pos.get_taxes_after_fp
                ? this.pos.get_taxes_after_fp(taxIds, this.fiscal_position)
                : taxIds.map(id => this.pos.taxes_by_id[id]).filter(Boolean);
            const configuredGrossFee = fee;
            let feeWithTax = fee;
            if (taxes.length && this.pos.compute_all) {
                if (method.markup_tax_included) {
                    // The configured percentage is the customer-facing gross
                    // fee.  Extract included taxes directly; for excluded
                    // taxes invert a tax-excluded calculation.
                    let low = 0;
                    let high = fee;
                    let probe;
                    const allIncluded = taxes.every(tax => tax.price_include);
                    if (allIncluded) {
                        probe = this.pos.compute_all(taxes, fee, 1, currency.rounding);
                        fee = roundPrecision(probe.total_excluded, currency.rounding);
                        feeWithTax = roundPrecision(probe.total_included, currency.rounding);
                    } else {
                        probe = this.pos.compute_all(taxes, fee, 1, currency.rounding, false);
                        if (probe.total_included > fee) {
                            for (let i = 0; i < 32; i++) {
                                const middle = (low + high) / 2;
                                probe = this.pos.compute_all(taxes, middle, 1, currency.rounding, false);
                                if (probe.total_included > fee) high = middle;
                                else low = middle;
                            }
                            fee = roundPrecision(low, currency.rounding);
                        }
                    }
                    // The rounded net price is the amount that can actually be
                    // serialized on an Odoo orderline.  Recompute the native
                    // gross from that price instead of retaining an unattainable
                    // configured gross (e.g. 0.05 gross -> 0.04 net -> 0.04
                    // gross with a 10% excluded tax after cent rounding).
                    if (!allIncluded) {
                        probe = this.pos.compute_all(
                            taxes, fee, 1, currency.rounding, false,
                        );
                        feeWithTax = roundPrecision(probe.total_included, currency.rounding);
                    }
                } else {
                    // The configured percentage is the tax-excluded net fee,
                    // even when the service product is price-included.
                    const taxData = this.pos.compute_all(taxes, fee, 1, currency.rounding, false);
                    feeWithTax = taxData.total_included;
                }
            }
            this.markup_base_amount = base;
            this.markup_rate_applied = hasProcessorAmount ? 0 : Number(method.markup_percent || 0);
            this.markup_amount = fee;
            this.markup_total_amount = roundPrecision(base + feeWithTax, currency.rounding);
            this.markup_amount_source = hasProcessorAmount ? 'processor' : 'local';
            const result = super.set_amount(this.markup_total_amount);
            this._sync_markup_orderline();
            return result;
        }

        _sync_markup_orderline() {
            const order = this.pos && this.pos.get_order && this.pos.get_order();
            if (order && typeof order._sync_markup_orderline === 'function') {
                order._sync_markup_orderline();
            }
        }

        export_as_JSON() {
            const json = super.export_as_JSON();
            json.markup_base_amount = this.markup_base_amount || 0;
            json.markup_rate_applied = this.markup_rate_applied || 0;
            json.markup_amount = this.markup_amount || 0;
            json.markup_total_amount = this.markup_total_amount || 0;
            json.markup_amount_source = this.markup_amount_source || 'none';
            json.markup_payment_reference = this.markup_payment_reference || '';
            json.markup_authorized = Boolean(this.markup_authorized);
            json.markup_authorized_amount = this.markup_authorized_amount;
            json.markup_evidence = this.markup_evidence || null;
            return json;
        }
    };
    Registries.Model.extend(models.Payment, MarkupPayment);

    const MarkupOrderline = (Orderline) => class extends Orderline {
        init_from_JSON(json) {
            super.init_from_JSON(json);
            this.markup_generated = Boolean(json.markup_generated);
        }

        export_as_JSON() {
            const json = super.export_as_JSON();
            json.markup_generated = Boolean(this.markup_generated);
            return json;
        }
    };
    Registries.Model.extend(models.Orderline, MarkupOrderline);

    const MarkupOrder = (Order) => class extends Order {
        remove_paymentline(line) {
            const result = super.remove_paymentline(line);
            this._sync_markup_orderline();
            return result;
        }

        _sync_markup_orderline() {
            const lines = this.get_paymentlines ? this.get_paymentlines() : [];
            if (!this.pos.db) return;
            this.get_orderlines().filter(line => line.markup_generated)
                .forEach(line => this.remove_orderline(line));
            const feeLines = [];
            lines.forEach(line => {
                const productId = line.payment_method && line.payment_method.markup_product_id &&
                    line.payment_method.markup_product_id[0];
                const grossFee = Number(line.markup_total_amount || 0) -
                    Number(line.markup_base_amount || 0);
                const netFee = Number(line.markup_amount || 0);
                if (productId && grossFee > 0) {
                    const product = this.pos.db.get_product_by_id(Number(productId));
                    const taxIds = product && product.taxes_id || [];
                    const fiscalPosition = this.fiscal_position;
                    const taxes = this.pos.get_taxes_after_fp
                        ? this.pos.get_taxes_after_fp(taxIds, fiscalPosition)
                        : taxIds.map(id => this.pos.taxes_by_id[id]).filter(Boolean);
                    // POS orderline prices must reproduce the customer-facing
                    // gross fee under the effective taxes.  A mixed tax set is
                    // neither a net-price nor a gross-price case: Odoo's
                    // included taxes are extracted before excluded taxes are
                    // added.  Invert compute_all for that case so the generated
                    // line's native tax total equals the payment surcharge.
                    let unitFee = netFee;
                    if (taxes.length && this.pos.compute_all) {
                        const allIncluded = taxes.every(tax => tax.price_include);
                        const allExcluded = taxes.every(tax => !tax.price_include);
                        if (allIncluded) {
                            unitFee = grossFee;
                        } else if (!allExcluded) {
                            let low = 0;
                            let high = Math.max(grossFee, netFee, this.pos.currency.rounding);
                            const totalAt = value => this.pos.compute_all(
                                taxes, value, 1, this.pos.currency.rounding,
                            ).total_included;
                            while (totalAt(high) < grossFee) high *= 2;
                            for (let i = 0; i < 32; i++) {
                                const middle = (low + high) / 2;
                                if (totalAt(middle) < grossFee) low = middle;
                                else high = middle;
                            }
                            const lowPrice = roundPrecision(low, this.pos.currency.rounding);
                            const highPrice = roundPrecision(high, this.pos.currency.rounding);
                            unitFee = Math.abs(totalAt(lowPrice) - grossFee) <=
                                Math.abs(totalAt(highPrice) - grossFee) ? lowPrice : highPrice;
                        }
                    }
                    feeLines.push({productId: Number(productId), unitFee});
                }
            });
            feeLines.forEach(({productId, unitFee}) => {
                const product = this.pos.db.get_product_by_id(productId);
                if (!product) return;
                // Keep each independently rounded tender in its own native line.
                // Combining same-product fees changes tax rounding and can add a
                // cent that was not disclosed or authorized by the processor.
                this.add_product(product, {
                    price: roundPrecision(unitFee, this.pos.currency.rounding),
                    quantity: 1, discount: 0, merge: false,
                });
                const created = this.get_last_orderline && this.get_last_orderline();
                if (created) {
                    created.markup_generated = true;
                    created.price_manually_set = true;
                }
            });
        }
    };
    Registries.Model.extend(models.Order, MarkupOrder);
});
