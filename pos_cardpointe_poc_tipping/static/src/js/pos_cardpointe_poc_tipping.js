/** @odoo-module */

import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";
import { CardPointePOC } from "@pos_cardpointe_poc/js/pos_cardpointe_poc";

patch(CardPointePOC.prototype, {
    async send_payment_request(uuid) {
        this._cardpointeNativeTipPromise = null;
        const approved = await super.send_payment_request(...arguments);
        if (approved && this._cardpointeNativeTipPromise) {
            try {
                await this._cardpointeNativeTipPromise;
            } catch (error) {
                console.error("CardPointe approved, but native Odoo tip update failed", error);
                this._showError(
                    _t("The card was approved, but Odoo could not update the native tip line. Review this order before validating it.")
                );
            } finally {
                this._cardpointeNativeTipPromise = null;
            }
        }
        return approved;
    },

    _applyApprovedCardPointeResult(line, result, captureMethod = "terminal") {
        super._applyApprovedCardPointeResult(...arguments);

        if (captureMethod !== "terminal" || !result.cardpointe_tip_prompted) {
            return;
        }

        const tipAmount = Number.parseFloat(result.cardpointe_tip_amount || 0) || 0;
        line.cardpointe_tip_amount = tipAmount;
        line.cardpointe_base_amount = Number.parseFloat(result.cardpointe_base_amount || 0) || 0;
        this._cardpointeNativeTipPromise = this._applyCardPointeNativeTip(tipAmount);
    },

    async _applyCardPointeNativeTip(tipAmount) {
        const order = this.pos.get_order();
        if (!order) {
            throw new Error("No active POS order");
        }
        if (!this.pos.config.iface_tipproduct || !this.pos.config.tip_product_id) {
            throw new Error("Native POS tip product is not configured");
        }

        // Native Odoo behavior creates or updates the configured tip-product line.
        await this.pos.set_tip(tipAmount);

        // Odoo restaurant uses this flag to record that after-payment tipping was handled.
        // Set it for both a positive tip and an explicit zero-tip selection.
        order.after_payment_tipping_set = true;
    },
});
