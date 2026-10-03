import importlib.util
import pathlib
import unittest
from decimal import Decimal

MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / 'services' / 'terminal_recovery.py'
spec = importlib.util.spec_from_file_location('terminal_recovery', MODULE_PATH)
terminal_recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(terminal_recovery)


class _Gateway:
    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def inquire_by_orderid(self, orderid, merchid):
        self.calls.append((orderid, merchid))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def resolve(answer, amount='42.50'):
    return terminal_recovery.resolve_terminal_outcome(_Gateway(answer), 'MID', 'ORDER1', amount)


class TestTerminalRecovery(unittest.TestCase):
    def test_order_id_is_alphanumeric_and_at_most_19(self):
        order_id = terminal_recovery.new_terminal_order_id()
        self.assertRegex(order_id, r'^[A-Z0-9]{19}$')
        self.assertNotEqual(order_id, terminal_recovery.new_terminal_order_id())

    def test_gateway_amounts_with_and_without_decimal(self):
        parse = terminal_recovery.parse_gateway_amount
        self.assertEqual(parse('10.50'), Decimal('10.50'))
        self.assertEqual(parse('1050'), Decimal('10.50'))  # minor units
        self.assertIsNone(parse(''))
        self.assertIsNone(parse('abc'))
        self.assertIsNone(parse(True))

    def test_approved_transaction_is_recovered(self):
        result = resolve({'ok': True, 'data': {'respstat': 'A', 'retref': 'R1', 'amount': '42.50'}})
        self.assertEqual(result['status'], 'approved')
        self.assertTrue(result['amount_matches'])

    def test_approved_amount_difference_is_reported(self):
        result = resolve({'ok': True, 'data': {'respstat': 'A', 'retref': 'R1', 'amount': '40.00'}})
        self.assertEqual(result['status'], 'approved')
        self.assertFalse(result['amount_matches'])

    def test_not_found_is_definitive(self):
        result = resolve({'ok': True, 'data': {'respstat': 'C', 'respcode': '29', 'resptext': 'Txn not found'}})
        self.assertEqual(result['status'], 'not_found')

    def test_not_found_on_http_error_is_definitive(self):
        result = resolve({'ok': False, 'http_status': 400,
                          'data': {'respcode': '29', 'resptext': 'Txn not found'}})
        self.assertEqual(result['status'], 'not_found')

    def test_voided_authorization_leaves_no_charge(self):
        result = resolve({'ok': True, 'data': {'respstat': 'A', 'retref': 'R1', 'amount': '42.50',
                                               'setlstat': 'Voided'}})
        self.assertEqual(result['status'], 'voided')

    def test_decline_is_reported_as_decline(self):
        result = resolve({'ok': True, 'data': {'respstat': 'C', 'respcode': '05', 'resptext': 'Do not honor'}})
        self.assertEqual(result['status'], 'declined')

    def test_transport_failure_stays_unknown(self):
        self.assertEqual(resolve({'ok': False, 'http_status': None, 'data': {}})['status'], 'unknown')
        self.assertEqual(resolve(RuntimeError('boom'))['status'], 'unknown')

    def test_empty_or_ambiguous_answers_stay_unknown(self):
        self.assertEqual(resolve({'ok': True, 'data': {}})['status'], 'unknown')
        two = [{'respstat': 'A', 'retref': 'R1', 'amount': '42.50'},
               {'respstat': 'A', 'retref': 'R2', 'amount': '42.50'}]
        self.assertEqual(resolve({'ok': True, 'data': two})['status'], 'unknown')

    def test_approval_without_reference_stays_unknown(self):
        self.assertEqual(resolve({'ok': True, 'data': {'respstat': 'A', 'amount': '42.50'}})['status'], 'unknown')


if __name__ == '__main__':
    unittest.main()
