from unittest.mock import patch

from requests import Response

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestCardPointeConnection(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.merchant = cls.env['cardpointe.merchant.config'].create({
            'name': 'Test merchant',
            'mid': 'test-mid',
            'gateway_base_url': 'https://gateway.invalid/cardconnect/rest/',
            'gateway_username': 'test-user',
            'gateway_password': 'test-password',
        })
        cls.provider = cls.env.ref('payment_cardpointe.payment_provider_cardpointe')
        cls.provider.write({'state': 'test', 'cardpointe_merchant_config_id': cls.merchant.id})

    @staticmethod
    def response(status, body):
        response = Response()
        response.status_code = status
        response._content = body.encode()
        return response

    def test_both_connection_buttons_accept_the_gateway_banner(self):
        def gateway(method, url, headers, json, auth, **kwargs):
            self.assertEqual((method, url), ('PUT', self.merchant.gateway_base_url))
            self.assertEqual(auth, ('test-user', 'test-password'))
            self.assertEqual(json, {'merchid': 'test-mid'})
            self.assertEqual(headers['Content-Type'], 'application/json')
            if headers['Accept'] != '*/*':
                return self.response(406, '{"error":"Not Acceptable"}')
            return self.response(200, '<html><h1>CardConnect REST Servlet</h1></html>')

        for action in (self.merchant.action_test_credentials, self.provider.action_cardpointe_test_connection):
            with self.subTest(action=action.__name__), patch('requests.request', side_effect=gateway) as send:
                result = action()['params']
                self.assertEqual(result['type'], 'success', result['message'])
                send.assert_called_once()

    def test_provider_rejects_wrong_banner_and_bad_credentials(self):
        for status, body in ((200, '<html>Maintenance</html>'), (401, '{"error":"Unauthorized"}')):
            with self.subTest(status=status), patch('requests.request', return_value=self.response(status, body)):
                result = self.provider.action_cardpointe_test_connection()['params']
                self.assertEqual(result['type'], 'danger')
                self.assertIn(str(status), result['message'])

    def test_provider_custom_endpoint_keeps_json_get(self):
        self.provider.cardpointe_test_endpoint = 'custom-check'
        with patch('requests.request', return_value=self.response(200, '{}')) as send:
            self.assertEqual(self.provider.action_cardpointe_test_connection()['params']['type'], 'success')
        send.assert_called_once()
        args = send.call_args.kwargs
        self.assertEqual((args['method'], args['url']), ('GET', self.merchant.gateway_base_url + 'custom-check'))
        self.assertEqual(args['headers']['Accept'], 'application/json')
        self.assertIsNone(args['json'])
