"""
WSGI Protocol Linter
====================

This module provides a middleware that performs sanity checks on the
behavior of the WSGI server and application. It checks that the
:pep:`3333` WSGI spec is properly implemented. It also warns on some
common HTTP errors such as non-empty responses for 304 status codes.

.. autoclass:: LintMiddleware

:copyright: 2007 Pallets
:license: BSD-3-Clause
"""

from __future__ import annotations

import typing as t
from types import TracebackType
from urllib.parse import urlparse
from warnings import warn

from ..datastructures import Headers
from ..http import is_entity_header
from ..wsgi import FileWrapper

if t.TYPE_CHECKING:
    from _typeshed.wsgi import StartResponse
    from _typeshed.wsgi import WSGIApplication
    from _typeshed.wsgi import WSGIEnvironment


class WSGIWarning(Warning):
    """Warning class for WSGI warnings."""


class HTTPWarning(Warning):
    """Warning class for HTTP warnings."""


def check_type(context: str, obj: object, need: type = str) -> None:
    if type(obj) is not need:
        warn(
            f"{context!r} requires {need.__name__!r}, got {type(obj).__name__!r}.",
            WSGIWarning,
            stacklevel=3,
        )


class InputStream:
    def __init__(self, stream: t.IO[bytes]) -> None:
        self._stream = stream

    def read(self, *args: t.Any) -> bytes:
        if len(args) == 0:
            warn(
                "WSGI does not guarantee an EOF marker on the input stream, thus making"
                " calls to 'wsgi.input.read()' unsafe. Conforming servers may never"
                " return from this call.",
                WSGIWarning,
                stacklevel=2,
            )
        elif len(args) != 1:
            warn(
                "Too many parameters passed to 'wsgi.input.read()'.",
                WSGIWarning,
                stacklevel=2,
            )
        return self._stream.read(*args)

    def readline(self, *args: t.Any) -> bytes:
        if len(args) == 0:
            warn(
                "Calls to 'wsgi.input.readline()' without arguments are unsafe. Use"
                " 'wsgi.input.read()' instead.",
                WSGIWarning,
                stacklevel=2,
            )
        elif len(args) == 1:
            warn(
                "'wsgi.input.readline()' was called with a size hint. WSGI does not"
                " support this, although it's available on all major servers.",
                WSGIWarning,
                stacklevel=2,
            )
        else:
            raise TypeError("Too many arguments passed to 'wsgi.input.readline()'.")
        return self._stream.readline(*args)

    def __iter__(self) -> t.Iterator[bytes]:
        try:
            return iter(self._stream)
        except TypeError:
            warn("'wsgi.input' is not iterable.", WSGIWarning, stacklevel=2)
            return iter(())

    def close(self) -> None:
        warn("The application closed the input stream!", WSGIWarning, stacklevel=2)
        self._stream.close()


class ErrorStream:
    def __init__(self, stream: t.IO[str]) -> None:
        self._stream = stream

    def write(self, s: str) -> None:
        check_type("wsgi.error.write()", s, str)
        self._stream.write(s)

    def flush(self) -> None:
        self._stream.flush()


    def close(self) -> None:
        warn("The application closed the error stream!", WSGIWarning, stacklevel=2)
        self._stream.close()


class GuardedWrite:
    def __init__(self, write: t.Callable[[bytes], object], chunks: list[int]) -> None:
        self._write = write
        self._chunks = chunks

    def __call__(self, s: bytes) -> None:
        check_type("write()", s, bytes)
        self._write(s)
        self._chunks.append(len(s))


class GuardedIterator:
    def __init__(
        self,
        iterator: t.Iterable[bytes],
        headers_set: tuple[int, Headers],
        chunks: list[int],
    ) -> None:
        self._iterator = iterator
        self._next = iter(iterator).__next__
        self.closed = False
        self.headers_set = headers_set
        self.chunks = chunks

    def __iter__(self) -> GuardedIterator:
        return self

    def __next__(self) -> bytes:
        if self.closed:
            warn("Iterated over closed 'app_iter'.", WSGIWarning, stacklevel=2)

        rv = self._next()

        if not self.headers_set:
            warn(
                "The application returned before it started the response.",
                WSGIWarning,
                stacklevel=2,
            )

        check_type("application iterator items", rv, bytes)
        self.chunks.append(len(rv))
        return rv

    def close(self) -> None:
        self.closed = True

        if hasattr(self._iterator, "close"):
            self._iterator.close()

        if self.headers_set:
            status_code, headers = self.headers_set
            bytes_sent = sum(self.chunks)
            content_length = headers.get("content-length", type=int)

            if status_code == 304:
                for key, _value in headers:
                    key = key.lower()
                    if key not in ("expires", "content-location") and is_entity_header(
                        key
                    ):
                        warn(
                            f"Entity header {key!r} found in 304 response.",
                            HTTPWarning,
                            stacklevel=2,
                        )
                if bytes_sent:
                    warn(
                        "304 responses must not have a body.",
                        HTTPWarning,
                        stacklevel=2,
                    )
            elif 100 <= status_code < 200 or status_code == 204:
                if content_length != 0:
                    warn(
                        f"{status_code} responses must have an empty content length.",
                        HTTPWarning,
                        stacklevel=2,
                    )
                if bytes_sent:
                    warn(
                        f"{status_code} responses must not have a body.",
                        HTTPWarning,
                        stacklevel=2,
                    )
            elif content_length is not None and content_length != bytes_sent:
                warn(
                    "Content-Length and the number of bytes sent to the"
                    " client do not match.",
                    WSGIWarning,
                    stacklevel=2,
                )

    def __del__(self) -> None:
        if not self.closed:
            try:
                warn(
                    "Iterator was garbage collected before it was closed.",
                    WSGIWarning,
                    stacklevel=2,
                )
            except Exception:
                pass


class LintMiddleware:
    """Warns about common errors in the WSGI and HTTP behavior of the
    server and wrapped application. Some of the issues it checks are:

    -   invalid status codes
    -   non-bytes sent to the WSGI server
    -   strings returned from the WSGI application
    -   non-empty conditional responses
    -   unquoted etags
    -   relative URLs in the Location header
    -   unsafe calls to wsgi.input
    -   unclosed iterators

    Error information is emitted using the :mod:`warnings` module.

    :param app: The WSGI application to wrap.

    .. code-block:: python

        from werkzeug.middleware.lint import LintMiddleware
        app = LintMiddleware(app)
    """

    def __init__(self, app: WSGIApplication) -> None:
        self.app = app





    def __call__(self, *args: t.Any, **kwargs: t.Any) -> t.Iterable[bytes]:
        if len(args) != 2:
            warn("A WSGI app takes two arguments.", WSGIWarning, stacklevel=2)

        if kwargs:
            warn(
                "A WSGI app does not take keyword arguments.", WSGIWarning, stacklevel=2
            )

        environ: WSGIEnvironment = args[0]
        start_response: StartResponse = args[1]

        self.check_environ(environ)
        environ["wsgi.input"] = InputStream(environ["wsgi.input"])
        environ["wsgi.errors"] = ErrorStream(environ["wsgi.errors"])

        # Hook our own file wrapper in so that applications will always
        # iterate to the end and we can check the content length.
        environ["wsgi.file_wrapper"] = FileWrapper

        headers_set: list[t.Any] = []
        chunks: list[int] = []


        app_iter = self.app(environ, t.cast("StartResponse", checking_start_response))
        self.check_iterator(app_iter)
        return GuardedIterator(
            app_iter, t.cast(tuple[int, Headers], headers_set), chunks
        )
