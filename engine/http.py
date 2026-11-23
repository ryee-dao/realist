"""Polite HTTP client: paced, retried with backoff, and counted.

The engine trades speed for consistency. Every request is spaced out per host,
transient failures are retried with exponential backoff, and a response only
counts once it parses as JSON.
"""
import gzip
import json
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request

RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}


class FetchError(RuntimeError):
    pass


class Client:
    def __init__(self, min_interval=0.5, retries=6, timeout=180, log=print):
        self.min_interval = min_interval
        self.retries = retries
        self.timeout = timeout
        self.log = log
        self._last = {}
        self.stats = {"requests": 0, "retries": 0, "bytes": 0}

    def _pace(self, host):
        wait = self._last.get(host, 0) + self.min_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)

    def get_json(self, url, params):
        return self._get(url, params, json.loads)

    def get_bytes(self, url):
        return self._get(url, {}, lambda body: body)

    def head(self, url):
        """Response headers only (for Last-Modified / ETag change checks on large files)."""
        return self._get(url, {}, None, method="HEAD")

    def _get(self, url, params, parse, method="GET"):
        full = url + ("?" + urllib.parse.urlencode(params) if params else "")
        host = urllib.parse.urlsplit(url).netloc
        headers = {"User-Agent": "realist-engine/2.0", "Accept-Encoding": "gzip"}
        token = os.environ.get("SOCRATA_APP_TOKEN")
        if token and "carto" not in host:
            headers["X-App-Token"] = token
        last_error = None
        for attempt in range(self.retries):
            self._pace(host)
            self.stats["requests"] += 1
            try:
                req = urllib.request.Request(full, headers=headers, method=method)
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    if parse is None:
                        self._last[host] = time.monotonic()
                        return {k.lower(): v for k, v in resp.headers.items()}
                    body = resp.read()
                    if resp.headers.get("Content-Encoding") == "gzip":
                        body = gzip.decompress(body)
                self._last[host] = time.monotonic()
                self.stats["bytes"] += len(body)
                return parse(body)
            except urllib.error.HTTPError as e:
                self._last[host] = time.monotonic()
                detail = e.read()[:300].decode("utf-8", "replace")
                # some APIs report a busy server as a 400 "timeout"; that is worth retrying too
                if e.code not in RETRY_STATUS and "timeout" not in detail.lower():
                    raise FetchError(f"{host} returned {e.code}: {detail}") from e
                last_error = f"HTTP {e.code}"
                delay = float(e.headers.get("Retry-After") or 0)
            except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError, OSError) as e:
                self._last[host] = time.monotonic()
                last_error = repr(e)
                delay = 0
            self.stats["retries"] += 1
            delay = max(delay, min(90, 2 ** (attempt + 1)) + random.random())
            self.log(f"  retry {attempt + 1}/{self.retries} for {host} in {delay:.0f}s ({last_error})")
            time.sleep(delay)
        raise FetchError(f"{host} failed after {self.retries} attempts: {last_error}")


def socrata_pages(client, base, dataset, select, where, page=50000, order=":id"):
    """Yield every row of a Socrata query, one stable-ordered page at a time."""
    url = f"{base}/resource/{dataset}.json"
    offset = 0
    while True:
        q = f"select {select}"
        if where:
            q += f" where {where}"
        q += f" order by {order} limit {page} offset {offset}"
        rows = client.get_json(url, {"$query": q})
        if not isinstance(rows, list):
            raise FetchError(f"{dataset}: unexpected response {str(rows)[:200]}")
        yield from rows
        if len(rows) < page:
            return
        offset += page


def socrata_updated_at(client, base, dataset):
    """Timestamp of the dataset's last row change, used to skip unchanged sources."""
    meta = client.get_json(f"{base}/api/views/{dataset}.json", {})
    return meta.get("rowsUpdatedAt")
