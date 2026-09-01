from .base import BaseClient
from ..debug import register_redactions_from_response
from ..utils import LazyLog

def register_redactions_from_response(x):
    pass

import json


class AsyncClient(BaseClient):

    async def close_async_session(self):
        await self.session.aclose()

    async def _get_request(self, path, params):
        dest = 'https://api.schwabapi.com' + path

        req_num = self._req_num()
        self.logger.debug('Req %s: GET to %s, params=%s',
                req_num, dest, LazyLog(lambda: json.dumps(params, indent=4)))

        resp = await self.session.get(dest, params=params)
        self._log_response(resp, req_num)
        register_redactions_from_response(resp)
        return resp

    # _post_request, _put_request and _delete_request are removed in this fork
    # (2026-09-01, st-c1af) — zero callers, and a generic authenticated write
    # path. See the DEFENSE NOTE in base.py.
