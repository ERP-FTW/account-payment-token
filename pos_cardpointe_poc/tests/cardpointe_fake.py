"""In-process stand-in for the CardPointe terminal and Gateway HTTP APIs.

Tests patch the single transport function every client uses (`request_json`) so they observe the
exact requests Odoo would send. Nothing here reaches the network, so a passing test proves Odoo's
behaviour, never the processor's.
"""
from contextlib import ExitStack
from unittest.mock import patch

from odoo.addons.payment_cardpointe_base.services.http import CardPointeRequestError

TRANSPORT_TARGETS = (
    'odoo.addons.payment_cardpointe_base.services.gateway.request_json',
    'odoo.addons.pos_cardpointe_poc.services.cardpointe_terminal.request_json',
    'odoo.addons.payment_cardpointe_base.models.cardpointe_client.request_json',
)


class FakeCardPointe:
    def __init__(self):
        self.calls = []
        self.auth_card = {'respstat': 'A', 'respcode': '000', 'resptext': 'Approval',
                          'retref': 'TERM-RETREF-1', 'authcode': 'A1B2C3', 'amount': '42.50'}
        self.auth_card_error = None          # e.g. CardPointeRequestError('timeout')
        self.inquire_by_orderid = None       # dict/list answer, or an exception instance
        self.inquire = {}
        self.inquire_retref_from_path = False  # answer each inquire/<retref> with that retref
        self.void = {'respstat': 'A', 'respcode': '000', 'resptext': 'Approval', 'authcode': 'REVERS'}
        self.refund = {'respstat': 'A', 'respcode': '000', 'resptext': 'Approval', 'retref': 'REFUND-1'}
        self.refund_error = None
        self.refund_answers = []             # consumed in order before falling back to `refund`

    def __call__(self, method, url, headers=None, json=None, timeout=30, verify=True, auth=None):
        path = url.split('://', 1)[-1].split('/', 1)[-1]
        self.calls.append({'method': method, 'path': path, 'json': json, 'headers': dict(headers or {})})
        if path.endswith('v2/connect'):
            return 200, {'X-CardConnect-SessionKey': 'SESSION-1;expires=600'}, '', {}
        if path.endswith('v2/disconnect') or path.endswith('v2/cancel'):
            return 200, {}, '', {}
        if path.endswith('v4/authCard'):
            if self.auth_card_error:
                raise self.auth_card_error
            return 200, {}, '', dict(self.auth_card)
        if '/inquireByOrderid/' in url:
            answer = self.inquire_by_orderid
            if isinstance(answer, Exception):
                raise answer
            if answer is None:
                return 200, {}, '', {'respstat': 'C', 'respcode': '29', 'respproc': 'PPS',
                                     'resptext': 'Txn not found'}
            return 200, {}, '', answer
        if '/inquire/' in url:
            answer = dict(self.inquire)
            if self.inquire_retref_from_path:
                answer['retref'] = url.split('/inquire/', 1)[1].split('/', 1)[0]
            return 200, {}, '', answer
        if path.endswith('rest/void'):
            return 200, {}, '', dict(self.void)
        if path.endswith('rest/refund'):
            if self.refund_error:
                raise self.refund_error
            if self.refund_answers:
                return 200, {}, '', dict(self.refund_answers.pop(0))
            return 200, {}, '', dict(self.refund)
        if path.endswith('cardconnect/rest/') and method == 'PUT':
            return 200, {}, '<html><body><h1>CardConnect REST Servlet</h1></body></html>', None
        return 404, {}, 'not handled', {}

    def paths(self, fragment):
        return [c for c in self.calls if fragment in c['path']]

    def patch_transport(self, testcase):
        stack = ExitStack()
        for target in TRANSPORT_TARGETS:
            stack.enter_context(patch(target, side_effect=self))
        testcase.addCleanup(stack.close)


TIMEOUT = CardPointeRequestError('timeout')
