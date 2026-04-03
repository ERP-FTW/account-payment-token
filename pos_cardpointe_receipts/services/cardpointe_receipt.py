import logging

from odoo.addons.pos_cardpointe_poc.services.cardpointe_terminal import CardPointeTerminalClient

_logger = logging.getLogger(__name__)


class CardPointeTerminalReceiptService(CardPointeTerminalClient):
    REPRINT_PATH = '/v3/printReceipt'

    def reprint_receipt(self, order_id):
        order_id = (order_id or '').strip()
        if not order_id:
            return {
                'ok': False,
                'status': 'invalid_request',
                'message': 'Missing CardPointe orderId for receipt reprint.',
            }

        connect_result = self.connect()
        if not connect_result.get('ok'):
            return {
                'ok': False,
                'status': connect_result.get('status', 'error'),
                'message': connect_result.get('message', 'Unable to connect terminal session.'),
            }

        session_key = connect_result.get('session_key')
        try:
            payload = {
                'merchantId': self.config.merchant_id,
                'hsn': self.config.device_serial,
                'orderId': order_id,
            }
            _logger.info(
                'CardPointe receipt reprint requested hsn=%s order_id=%s',
                self.config.device_serial,
                order_id,
            )
            result = self._request(
                'POST',
                self.REPRINT_PATH,
                payload=payload,
                session_key=session_key,
                timeout=max(20, self.config.request_timeout_seconds or 120),
            )
            if not result.get('ok'):
                return {
                    'ok': False,
                    'status': result.get('status', 'error'),
                    'message': result.get('message', 'Receipt reprint request failed.'),
                }

            data = dict(result.get('data') or {})
            error_code = str(data.get('errorCode') or '').strip()
            error_message = data.get('errorMessage') or ''
            ok = result.get('http_status') == 200 and not error_code

            _logger.info(
                'CardPointe receipt reprint mapped ok=%s http_status=%s error_code=%s order_id=%s',
                ok,
                result.get('http_status'),
                error_code,
                order_id,
            )

            return {
                'ok': ok,
                'status': 'printed' if ok else 'error',
                'message': error_message or 'Receipt print request sent to terminal.',
                'error_code': error_code,
            }
        finally:
            if session_key:
                disconnect_result = self.disconnect(session_key)
                if not disconnect_result.get('ok'):
                    _logger.warning(
                        'CardPointe receipt reprint disconnect failed hsn=%s message=%s',
                        self.config.device_serial,
                        disconnect_result.get('message'),
                    )
