"""A stand-in for autoproton.net.Net that serves canned responses."""

from __future__ import annotations

import io
import json

from autoproton.net import Cancelled, HttpError, NetError


class FakeNet:
    def __init__(self, routes=None):
        self.routes = dict(routes or {})
        self.calls: list[str] = []

    def _route(self, url):
        self.calls.append(url)
        if url not in self.routes:
            raise HttpError(404, url)
        r = self.routes[url]
        if callable(r):
            r = r()
        if isinstance(r, Exception):
            raise r
        return r

    def get_json(self, url, headers=None):
        r = self._route(url)
        if isinstance(r, tuple):
            status, body = r
            if status >= 400:
                raise HttpError(status, url)
            return body
        return r

    def get(self, url, headers=None):
        r = self._route(url)
        body = r if isinstance(r, bytes) else json.dumps(r).encode()

        class Resp:
            status = 200
            headers = {}

        resp = Resp()
        resp.body = body
        return resp

    def download(self, url, out, progress=None, cancel=None, chunk=1 << 16):
        data = self._route(url)
        if not isinstance(data, bytes):
            raise NetError("not bytes")
        src = io.BytesIO(data)
        done = 0
        while True:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            block = src.read(chunk)
            if not block:
                break
            out.write(block)
            done += len(block)
            if progress:
                progress(done, len(data))
        return done
