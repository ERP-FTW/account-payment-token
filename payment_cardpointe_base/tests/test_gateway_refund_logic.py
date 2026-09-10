import importlib.util
import os
import sys
import types
import unittest

CURRENT_DIR = os.path.dirname(__file__)
BASE_DIR = os.path.abspath(os.path.join(CURRENT_DIR, '..'))
SERVICES_DIR = os.path.join(BASE_DIR, 'services')


def _load_services_module(module_name, filename):
    full_name = f'payment_cardpointe_base.services.{module_name}'
    path = os.path.join(SERVICES_DIR, filename)
    spec = importlib.util.spec_from_file_location(full_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    spec.loader.exec_module(module)
    return module


if 'payment_cardpointe_base' not in sys.modules:
    pkg = types.ModuleType('payment_cardpointe_base')
    pkg.__path__ = [BASE_DIR]
    sys.modules['payment_cardpointe_base'] = pkg
if 'payment_cardpointe_base.services' not in sys.modules:
    pkg = types.ModuleType('payment_cardpointe_base.services')
    pkg.__path__ = [SERVICES_DIR]
    sys.modules['payment_cardpointe_base.services'] = pkg

_load_services_module('http', 'http.py')
_load_services_module('money', 'money.py')
gateway = _load_services_module('gateway', 'gateway.py')
refunds = _load_services_module('refunds', 'refunds.py')


class _GatewayStub:
    def __init__(self, inquire, refund=None, void=None):
        self.inquire_response = {'ok': True, 'data': inquire}
        self.refund_response = refund or {'ok': True, 'data': {'respstat': 'A', 'respcode': '000', 'resptext': 'Approval'}}
        self.void_response = void or {'ok': True, 'data': {'respstat': 'A', 'respcode': '000', 'resptext': 'Approval'}}
        self.calls = []

    def inquire(self, retref, merchid):
        self.calls.append(('inquire', retref, merchid))
        return self.inquire_response

    def refund(self, merchid, retref, amount):
        self.calls.append(('refund', merchid, retref, amount))
        return self.refund_response

    def void(self, merchid, retref):
        self.calls.append(('void', merchid, retref))
        return self.void_response


class TestGatewayRefundDecision(unittest.TestCase):
    def test_full_unsettled_refund_uses_void(self):
        client = _GatewayStub({'retref': 'r', 'amount': '1.00', 'setlstat': 'Queued', 'voidable': 'Y'})
        result = refunds.execute_void_or_refund(client, 'mid', 'r', '1.00')
        self.assertTrue(result['ok'])
        self.assertEqual(result['operation'], 'void')
        self.assertEqual([call[0] for call in client.calls], ['inquire', 'void'])

    def test_partial_unsettled_refund_uses_exact_refund_without_void(self):
        client = _GatewayStub({'retref': 'r', 'amount': '10.00', 'setlstat': 'Queued', 'voidable': 'Y'})
        result = refunds.execute_void_or_refund(client, 'mid', 'r', '1.25')
        self.assertTrue(result['ok'])
        self.assertEqual(result['operation'], 'refund')
        self.assertEqual(client.calls[1], ('refund', 'mid', 'r', '1.25'))
        self.assertNotIn('void', [call[0] for call in client.calls])

    def test_accepted_nonvoidable_uses_refund(self):
        client = _GatewayStub({'retref': 'r', 'amount': '10.00', 'setlstat': 'Accepted', 'voidable': 'N', 'refundable': 'Y'})
        result = refunds.execute_void_or_refund(client, 'mid', 'r', '10.00')
        self.assertTrue(result['ok'])
        self.assertEqual(result['operation'], 'refund')
        self.assertEqual([call[0] for call in client.calls], ['inquire', 'refund'])

    def test_failed_inquiry_does_not_mutate(self):
        client = _GatewayStub({}, refund={'ok': True, 'data': {'respstat': 'A', 'respcode': '000'}})
        client.inquire_response = {'ok': False, 'message': '401 Unauthorized', 'data': {}}
        result = refunds.execute_void_or_refund(client, 'mid', 'r', '1.00')
        self.assertFalse(result['ok'])
        self.assertEqual(result['message'], '401 Unauthorized')
        self.assertEqual([call[0] for call in client.calls], ['inquire'])

    def test_successful_inquiry_with_empty_transaction_does_not_mutate(self):
        client = _GatewayStub({})
        result = refunds.execute_void_or_refund(client, 'mid', 'r', '1.00')
        self.assertFalse(result['ok'])
        self.assertIn('transaction', result['message'].lower())
        self.assertEqual([call[0] for call in client.calls], ['inquire'])

    def test_inquiry_transaction_must_match_requested_reference(self):
        for transaction in ({'amount': '1.00'}, {'retref': 'other', 'amount': '1.00'}):
            with self.subTest(transaction=transaction):
                client = _GatewayStub(transaction)
                result = refunds.execute_void_or_refund(client, 'mid', 'r', '1.00')
                self.assertFalse(result['ok'])
                self.assertIn('reference', result['message'].lower())
                self.assertEqual([call[0] for call in client.calls], ['inquire'])

    def test_malformed_inquiry_transaction_does_not_mutate(self):
        client = _GatewayStub({'retref': ['r'], 'amount': '1.00'})
        result = refunds.execute_void_or_refund(client, 'mid', 'r', '1.00')
        self.assertFalse(result['ok'])
        self.assertIn('reference', result['message'].lower())
        self.assertEqual([call[0] for call in client.calls], ['inquire'])

    def test_malformed_success_envelope_cannot_look_approved(self):
        client = _GatewayStub({})
        client.inquire_response = {
            'ok': True,
            'data': {'retref': ['r'], 'respstat': 'A', 'respcode': '000'},
        }
        result = refunds.execute_void_or_refund(client, 'mid', 'r', '1.00')
        self.assertFalse(result['ok'])
        self.assertIn('reference', result['message'].lower())
        self.assertEqual([call[0] for call in client.calls], ['inquire'])

    def test_failed_envelope_cannot_look_approved_from_nested_data(self):
        client = _GatewayStub({})
        client.inquire_response = {
            'ok': False,
            'message': '401 Unauthorized',
            'data': {'retref': 'r', 'respstat': 'A', 'respcode': '000'},
        }
        result = refunds.execute_void_or_refund(client, 'mid', 'r', '1.00')
        self.assertFalse(result['ok'])
        self.assertEqual(result['message'], '401 Unauthorized')
        self.assertEqual([call[0] for call in client.calls], ['inquire'])

    def test_malformed_inquiry_status_fields_do_not_mutate(self):
        for field, value in (('setlstat', {'state': 'Accepted'}), ('respstat', ['D'])):
            with self.subTest(field=field):
                transaction = {
                    'retref': 'r', 'amount': '1.00', 'voidable': 'Y',
                    'refundable': 'Y', field: value,
                }
                client = _GatewayStub(transaction)
                result = refunds.execute_void_or_refund(client, 'mid', 'r', '1.00')
                self.assertFalse(result['ok'])
                self.assertIn('malformed', result['message'].lower())
                self.assertEqual(result['operation'], 'inquire')
                self.assertEqual([call[0] for call in client.calls], ['inquire'])

    def test_inquiry_transport_failure_does_not_mutate(self):
        class FailingInquiryClient(_GatewayStub):
            def inquire(self, retref, merchid):
                self.calls.append(('inquire', retref, merchid))
                raise TimeoutError('gateway unavailable')

        client = FailingInquiryClient({})
        result = refunds.execute_void_or_refund(client, 'mid', 'r', '1.00')
        self.assertFalse(result['ok'])
        self.assertIn('inquiry failed', result['message'].lower())
        self.assertEqual([call[0] for call in client.calls], ['inquire'])

    def test_explicit_decline_dominates_apparent_success(self):
        client = _GatewayStub({'retref': 'r', 'amount': '1.00', 'setlstat': 'Queued', 'respstat': 'D', 'respcode': '000'})
        result = refunds.execute_void_or_refund(client, 'mid', 'r', '1.00')
        self.assertFalse(result['ok'])
        self.assertEqual([call[0] for call in client.calls], ['inquire'])

    def test_missing_or_invalid_original_amount_never_voids(self):
        for original in (None, 'not-money', '0.00', '-1.00'):
            with self.subTest(original=original):
                client = _GatewayStub({'retref': 'r', 'amount': original, 'setlstat': 'Queued', 'voidable': 'Y'})
                result = refunds.execute_void_or_refund(client, 'mid', 'r', '1.00')
                self.assertEqual(result['operation'], 'refund')
                self.assertEqual([call[0] for call in client.calls], ['inquire', 'refund'])

    def test_partial_code_28_does_not_fall_back_to_void(self):
        client = _GatewayStub(
            {'retref': 'r', 'amount': '10.00', 'setlstat': 'Accepted', 'refundable': 'Y'},
            refund={'ok': True, 'data': {'respstat': 'D', 'respcode': '28', 'resptext': 'Txn not settled'}},
        )
        result = refunds.execute_void_or_refund(client, 'mid', 'r', '1.00')
        self.assertFalse(result['ok'])
        self.assertEqual([call[0] for call in client.calls], ['inquire', 'refund'])

    def test_full_refund_code_28_can_fallback_to_void(self):
        client = _GatewayStub(
            {'retref': 'r', 'amount': '1.00', 'setlstat': 'Accepted', 'voidable': 'Y', 'refundable': 'Y'},
            refund={'ok': True, 'data': {'respstat': 'D', 'respcode': '28', 'resptext': 'Txn not settled'}},
        )
        result = refunds.execute_void_or_refund(client, 'mid', 'r', '1.00')
        self.assertTrue(result['ok'])
        self.assertEqual(result['operation'], 'void')
        self.assertEqual(client.calls[1], ('refund', 'mid', 'r', '1.00'))
        self.assertEqual([call[0] for call in client.calls], ['inquire', 'refund', 'void'])

    def test_full_refund_code_28_does_not_fallback_when_voiding_is_forbidden(self):
        client = _GatewayStub(
            {'retref': 'r', 'amount': '1.00', 'setlstat': 'Accepted', 'voidable': 'N', 'refundable': 'Y'},
            refund={'ok': True, 'data': {'respstat': 'D', 'respcode': '28', 'resptext': 'Txn not settled'}},
        )
        result = refunds.execute_void_or_refund(client, 'mid', 'r', '1.00')
        self.assertFalse(result['ok'])
        self.assertEqual(result['operation'], 'refund')
        self.assertEqual([call[0] for call in client.calls], ['inquire', 'refund'])

    def test_failed_full_refund_code_28_envelope_does_not_authorize_void(self):
        client = _GatewayStub(
            {'retref': 'r', 'amount': '1.00', 'setlstat': 'Accepted', 'voidable': 'Y', 'refundable': 'Y'},
            refund={'ok': False, 'message': 'HTTP 503', 'data': {
                'respstat': 'D', 'respcode': '28', 'resptext': 'Txn not settled',
            }},
        )
        result = refunds.execute_void_or_refund(client, 'mid', 'r', '1.00')
        self.assertFalse(result['ok'])
        self.assertEqual(result['message'], 'HTTP 503')
        self.assertEqual(result['operation'], 'refund')
        self.assertEqual([call[0] for call in client.calls], ['inquire', 'refund'])

    def test_void_not_settled_does_not_fallback_to_refund(self):
        client = _GatewayStub(
            {'retref': 'r', 'amount': '1.00', 'setlstat': 'Queued', 'voidable': 'Y', 'refundable': 'Y'},
            void={'ok': True, 'data': {'respstat': 'D', 'respcode': '28', 'resptext': 'Txn not settled'}},
        )
        result = refunds.execute_void_or_refund(client, 'mid', 'r', '1.00')
        self.assertFalse(result['ok'])
        self.assertEqual(result['operation'], 'void')
        self.assertEqual([call[0] for call in client.calls], ['inquire', 'void'])

    def test_full_void_failure_can_fallback_to_refund_when_settled(self):
        client = _GatewayStub(
            {'retref': 'r', 'amount': '1.00', 'setlstat': 'Queued', 'voidable': 'Y', 'refundable': 'Y'},
            void={'ok': True, 'data': {'respstat': 'D', 'respcode': '12', 'resptext': 'Already settled'}},
        )
        result = refunds.execute_void_or_refund(client, 'mid', 'r', '1.00')
        self.assertTrue(result['ok'])
        self.assertEqual(result['operation'], 'refund')
        self.assertEqual([call[0] for call in client.calls], ['inquire', 'void', 'refund'])

    def test_failed_void_envelope_never_authorizes_refund_fallback(self):
        client = _GatewayStub(
            {'retref': 'r', 'amount': '1.00', 'setlstat': 'Queued', 'voidable': 'Y', 'refundable': 'Y'},
            void={'ok': False, 'message': 'HTTP 503', 'data': {
                'respstat': 'D', 'respcode': '12', 'resptext': 'Already settled',
            }},
        )
        result = refunds.execute_void_or_refund(client, 'mid', 'r', '1.00')
        self.assertFalse(result['ok'])
        self.assertEqual(result['message'], 'HTTP 503')
        self.assertEqual(result['operation'], 'void')
        self.assertEqual([call[0] for call in client.calls], ['inquire', 'void'])

    def test_approved_full_refund_is_terminal_even_with_not_settled_text(self):
        client = _GatewayStub(
            {'retref': 'r', 'amount': '1.00', 'setlstat': 'Accepted', 'voidable': 'Y', 'refundable': 'Y'},
            refund={'ok': True, 'data': {
                'respstat': 'A', 'respcode': '000',
                'resptext': 'Approved; transaction not settled',
            }},
        )
        result = refunds.execute_void_or_refund(client, 'mid', 'r', '1.00')
        self.assertTrue(result['ok'])
        self.assertEqual(result['operation'], 'refund')
        self.assertEqual([call[0] for call in client.calls], ['inquire', 'refund'])

    def test_already_voided_is_not_reported_as_new_success(self):
        client = _GatewayStub({'retref': 'r', 'setlstat': 'Voided'})
        result = refunds.execute_void_or_refund(client, 'mid', 'r', '1.00')
        self.assertFalse(result['ok'])
        self.assertEqual([call[0] for call in client.calls], ['inquire'])

    def test_gateway_logging_sanitizes_signature_and_receipt(self):
        payload = {'signature': 'abcdef', 'receipt': 'huge-text', 'emvTagData': 'XYZ', 'userfields': '{"receipt":"Y"}', 'resptext': 'A' * 500}
        sanitized = gateway.sanitize_for_log(payload)
        self.assertNotIn('signature', sanitized)
        self.assertNotIn('receipt', sanitized)
        self.assertNotIn('emvTagData', sanitized)
        self.assertNotIn('userfields', sanitized)
        self.assertEqual(len(sanitized['resptext']), 303)


if __name__ == '__main__':
    unittest.main()
