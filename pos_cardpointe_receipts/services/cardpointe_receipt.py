import logging

_logger = logging.getLogger(__name__)


class CardPointeTerminalReceiptService:
    """Scaffold service for terminal-side CardPointe receipt reprint calls."""

    def __init__(self, terminal_config):
        self.config = terminal_config

    def reprint_receipt(self, retref, order_id=None):
        _logger.info(
            "CardPointe receipt reprint requested device_type=%s hsn=%s retref_present=%s order_id=%s",
            self.config.device_type,
            self.config.device_serial,
            bool(retref),
            order_id,
        )

        # TODO(cardpointe-receipts): Integrate the concrete CardPointe/Bolt endpoint for
        # terminal receipt reprint once endpoint details (path, auth headers, payload, and
        # success/error response mapping) are finalized and documented for this integration.
        return {
            'ok': False,
            'status': 'not_implemented',
            'message': 'Terminal receipt reprint endpoint is not implemented yet.',
        }
