from .base import BaseClient
from ..utils import LazyLog
from ..debug import register_redactions_from_response

def register_redactions_from_response(x):
    pass

import json


class Client(BaseClient):
    def _get_request(self, path, params):
        dest = 'https://api.schwabapi.com' + path

        req_num = self._req_num()
        self.logger.debug('Req %s: GET to %s, params=%s',
                req_num, dest, LazyLog(lambda: json.dumps(params, indent=4)))

        resp = self.session.get(dest, params=params)
        self._log_response(resp, req_num)
        register_redactions_from_response(resp)
        return resp

    # _post_request, _put_request and _delete_request are removed in this fork
    # (2026-09-01, st-c1af). They had zero callers — every surviving method is
    # a read and goes through _get_request — and they were a generic write
    # path: any code holding a Client could have POSTed an order body to the
    # orders endpoint on the authenticated session without touching a removed
    # method name. See the DEFENSE NOTE in base.py.
