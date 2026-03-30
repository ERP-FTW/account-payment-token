# -*- coding: utf-8 -*-
import logging

from odoo.addons.pos_cardpointe_poc.services.cardpointe_terminal import CardPointeTerminalClient

_logger = logging.getLogger(__name__)


class CardPointeTerminalReceiptService(CardPointeTerminalClient):
    REPRINT_PATH = "/v3/printReceipt"

    def reprint_receipt(
        self,
        order_id,
        auth_merchant_id=None,
        print_extra_receipt=False,
        print_delay=None,
    ):
        order_id = (order_id or "").strip()
        if not order_id:
            return {
                "ok": False,
                "status": "invalid_request",
                "message": "Missing CardPointe orderId for receipt reprint.",
            }

        connect_result = self.connect()
        if not connect_result.get("ok"):
            return {
                "ok": False,
                "status": connect_result.get("status", "error"),
                "message": connect_result.get("message", "Unable to connect terminal session."),
            }

        session_key = connect_result.get("session_key")
        disconnect_warning = ""

        try:
            payload = {
                "merchantId": self.config.merchant_id,
                "hsn": self.config.device_serial,
                "orderId": order_id,
            }
            if auth_merchant_id:
                payload["authMerchantId"] = auth_merchant_id
            if print_extra_receipt:
                payload["printExtraReceipt"] = True
                if print_delay is None:
                    return {
                        "ok": False,
                        "status": "invalid_request",
                        "message": "printDelay is required when printExtraReceipt is true.",
                    }
                payload["printDelay"] = str(print_delay)

            _logger.info(
                "CardPointe receipt reprint requested device_type=%s hsn=%s order_id=%s print_extra_receipt=%s",
                self.config.device_type,
                self.config.device_serial,
                order_id,
                bool(print_extra_receipt),
            )

            result = self._request(
                "POST",
                self.REPRINT_PATH,
                payload=payload,
                session_key=session_key,
                timeout=max(20, self.config.request_timeout_seconds or 120),
            )

            if not result.get("ok"):
                return {
                    "ok": False,
                    "status": result.get("status", "error"),
                    "message": result.get("message", "Receipt reprint request failed."),
                    "raw": result.get("data") or {},
                }

            http_status = result.get("http_status")
            data = dict(result.get("data") or {})
            error_code = str(data.get("errorCode") or "").strip()
            error_message = data.get("errorMessage") or ""
            receipt_data = data.get("receiptData") or {}

            ok = http_status == 200 and not error_code

            _logger.info(
                "CardPointe receipt reprint mapped ok=%s http_status=%s error_code=%s order_id=%s",
                ok,
                http_status,
                error_code,
                order_id,
            )

            return {
                "ok": ok,
                "status": "printed" if ok else "error",
                "message": error_message or "Receipt print request sent to terminal.",
                "error_code": error_code,
                "receipt_data": receipt_data,
                "raw": data,
            }

        finally:
            if session_key:
                disc = self.disconnect(session_key)
                if not disc.get("ok"):
                    disconnect_warning = " (disconnect failed after receipt print request)"
                    _logger.warning(
                        "CardPointe receipt reprint disconnect failed hsn=%s message=%s",
                        self.config.device_serial,
                        disc.get('message'),
                    )