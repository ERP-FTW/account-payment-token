import importlib.util
import pathlib
import unittest

MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / 'services' / 'signature_policy.py'
spec = importlib.util.spec_from_file_location('signature_policy', MODULE_PATH)
signature_policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(signature_policy)


class TestSignaturePolicy(unittest.TestCase):
    def test_over_threshold_true(self):
        self.assertTrue(signature_policy.amount_meets_threshold('1.02', '1.00'))

    def test_over_threshold_false(self):
        self.assertFalse(signature_policy.amount_meets_threshold('0.51', '1.00'))

    def test_on_policy_signature_true(self):
        emv = signature_policy.parse_emv_tag_data('{"Signature":"true","PIN":"None"}')
        self.assertTrue(signature_policy.emv_indicates_signature_applicable(emv))

    def test_on_policy_signature_false(self):
        emv = signature_policy.parse_emv_tag_data('{"Signature":"false","PIN":"None"}')
        self.assertFalse(signature_policy.emv_indicates_signature_applicable(emv))

    def test_on_policy_invalid_json(self):
        emv = signature_policy.parse_emv_tag_data('{invalid')
        self.assertFalse(signature_policy.emv_indicates_signature_applicable(emv))


if __name__ == '__main__':
    unittest.main()
