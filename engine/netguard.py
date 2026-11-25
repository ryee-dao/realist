"""Outbound reads of URLs that come from third-party pages (listing pages, photo galleries, robots.txt).

Those URLs are chosen by publishers, so before anything is sent the engine checks that the URL is https on the
default port, has no user name or password, and that its host resolves only to public addresses: never loopback,
private, link-local (169.254.169.254, the cloud metadata service), carrier-grade NAT, multicast or reserved ranges.
Every redirect is checked the same way before it is followed. Standard library only.

Limits: the address is resolved here and again by urllib when it connects, so a host that changes its DNS answer
between the two (DNS rebinding) is not fully excluded. The public-API client (engine/http.py) reads fixed government
hosts and does not use this module.
"""
import ipaddress
import socket
import urllib.request
from urllib.parse import urlsplit

MAX_REDIRECTS = 4


class UnsafeURL(ValueError):
    """The URL is not one the engine will fetch (scheme, port, credentials or a non-public address)."""


def _addresses(host, resolve):
    try:
        return {ipaddress.ip_address(host.strip("[]"))}
    except ValueError:
        pass
    try:
        infos = resolve(host, 443, type=socket.SOCK_STREAM)
    except UnicodeError as exc:
        raise UnsafeURL(f"unusable host name {host!r}") from exc
    return {ipaddress.ip_address(info[4][0].split("%")[0]) for info in infos}


def _public(ip):
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def check_url(url, resolve=socket.getaddrinfo):
    """Raise UnsafeURL unless ``url`` is https, on port 443, without credentials, and every address of its host is
    public. Name resolution failures raise OSError (socket.gaierror), as a failed connection would."""
    if not isinstance(url, str):
        raise UnsafeURL("not a URL")
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise UnsafeURL("only https URLs are fetched")
    try:
        port = parts.port
    except ValueError as exc:
        raise UnsafeURL("malformed port") from exc
    host = (parts.hostname or "").rstrip(".")
    if not host or parts.username is not None or parts.password is not None or port not in (None, 443):
        raise UnsafeURL("URL has no host, has credentials, or uses a non-standard port")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise UnsafeURL(f"{host} is a local name")
    addresses = _addresses(host, resolve)
    if not addresses or not all(_public(ip) for ip in addresses):
        raise UnsafeURL(f"{host} does not resolve to a public address")


class _CheckedRedirects(urllib.request.HTTPRedirectHandler):
    max_redirections = MAX_REDIRECTS

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_CheckedRedirects)


def urlopen_public(request, timeout):
    """urllib.request.urlopen for a third-party URL: check_url on the URL and on every redirect, then open."""
    url = request.full_url if isinstance(request, urllib.request.Request) else request
    check_url(url)
    return _OPENER.open(request, timeout=timeout)


class HostCheck:
    """check_url as a cached yes/no per scheme and host, for a browser's request filter (one lookup per host)."""

    def __init__(self, resolve=socket.getaddrinfo):
        self.resolve = resolve
        self.seen = {}

    def allowed(self, url):
        parts = urlsplit(url)
        key = (parts.scheme, parts.netloc)
        if key not in self.seen:
            try:
                check_url(url, self.resolve)
                self.seen[key] = True
            except (OSError, ValueError):
                self.seen[key] = False
        return self.seen[key]
