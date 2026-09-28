#!/bin/sh
# Flush one zone from unbound cache (AAAA/HTTPS/SVCB leave immediately).
# Invoked by dns2bgp after a domain is added to the AAAA-suppress list.
# Packaged with sudoers so the dns2bgp user can run it as root.
set -eu

domain="${1:-}"
if [ -z "$domain" ]; then
    echo "flush-unbound-zone: domain required" >&2
    exit 2
fi

# Basic hardening: unbound zone name, no shell metacharacters / path bits.
case "$domain" in
    *[!A-Za-z0-9._-]* | *" "* | .* | *..* )
        echo "flush-unbound-zone: invalid domain: $domain" >&2
        exit 2
        ;;
esac

exec unbound-control flush_zone "$domain"
