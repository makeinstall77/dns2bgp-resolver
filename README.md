# dns2bgp-resolver

Resolve a list of domains into IPv4 addresses and publish them as bird static routes for VPN traffic steering.

CLI, Telegram bot, and web UI all talk to the same command bus. Persistence goes through a repository port (SQLite by default; PostgreSQL via the same SQLAlchemy adapter and a different URL).

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp config.example.yaml config.yaml
```

## CLI

```bash
dns2bgp add example.com
dns2bgp list
dns2bgp resolve              # all domains
dns2bgp resolve example.com
dns2bgp export
dns2bgp serve                # scheduler + web + telegram
```

## Bird integration

The service atomically writes an include file (default `./data/dns2bgp.routes`). Bird reads that file on start/reload; if the resolver dies, bird keeps the last good routes. If bird is down, the resolver still updates the file.

In `bird.conf`:

```bird
include "/var/lib/dns2bgp/dns2bgp.routes";
```

See [deploy/bird.include.example.conf](deploy/bird.include.example.conf) and [deploy/dns2bgp.service](deploy/dns2bgp.service).

Set `bird.include_path` and optionally `bird.birdc_enable: true` in `config.yaml`. `birdc configure` is best-effort and never fails the export.

## Web & Telegram

- Web UI: `http://127.0.0.1:8080/` (API key from config; send `X-API-Key` for REST)
- REST: `GET/POST /api/domains`, `DELETE /api/domains/{name}`, `POST /api/resolve`
- Telegram: set `telegram.token` and `telegram.allowed_user_ids`; commands `/add`, `/remove`, `/list`, `/resolve`

## PostgreSQL later

```yaml
database:
  url: "postgresql+asyncpg://user:pass@localhost/dns2bgp"
```

```bash
pip install 'dns2bgp-resolver[postgres]'
```

No changes to the command layer — same `DomainRepository` port.

## IPv6

MVP is IPv4/`A` only. Address rows already store `family`; enabling `AAAA` later is a DNS resolver + export extension, not a redesign.

## Tests

```bash
pytest
```
