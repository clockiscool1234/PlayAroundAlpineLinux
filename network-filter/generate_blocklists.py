#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ipaddress
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

DOMAIN_RE = re.compile(
    r"^(?=.{1,253}\.?$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.?$"
)


def normalize_domain(value: str) -> str | None:
    value = value.strip().lower()
    if not value or value.startswith(("#", "!")):
        return None

    # Accept domains, URLs, hosts-file rows, and common adblock syntax.
    value = value.removeprefix("||").removesuffix("^")
    if " " in value or "\t" in value:
        value = value.split()[-1]
    parsed = urlsplit(value if "://" in value else f"//{value}")
    host = parsed.hostname
    if not host:
        return None
    host = host.strip(".*.")
    if host.startswith("www."):
        host = host[4:]
    try:
        ipaddress.ip_address(host)
        return None
    except ValueError:
        pass
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    return host if DOMAIN_RE.fullmatch(host) else None


def add_lines(path: Path, domains: set[str]) -> None:
    if not path.exists():
        raise FileNotFoundError(f"Required blocklist is missing: {path}")
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        domain = normalize_domain(line)
        if domain:
            domains.add(domain)


def collapse_subdomains(domains: set[str]) -> list[str]:
    """Remove entries already covered by a blocked parent domain."""
    collapsed: list[str] = []
    for domain in sorted(domains):
        labels = domain.split(".")
        if any(".".join(labels[index:]) in domains for index in range(1, len(labels))):
            continue
        collapsed.append(domain)
    return collapsed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bad-websites", type=Path, required=True)
    parser.add_argument("--phishing-domains", type=Path, required=True)
    parser.add_argument("--phishing-ips", type=Path, required=True)
    parser.add_argument("--custom", type=Path, required=True)
    parser.add_argument("--domains-output", type=Path, required=True)
    parser.add_argument("--ips-output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    domains: set[str] = set()

    with args.bad_websites.open(encoding="utf-8") as file:
        bad_websites = json.load(file)
    for value in bad_websites.get("links", []):
        domain = normalize_domain(str(value))
        if domain:
            domains.add(domain)

    add_lines(args.phishing_domains, domains)
    add_lines(args.custom, domains)
    domains = set(collapse_subdomains(domains))

    addresses: set[str] = set()
    for line in args.phishing_ips.read_text(
        encoding="utf-8", errors="ignore"
    ).splitlines():
        try:
            addresses.add(str(ipaddress.ip_address(line.strip())))
        except ValueError:
            continue

    args.domains_output.write_text(
        "".join(f".{domain}\n" for domain in sorted(domains)),
        encoding="ascii",
    )
    args.ips_output.write_text(
        "".join(f"{address}\n" for address in sorted(addresses)),
        encoding="ascii",
    )
    print(
        f"Generated {len(domains):,} blocked domains and "
        f"{len(addresses):,} blocked IP addresses",
        flush=True,
    )


if __name__ == "__main__":
    main()
