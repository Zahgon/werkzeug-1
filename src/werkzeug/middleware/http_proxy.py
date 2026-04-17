"""
Basic HTTP Proxy
================

.. autoclass:: ProxyMiddleware

:copyright: 2007 Pallets
:license: BSD-3-Clause
"""

from __future__ import annotations

import typing as t
from http import client
from urllib.parse import quote
from urllib.parse import urlsplit

from ..datastructures import EnvironHeaders
from ..http import is_hop_by_hop_header
from ..wsgi import get_input_stream

if t.TYPE_CHECKING:
    from _typeshed.wsgi import StartResponse
    from _typeshed.wsgi import WSGIApplication
    from _typeshed.wsgi import WSGIEnvironment


class ProxyMiddleware:
    """Proxy requests under a path to an external server, routing other
    requests to the app.

    This middleware can only proxy HTTP requests, as HTTP is the only
    protocol handled by the WSGI server. Other protocols, such as
    WebSocket requests, cannot be proxied at this layer. This should
    only be used for development, in production a real proxy server
    should be used.

    The middleware takes a dict mapping a path prefix to a dict
    describing the host to be proxied to::

        app = ProxyMiddleware(app, {
            "/static/": {
                "target": "http://127.0.0.1:5001/",
            }
        })

    Each host has the following options:

    ``target``:
        The target URL to dispatch to. This is required.
    ``remove_prefix``:
        Whether to remove the prefix from the URL before dispatching it
        to the target. The default is ``False``.
    ``host``:
        ``"<auto>"`` (default):
            The host header is automatically rewritten to the URL of the
            target.
        ``None``:
            The host header is unmodified from the client request.
        Any other value:
            The host header is overwritten with the value.
    ``headers``:
        A dictionary of headers to be sent with the request to the
        target. The default is ``{}``.
    ``ssl_context``:
        A :class:`ssl.SSLContext` defining how to verify requests if the
        target is HTTPS. The default is ``None``.

    In the example above, everything under ``"/static/"`` is proxied to
    the server on port 5001. The host header is rewritten to the target,
    and the ``"/static/"`` prefix is removed from the URLs.

    :param app: The WSGI application to wrap.
    :param targets: Proxy target configurations. See description above.
    :param chunk_size: Size of chunks to read from input stream and
        write to target.
    :param timeout: Seconds before an operation to a target fails.

    .. versionadded:: 0.14
    """

    def __init__(
        self,
        app: WSGIApplication,
        targets: t.Mapping[str, dict[str, t.Any]],
        chunk_size: int = 2 << 13,
        timeout: int = 10,
    ) -> None:

        self.app = app
        self.targets = {
            f"/{k.strip('/')}/": _set_defaults(v) for k, v in targets.items()
        }
        self.chunk_size = chunk_size
        self.timeout = timeout


    def __call__(
        self, environ: WSGIEnvironment, start_response: StartResponse
    ) -> t.Iterable[bytes]:
        path = environ["PATH_INFO"]
        app = self.app

        for prefix, opts in self.targets.items():
            if path.startswith(prefix):
                app = self.proxy_to(opts, path, prefix)
                break

        return app(environ, start_response)
