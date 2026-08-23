# dns2bgp-resolver

Resolve a list of domains into IPv4 addresses and publish them as bird static routes for VPN traffic steering.

CLI, Telegram bot, and web UI all talk to the same command bus. Persistence goes through a repository port (SQLite by default; PostgreSQL via the same SQLAlchemy adapter and a different URL).

## Install

### Debian package

Build dependencies:

```bash
sudo apt install python3 python3-venv python3-pip dpkg-dev
```

Build and install:

```bash
git clone <repo-url> && cd dns2bgp-resolver
./scripts/build-deb.sh
sudo dpkg -i dist/dns2bgp-resolver_*.deb
```

Reinstall after changes:

```bash
./scripts/build-deb.sh && sudo dpkg -i dist/dns2bgp-resolver_*.deb
```

Remove:

```bash
sudo apt remove dns2bgp-resolver      # stops service, keeps /etc and /var/lib
sudo apt purge dns2bgp-resolver       # also removes /etc/dns2bgp; data in /var/lib/dns2bgp stays
```

One-liner build + install:

```bash
./scripts/build-deb.sh --install
```

After install, edit `/etc/dns2bgp/config.yaml` (`web.api_key`, `bird.nexthop`, `telegram.token`), then:

```bash
sudo systemctl restart dns2bgp
```

Add to `bird.conf`:

```bird
include "/var/lib/dns2bgp/dns2bgp.routes";
```

See [deploy/bird.include.example.conf](deploy/bird.include.example.conf).

### Development (from source)

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

Set `bird.include_path` and optionally `bird.birdc_enable: true` in `config.yaml`. After each export the service runs `birdc configure` (best-effort; export never fails because of bird). The `dns2bgp` user must be in the `bird` group to access the control socket (`birdc_socket`, default `/run/bird/bird.ctl`); the deb package adds this automatically.

## Web & Telegram

- Web UI: `http://127.0.0.1:8080/` — manual domains; `/auto` — auto domains; `/settings` — domain lists and sync
- REST: `GET/POST /api/domains`, `DELETE /api/domains/{name}`, `POST /api/resolve`
- Auto REST: `GET /api/auto/domains?q=&page=`, `GET/POST/DELETE /api/auto/filters`, `POST /api/auto/sync`
- Lists REST: `GET/POST /api/lists`, `PATCH/DELETE /api/lists/{id}`, `POST /api/lists/{id}/sync|clear`
- Settings REST: `GET/PATCH /api/settings`
- Telegram: button UI (Reply + Inline keyboards); `/start` opens main menu

## Domain lists

Multiple URL/file lists with per-list sync interval. Bootstrap from config on first run:

```yaml
auto_list:
  enabled: true
  url: "https://antifilter.download/list/domains.lst"
  sync_interval: 86400
  sync_on_startup: true
  exclude_keywords: []
```

Runtime management via web `/settings`, CLI, or Telegram. Disable sync keeps domains; clear removes domains only; delete removes list and domains.

Routes export as `/24` prefixes (deduplicated).

```bash
dns2bgp lists show
dns2bgp lists add-url https://example.com/domains.lst --name mylist
dns2bgp lists add-file ./domains.txt
dns2bgp lists enable|disable|clear|remove|sync [id]
dns2bgp settings sync-interval 86400
dns2bgp sync-auto
```

Manual and auto domains are stored separately. `list` shows manual only. Keyword filter excludes domains containing configured substrings.

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
