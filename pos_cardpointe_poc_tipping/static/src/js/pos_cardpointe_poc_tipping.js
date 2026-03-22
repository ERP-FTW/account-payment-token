odoo.define('pos_cardpointe_poc_tipping.payment', function (require) {
    'use strict';

    const core = require('web.core');
    const rpc = require('web.rpc');
    const PaymentInterface = require('point_of_sale.PaymentInterface');
    const models = require('point_of_sale.models');
    const Registries = require('point_of_sale.Registries');
    const { Gui } = require('point_of_sale.Gui');

    const _t = core._t;

    const CardPointePOCTipping = PaymentInterface.extend({
        init: function () {
            this._super.apply(this, arguments);
            this._activeRequestByCid = {};
            this._cashierCancelledByCid = {};
        },

        send_payment_request: async function (cid) {
            this._super.apply(this, arguments);
            const order = this.pos.get_order();
            const line = order.paymentlines.find((paymentLine) => paymentLine.cid === cid);
            if (!line) {
                return false;
            }
            if (line.amount === 0) {
                this._showError(_t('Amount must be greater than zero.'));
                line.set_payment_status('retry');
                delete this._cashierCancelledByCid[cid];
                return false;
            }

            if (line.amount < 0) {
                return this._send_refund_request(order, line);
            }

            line.set_payment_status('waitingCard');
            let startResult;
            try {
                startResult = await rpc.query({
                    route: '/pos_cardpointe_poc/start',
                    params: {
                        pos_config_id: this.pos.config.id,
                        payment_method_id: line.payment_method.id,
                        amount: line.amount,
                        currency: this.pos.currency.name,
                        order_uid: order.uid,
                        payment_line_uuid: line.cid,
                    },
                }, { shadow: true, timeout: 30000 });
            } catch (_error) {
                this._showError(_t('Could not reach Odoo server while starting terminal payment.'));
                line.set_payment_status('retry');
                return false;
            }

            if (startResult.status !== 'ready' || !startResult.request_id) {
                this._handleFailedResult(line, startResult);
                return false;
            }

            this._activeRequestByCid[cid] = startResult.request_id;
            delete this._cashierCancelledByCid[cid];
            if (startResult.tip_enabled) {
                Gui.showPopup('ConfirmPopup', {
                    title: _t('CardPointe POC'),
                    body: _t('Select tip on terminal…'),
                    confirmText: _t('OK'),
                    cancelText: _t('Close'),
                });
            }
            let result;
            try {
                result = await rpc.query({
                    route: '/pos_cardpointe_poc/auth',
                    params: { request_id: startResult.request_id },
                }, { shadow: true, timeout: 150000 });
            } catch (_error) {
                this._showError(_t('Could not reach Odoo server during terminal payment.'));
                line.set_payment_status('retry');
                delete this._activeRequestByCid[cid];
                return false;
            }
            delete this._activeRequestByCid[cid];

            if (result.status === 'approved') {
                const approvedAmount = this._normalizeAmount(result.amount, line.amount);
                line.set_amount(approvedAmount);
                line.cardpointe_retref = result.retref || '';
                line.cardpointe_authcode = result.authcode || '';
                line.cardpointe_respcode = result.respcode || '';
                line.cardpointe_resptext = result.resptext || '';
                line.cardpointe_token = result.token || '';
                line.cardpointe_status = 'approved';
                line.cardpointe_operation = 'sale';
                line.cardpointe_ok = true;
                line.cardpointe_signature_required = !!result.signature_required;
                line.cardpointe_signature_captured = !!result.signature_captured;
                line.cardpointe_signature_method = result.signature_method || '';
                line.cardpointe_tip_amount = result.cardpointe_tip_amount || 0.0;
                line.cardpointe_base_amount = result.cardpointe_base_amount || line.amount;
                line.transaction_id = result.retref || '';
                line.set_payment_status('done');
                return true;
            }

            if (result.status === 'cancelled' && this._cashierCancelledByCid[cid]) {
                line.cardpointe_status = 'cancelled';
                line.cardpointe_respcode = result.respcode || '';
                line.cardpointe_resptext = result.resptext || '';
                line.cardpointe_tip_amount = 0.0;
                line.cardpointe_base_amount = 0.0;
                line.set_payment_status('retry');
                delete this._cashierCancelledByCid[cid];
                return false;
            }

            this._handleFailedResult(line, result);
            delete this._cashierCancelledByCid[cid];
            return false;
        },

        _send_refund_request: async function (order, line) {
            const refundedOrderLineIds = order
                .get_orderlines()
                .filter((orderLine) => orderLine.refunded_orderline_id)
                .map((orderLine) => orderLine.refunded_orderline_id);
            if (!refundedOrderLineIds.length) {
                this._showError(_t('Refund must be started from a paid ticket so original CardPointe payment can be located.'));
                line.set_payment_status('retry');
                return false;
            }

            line.set_payment_status('waiting');
            let result;
            try {
                result = await rpc.query({
                    route: '/pos_cardpointe_poc/refund',
                    params: {
                        payment_method_id: line.payment_method.id,
                        amount: line.amount,
                        refunded_orderline_ids: refundedOrderLineIds,
                    },
                }, { shadow: true, timeout: 90000 });
            } catch (_error) {
                this._showError(_t('Could not reach Odoo server during CardPointe refund.'));
                line.set_payment_status('retry');
                return false;
            }

            if (result.status !== 'approved') {
                this._handleFailedResult(line, result);
                return false;
            }

            line.cardpointe_retref = result.retref || '';
            line.cardpointe_original_retref = result.original_retref || '';
            line.cardpointe_respcode = result.respcode || '';
            line.cardpointe_resptext = result.resptext || '';
            line.cardpointe_status = 'approved';
            line.cardpointe_operation = result.operation || 'refund';
            line.cardpointe_ok = !!result.ok;
            line.transaction_id = result.retref || '';
            line.set_payment_status('done');
            return true;
        },

        send_payment_cancel: async function (order, cid) {
            this._super.apply(this, arguments);
            const line = order.paymentlines.find((paymentLine) => paymentLine.cid === cid);
            if (!line) {
                return false;
            }

            const requestId = this._activeRequestByCid[cid];
            if (!requestId) {
                line.set_payment_status('retry');
                delete this._cashierCancelledByCid[cid];
                return true;
            }

            this._cashierCancelledByCid[cid] = true;

            let result;
            try {
                result = await rpc.query({
                    route: '/pos_cardpointe_poc/cancel',
                    params: { request_id: requestId },
                }, { shadow: true, timeout: 30000 });
            } catch (_error) {
                this._showError(_t('Could not reach Odoo server to cancel terminal payment.'));
                line.set_payment_status('retry');
                delete this._cashierCancelledByCid[cid];
                return false;
            }

            delete this._activeRequestByCid[cid];
            line.cardpointe_status = result.status || 'error';
            line.cardpointe_ok = false;
            line.cardpointe_signature_required = false;
            line.cardpointe_signature_captured = false;
            line.cardpointe_signature_method = '';
            line.cardpointe_tip_amount = 0.0;
            line.cardpointe_base_amount = 0.0;
            line.cardpointe_respcode = result.respcode || '';
            line.cardpointe_resptext = result.resptext || '';
            line.set_payment_status('retry');

            if (result.status !== 'cancelled') {
                this._showError(result.message || _t('Cancel request was not accepted by terminal.'));
                delete this._cashierCancelledByCid[cid];
                return false;
            }
            return true;
        },

        _handleFailedResult: function (line, result) {
            line.cardpointe_status = result.status || 'error';
            line.cardpointe_ok = false;
            line.cardpointe_signature_required = false;
            line.cardpointe_signature_captured = false;
            line.cardpointe_signature_method = '';
            line.cardpointe_tip_amount = 0.0;
            line.cardpointe_base_amount = 0.0;
            line.cardpointe_respcode = result.respcode || '';
            line.cardpointe_resptext = result.resptext || '';
            line.set_payment_status('retry');

            if (result.status === 'merchant_mode') {
                this._showError(_t('Terminal is in Merchant Mode. Open the CardPointe Integrated/Bolt app or switch terminal to Integrated mode.'));
            } else if (result.status === 'cancelled') {
                this._showError(_t('Payment cancelled on terminal.'));
            } else if (result.status === 'timeout') {
                this._showError(_t('Terminal request timed out. Please check device status and try again.'));
            } else if (result.status === 'in_use') {
                this._showError(_t('Terminal is in use, retry in a few seconds.'));
            } else {
                this._showError(result.message || _t('Card payment not approved.'));
            }
        },

        _normalizeAmount: function (amount, fallback) {
            if (amount === undefined || amount === null || amount === '') {
                return fallback;
            }
            const value = String(amount);
            if (value.indexOf('.') !== -1) {
                return parseFloat(value);
            }
            return parseFloat(value) / 100;
        },

        _showError: function (message) {
            Gui.showPopup('ErrorPopup', {
                title: _t('CardPointe POC'),
                body: message,
            });
        },
    });

    models.register_payment_method('cardpointe_poc', CardPointePOCTipping);

    const CardPointePaymentTipping = (Payment) => class extends Payment {
        init_from_JSON(json) {
            super.init_from_JSON(json);
            this.cardpointe_tip_amount = json.cardpointe_tip_amount || 0.0;
            this.cardpointe_base_amount = json.cardpointe_base_amount || 0.0;
        }

        export_as_JSON() {
            const json = super.export_as_JSON();
            json.cardpointe_tip_amount = this.cardpointe_tip_amount || 0.0;
            json.cardpointe_base_amount = this.cardpointe_base_amount || 0.0;
            return json;
        }
    };

    Registries.Model.extend(models.Payment, CardPointePaymentTipping);
});
