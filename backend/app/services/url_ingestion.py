"""Fetch a remote recording with address pinning and TLS verification."""

import base64
import hmac
import http.client
import ipaddress
import re
import socket
import ssl
import time
from contextlib import contextmanager
from shutil import disk_usage
from threading import Timer
from urllib.parse import urljoin, urlsplit

import dns.exception
import dns.resolver
from cryptography.fernet import Fernet

from app.core.errors import StudioError
from app.core.settings import get_settings

CHUNK = 256 * 1024
PAGE_HOSTS = ("youtube.com", "youtu.be", "youtube-nocookie.com", "twitch.tv")
REDIRECTS = {301, 302, 303, 307, 308}


def url_cipher():
    secret = get_settings().redis_password.get_secret_value().encode()
    return Fernet(
        base64.urlsafe_b64encode(
            hmac.digest(secret, b"clipper:url-import:v1", "sha256")
        )
    )


def resolve(host):
    try:
        return [ipaddress.ip_address(host)]
    except ValueError:
        pass
    try:
        answers = dns.resolver.resolve_name(host + ".", lifetime=5)
        return [ipaddress.ip_address(a) for a in answers.addresses()]
    except dns.exception.DNSException:
        raise StudioError(422, "import_dns") from None


def parse_https(url):
    if (
        not url
        or len(url) > 4096
        or not url.isascii()
        or any(ord(c) <= 32 or ord(c) == 127 for c in url)
        or "\\" in url
    ):
        raise StudioError(422, "import_url")
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if (
            not host
            or parts.username is not None
            or parts.password is not None
            or parts.port not in (None, 443)
            or parts.fragment
        ):
            raise ValueError
        if "%" in host or not re.fullmatch(r"[a-z0-9.:-]+", host):
            raise ValueError
        host = host.rstrip(".")
    except ValueError:
        raise StudioError(422, "import_url") from None
    if parts.scheme != "https":
        raise StudioError(422, "import_url")
    return parts, host


def destination(url):
    parts, host = parse_https(url)
    if any(host == h or host.endswith("." + h) for h in PAGE_HOSTS):
        raise StudioError(422, "import_provider_page")
    if (
        host == "localhost"
        or host.endswith((".localhost", ".local", ".internal"))
        or ("." not in host and ":" not in host)
    ):
        raise StudioError(422, "import_destination")
    addresses = resolve(host)
    for addr in addresses:
        if not addr.is_global or addr.is_multicast or str(addr) == "168.63.129.16":
            raise StudioError(422, "import_destination")
        if addr.version == 6 and (
            addr not in ipaddress.ip_network("2000::/3")
            or addr.sixtofour
            or addr.teredo
        ):
            raise StudioError(422, "import_destination")
    if not addresses:
        raise StudioError(422, "import_dns")
    return host, addresses, parts.path or "/", parts.query


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, address, timeout):
        super().__init__(
            host, 443, timeout=timeout, context=ssl.create_default_context()
        )
        self.address = address

    def connect(self):
        family = socket.AF_INET if self.address.version == 4 else socket.AF_INET6
        with socket.socket(family, socket.SOCK_STREAM) as raw:
            raw.settimeout(self.timeout)
            raw.connect((str(self.address), 443))
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


@contextmanager
def response_for(url, deadline):
    host, addresses, path, query = destination(url)
    # Prefer IPv4 where both families are published. No second DNS lookup.
    for address in sorted(addresses, key=lambda a: a.version):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise StudioError(408, "import_timeout")
        conn = PinnedHTTPS(host, address, min(10, remaining))
        try:
            conn.connect()
        except OSError:
            conn.close()
            continue
        sock = conn.sock

        def expire(sock=sock):
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

        timer = Timer(min(15, max(0, deadline - time.monotonic())), expire)
        timer.daemon = True
        timer.start()
        try:
            conn.request(
                "GET",
                path + ("?" + query if query else ""),
                headers={"Accept-Encoding": "identity"},
            )
            with conn.getresponse() as response:
                timer.cancel()
                timer = Timer(max(0, deadline - time.monotonic()), expire)
                timer.daemon = True
                timer.start()
                yield response
        finally:
            timer.cancel()
            conn.close()
        return
    raise StudioError(502, "import_connection")


def download(url, target, progress, *, quota=None):
    cfg = get_settings()
    started = time.monotonic()
    deadline = started + cfg.import_download_seconds
    budget = min(cfg.import_max_bytes, cfg.recording_quota_bytes)

    def check_space(incoming, written):
        if quota is not None and written + incoming > quota:
            raise StudioError(507, "recording_quota")
        if written + incoming > budget:
            raise StudioError(413, "import_size")
        if disk_usage(target.parent).free < cfg.import_reserve_bytes + incoming:
            raise StudioError(507, "import_space")

    received = 0
    try:
        for hop in range(4):
            with response_for(url, deadline) as response:
                if response.status in REDIRECTS:
                    location = response.getheader("Location")
                    if not location or hop == 3:
                        raise StudioError(422, "import_redirect")
                    url = urljoin(url, location)
                    continue
                if response.status in {401, 403, 404, 410}:
                    raise StudioError(422, "import_access")
                if (
                    response.status != 200
                    or response.getheader("Content-Encoding", "identity").lower()
                    != "identity"
                ):
                    raise StudioError(422, "import_response")
                length = response.getheader("Content-Length")
                if length is not None and (
                    not re.fullmatch(r"[0-9]{1,20}", length)
                    or response.getheader("Transfer-Encoding")
                ):
                    raise StudioError(422, "import_response")
                total = int(length) if length is not None else None
                check_space(total or 0, 0)
                progress(0, total)
                with target.open("xb") as output:
                    next_read = time.monotonic()
                    while chunk := response.read1(CHUNK):
                        check_space(len(chunk), received)
                        output.write(chunk)
                        received += len(chunk)
                        progress(received, total)
                        if time.monotonic() >= deadline:
                            raise StudioError(408, "import_timeout")
                        delay = (
                            next_read
                            + len(chunk) / cfg.import_bytes_per_second
                            - time.monotonic()
                        )
                        if delay > 0:
                            time.sleep(min(delay, max(0, deadline - time.monotonic())))
                        next_read = time.monotonic()
                if time.monotonic() >= deadline:
                    raise StudioError(408, "import_timeout")
                if not received or (total is not None and total != received):
                    raise StudioError(422, "import_incomplete")
                return received
    except TimeoutError:
        raise StudioError(408, "import_timeout") from None
    except (OSError, http.client.HTTPException, ValueError):
        raise StudioError(502, "import_transfer") from None
