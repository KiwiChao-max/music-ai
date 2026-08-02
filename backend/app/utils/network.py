"""Shared network utilities --- IP extraction, proxy trust, etc."""

from __future__ import annotations

import ipaddress


def is_trusted_proxy(remote_addr: str, trusted: list[str]) -> bool:
    """Return True if `remote_addr` matches any entry in `trusted`.

    Each entry in `trusted` may be:
      * a plain IP (``"10.0.0.1"``)
      * a CIDR network (``"10.0.0.0/8"``)

    The comparison is done via the ``ipaddress`` module so IPv4 and
    IPv6 are both handled correctly.
    """
    if not remote_addr or not trusted:
        return False
    try:
        addr = ipaddress.ip_address(remote_addr)
    except ValueError:
        return False
    for entry in trusted:
        try:
            net = ipaddress.ip_network(entry, strict=False)
        except ValueError:
            continue
        if addr in net:
            return True
    return False


def get_client_ip(
    remote_addr: str | None,
    x_forwarded_for: str | None,
    *,
    trusted_proxies: list[str] | None = None,
) -> str:
    """Extract the real client IP, only trusting ``X-Forwarded-For``
    from known reverse-proxy addresses.

    * ``remote_addr`` --- the direct TCP peer (e.g. ``request.client.host``,
      ``websocket.client.host``).
    * ``x_forwarded_for`` --- the raw ``X-Forwarded-For`` header value.
    * ``trusted_proxies`` --- list of IPs / CIDRs that are allowed to set
      ``X-Forwarded-For``.  If the direct peer is NOT in this list, the
      header is ignored entirely (it could be spoofed by the client).

    When the direct peer is trusted, the **leftmost** IP in
    ``X-Forwarded-For`` is used (the original client, per convention).
    """
    if trusted_proxies is None:
        trusted_proxies = []
    if x_forwarded_for and is_trusted_proxy(remote_addr or "", trusted_proxies):
        first = x_forwarded_for.split(",", 1)[0].strip()
        if first:
            return first
    return remote_addr or "unknown"
