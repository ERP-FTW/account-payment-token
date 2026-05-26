/** @odoo-module */

import { _t } from "@web/core/l10n/translation";
import { rpc } from "@web/core/network/rpc";
import { AlertDialog, ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { PaymentInterface } from "@point_of_sale/app/payment/payment_interface";
import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";
import { patch } from "@web/core/utils/patch";
import { CardPointeManualEntryPopup } from "./manual_entry_popup";
import { register_payment_method } from "@point_of_sale/app/store/pos_store";

export class CardPointePOC extends PaymentInterface {
    setup() {
        super.setup(...arguments);
        this._activeRequestByUuid = {};
        this._cashierCancelledByUuid = {};
    }

    _findLine(order, uuid) {
        return order.payment_ids.find((paymentLine) => paymentLine.uuid === uuid);
    }

    async send_payment_request(uuid) {
        super.send_payment_request(uuid);
        const order = this.pos.get_order();
        const line = this._findLine(order, uuid);
        if (!line) {
            return false;
        }
        if (line.amount === 0) {
            this._showError(_t("Amount must be greater than zero."));
            line.set_payment_status("retry");
            delete this._cashierCancelledByUuid[uuid];
            return false;
        }

        if (line.amount < 0) {
            return this._send_refund_request(order, line);
        }

        line.set_payment_status("waitingCard");
        let startResult;
        try {
            startResult = await rpc(
                "/pos_cardpointe_poc/start",
                {
                    pos_config_id: this.pos.config.id,
                    payment_method_id: line.payment_method_id.id,
                    amount: line.amount,
                    currency: this.pos.currency.name,
                    order_uid: order.uuid,
                    payment_line_uuid: line.uuid,
                    payment_id: typeof line.id === "number" ? line.id : false,
                    payment_client_id: line.id || false,
                },
                { silent: true }
            );
        } catch {
            this._showError(_t("Could not reach Odoo server while starting terminal payment."));
            line.set_payment_status("retry");
            return false;
        }

        if (startResult.status !== "ready" || !startResult.request_id) {
            this._handleFailedResult(line, startResult);
            return false;
        }

        this._activeRequestByUuid[uuid] = startResult.request_id;
        delete this._cashierCancelledByUuid[uuid];
        let result;
        try {
            result = await rpc(
                "/pos_cardpointe_poc/auth",
                { request_id: startResult.request_id, amount: line.amount },
                { silent: true }
            );
        } catch {
            this._showError(_t("Could not reach Odoo server during terminal payment."));
            line.set_payment_status("retry");
            delete this._activeRequestByUuid[uuid];
            return false;
        }
        delete this._activeRequestByUuid[uuid];

        if (result.status === "approved") {
            this._applyApprovedCardPointeResult(line, result, "terminal");
            return true;
        }

        if (result.status === "cancelled" && this._cashierCancelledByUuid[uuid]) {
            line.cardpointe_status = "cancelled";
            line.cardpointe_respcode = result.respcode || "";
            line.cardpointe_resptext = result.resptext || "";
            line.set_payment_status("retry");
            delete this._cashierCancelledByUuid[uuid];
            return false;
        }

        this._handleFailedResult(line, result);
        delete this._cashierCancelledByUuid[uuid];
        return false;
    }

    async _send_refund_request(order, line) {
        const refundedOrderLineIds = order.lines
            .filter((orderLine) => orderLine.refunded_orderline_id)
            .map((orderLine) => orderLine.refunded_orderline_id.id || orderLine.refunded_orderline_id);
        if (!refundedOrderLineIds.length) {
            this._showError(
                _t("Refund must be started from a paid ticket so original CardPointe payment can be located.")
            );
            line.set_payment_status("retry");
            return false;
        }

        line.set_payment_status("waiting");
        let result;
        try {
            result = await rpc(
                "/pos_cardpointe_poc/refund",
                {
                    payment_method_id: line.payment_method_id.id,
                    amount: line.amount,
                    refunded_orderline_ids: refundedOrderLineIds,
                },
                { silent: true }
            );
        } catch {
            this._showError(_t("Could not reach Odoo server during CardPointe refund."));
            line.set_payment_status("retry");
            return false;
        }

        if (result.status !== "approved") {
            this._handleFailedResult(line, result);
            return false;
        }

        line.cardpointe_retref = result.retref || "";
        line.cardpointe_original_retref = result.original_retref || "";
        line.cardpointe_respcode = result.respcode || "";
        line.cardpointe_resptext = result.resptext || "";
        line.cardpointe_status = "approved";
        line.cardpointe_operation = result.operation || "refund";
        line.cardpointe_ok = !!result.ok;
        line.transaction_id = result.retref || "";
        line.set_payment_status("done");
        return true;
    }

    async send_payment_cancel(order, uuid) {
        super.send_payment_cancel(order, uuid);
        const line = this._findLine(order, uuid);
        if (!line) {
            return false;
        }

        const requestId = this._activeRequestByUuid[uuid];
        if (!requestId) {
            line.set_payment_status("retry");
            delete this._cashierCancelledByUuid[uuid];
            return true;
        }

        this._cashierCancelledByUuid[uuid] = true;

        let result;
        try {
            result = await rpc("/pos_cardpointe_poc/cancel", { request_id: requestId }, { silent: true });
        } catch {
            this._showError(_t("Could not reach Odoo server to cancel terminal payment."));
            line.set_payment_status("retry");
            delete this._cashierCancelledByUuid[uuid];
            return false;
        }

        delete this._activeRequestByUuid[uuid];
        line.cardpointe_status = result.status || "error";
        line.cardpointe_ok = false;
        line.cardpointe_signature_required = false;
        line.cardpointe_signature_captured = false;
        line.cardpointe_signature_method = "";
        line.cardpointe_respcode = result.respcode || "";
        line.cardpointe_resptext = result.resptext || "";
        line.set_payment_status("retry");

        if (result.status !== "cancelled") {
            this._showError(result.message || _t("Cancel request was not accepted by terminal."));
            delete this._cashierCancelledByUuid[uuid];
            return false;
        }
        return true;
    }

    _handleFailedResult(line, result) {
        line.cardpointe_status = result.status || "error";
        line.cardpointe_ok = false;
        line.cardpointe_signature_required = false;
        line.cardpointe_signature_captured = false;
        line.cardpointe_signature_method = "";
        line.cardpointe_respcode = result.respcode || "";
        line.cardpointe_resptext = result.resptext || "";
        line.set_payment_status("retry");

        const canManual = line.payment_method_id.cardpointe_manual_entry_enabled && line.amount > 0;
        if (canManual && ["timeout", "in_use", "merchant_mode", "error"].includes(result.status || "error")) {
            this.env.services.dialog.add(ConfirmationDialog, {
                title: _t("Card Reader Unavailable"),
                body: result.message || result.resptext || _t("Card payment not approved."),
                confirmLabel: _t("Manual Entry"),
                cancelLabel: _t("Cancel"),
                confirm: () => this.open_manual_entry(line.uuid, {
                    fallback_reason: ({ timeout: "terminal_timeout", in_use: "terminal_in_use", merchant_mode: "terminal_merchant_mode" })[result.status] || "terminal_error",
                    terminal_error_status: result.status || "",
                    terminal_error_message: result.message || result.resptext || "",
                }),
            });
            return;
        }

        if (result.status === "merchant_mode") {
            this._showError(
                _t("Terminal is in Merchant Mode. Open the CardPointe Integrated/Bolt app or switch terminal to Integrated mode.")
            );
        } else if (result.status === "cancelled") {
            this._showError(_t("Payment cancelled on terminal."));
        } else if (result.status === "timeout") {
            this._showError(_t("Terminal request timed out. Please check device status and try again."));
        } else if (result.status === "in_use") {
            this._showError(_t("Terminal is in use, retry in a few seconds."));
        } else {
            this._showError(result.message || _t("Card payment not approved."));
        }
    }


    async open_manual_entry(uuid, options = {}) {
        const order = this.pos.get_order();
        const line = this._findLine(order, uuid);
        if (!line) return false;
        const config = await this._loadManualConfig(line);
        if (!config) return false;

        this.env.services.dialog.add(CardPointeManualEntryPopup, {
            title: _t("Enter Card Manually"),
            tokenizerUrl: config.tokenizer_url,
            allowedEcominds: config.allowed_ecominds || [["E", "E - Ecommerce"], ["T", "T - Telephone/Mail"]],
            defaultEcomind: config.default_ecomind || "E",
            cancelLabel: _t("Cancel"),
            onToken: ({ token, ecomind }) => this._submitManualAuth(line, order, token, ecomind, options),
        });
        return true;
    }

    async _loadManualConfig(line) {
        try {
            const result = await rpc("/pos_cardpointe_poc/manual_config", { pos_config_id: this.pos.config.id, payment_method_id: line.payment_method_id.id }, { silent: true });
            if (result.status !== "ok") {
                this._showError(result.message || _t("Manual Entry is unavailable."));
                return null;
            }
            return result;
        } catch {
            this._showError(_t("Could not reach Odoo server while loading Manual Entry."));
            return null;
        }
    }

    async _submitManualAuth(line, order, token, ecomind, options = {}) {
        line.set_payment_status("waiting");
        try {
            const result = await rpc("/pos_cardpointe_poc/manual_auth", {
                pos_config_id: this.pos.config.id,
                payment_method_id: line.payment_method_id.id,
                amount: line.amount,
                currency: this.pos.currency.name,
                order_uid: order.uuid,
                payment_line_uuid: line.uuid,
                token,
                ecomind,
                partner_id: order.get_partner()?.id || null,
                ...options,
            }, { silent: true });
            if (result.status === "approved") {
                this._applyApprovedCardPointeResult(line, result, "iframe_manual");
                return true;
            }
            this._handleFailedResult(line, result);
            throw new Error("manual_auth_not_approved");
        } catch {
            this._handleFailedResult(line, { status: "error", message: _t("Could not reach Odoo server during Manual Entry."), fallback_reason: "server_error" });
            throw new Error("manual_auth_rpc_error");
        }
    }

    _applyApprovedCardPointeResult(line, result, captureMethod = "terminal") {
        const approvedAmount = this._normalizeAmount(result.amount, line.amount);
        line.set_amount(approvedAmount);
        line.cardpointe_retref = result.retref || "";
        line.cardpointe_authcode = result.authcode || "";
        line.cardpointe_respcode = result.respcode || "";
        line.cardpointe_resptext = result.resptext || "";
        line.cardpointe_token = result.token || "";
        line.cardpointe_entrymode = result.entrymode || captureMethod || "";
        line.cardpointe_emvtagdata = result.emvTagData || "";
        line.cardpointe_status = "approved";
        line.cardpointe_operation = "sale";
        line.cardpointe_ok = true;
        line.cardpointe_capture_method = captureMethod;
        line.cardpointe_ecomind = result.ecomind || "";
        line.cardpointe_fallback_reason = result.fallback_reason || "";
        line.cardpointe_terminal_error_status = result.terminal_error_status || "";
        line.cardpointe_terminal_error_message = result.terminal_error_message || "";
        line.cardpointe_gateway_http_status = result.http_status || 0;
        line.cardpointe_signature_required = !!result.signature_required;
        line.cardpointe_signature_captured = !!result.signature_captured;
        line.cardpointe_signature_method = result.signature_method || "";
        line.transaction_id = result.retref || "";
        line.set_payment_status("done");
    }

    _normalizeAmount(amount, fallback) {
        if (amount === undefined || amount === null || amount === "") {
            return fallback;
        }
        const value = String(amount);
        if (value.includes(".")) {
            return parseFloat(value);
        }
        return parseFloat(value) / 100;
    }

    _showError(message) {
        this.env.services.dialog.add(AlertDialog, {
            title: _t("CardPointe POC"),
            body: message,
        });
    }
}

register_payment_method("cardpointe_poc", CardPointePOC);

patch(PaymentScreen.prototype, {
    async onClickCardPointeManualEntry() {
        const order = this.pos.get_order();
        const line = order?.selected_paymentline;
        if (!line || line.payment_method_id.use_payment_terminal !== "cardpointe_poc") return;
        await line.payment_method_id.payment_terminal.open_manual_entry(line.uuid, { fallback_reason: "manual_selected" });
    },
});
