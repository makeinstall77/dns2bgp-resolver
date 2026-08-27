# dns2bgp-resolver

[English](README.md) | [Русский](README.ru.md)

Резолвит список доменов в IPv4-адреса и публикует их как static-маршруты bird для направления трафика в VPN.

CLI, Telegram-бот и веб-UI работают через одну шину команд. Хранение — через порт репозитория (по умолчанию SQLite; PostgreSQL — тот же адаптер SQLAlchemy, другой URL).

## Установка

### Debian-пакет

Зависимости для сборки:

```bash
sudo apt install python3 python3-venv python3-pip dpkg-dev
```

Сборка и установка:

```bash
git clone https://github.com/makeinstall77/dns2bgp-resolver.git && cd dns2bgp-resolver
./scripts/build-deb.sh
sudo dpkg -i dist/dns2bgp-resolver_*.deb
```

Переустановка после изменений:

```bash
./scripts/build-deb.sh && sudo dpkg -i dist/dns2bgp-resolver_*.deb
```

Удаление:

```bash
sudo apt remove dns2bgp-resolver      # останавливает сервис, оставляет /etc и /var/lib
sudo apt purge dns2bgp-resolver       # также удаляет /etc/dns2bgp; данные в /var/lib/dns2bgp остаются
```

Сборка + установка одной командой:

```bash
./scripts/build-deb.sh --install
```

После установки отредактируйте `/etc/dns2bgp/config.yaml` (`web.api_key`, `bird.nexthop`, `telegram.token`), затем:

```bash
sudo systemctl restart dns2bgp
```

В `bird.conf`:

```bird
include "/var/lib/dns2bgp/dns2bgp.routes";
```

См. [deploy/bird.include.example.conf](deploy/bird.include.example.conf).

### Разработка (из исходников)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp config.example.yaml config.yaml
```

## CLI

```bash
dns2bgp add example.com
dns2bgp add '*.youtube.com'
dns2bgp prefixes add 149.154.160.0/20 --name telegram
dns2bgp prefixes list
dns2bgp list
dns2bgp resolve              # все ручные домены
dns2bgp resolve example.com
dns2bgp export
dns2bgp serve                # планировщик + web + telegram (+ dnstap, если включён)
```

## Интеграция с Bird

Сервис атомарно пишет include-файл (по умолчанию `./data/dns2bgp.routes`). Bird читает его при старте/reload; если резолвер упал, bird сохраняет последние маршруты. Если bird не работает, резолвер всё равно обновляет файл.

Источники пула префиксов:

- **ручные домены** — активный DNS-резолв (обновление по TTL); `*.example.com` / `example.com` также ловят субдомены через dnstap
- **авто-списки доменов** — синхронизируются во in-memory индекс (без pre-resolve); IP появляются, когда unbound отвечает на совпадающие запросы (dnstap)
- **статические префиксы** — CIDR/IP как есть (`dns2bgp prefixes add 149.154.160.0/20`)

### Unbound dnstap (рекомендуется)

Запускайте dns2bgp рядом с unbound (или шарьте путь к сокету). В `config.yaml`:

```yaml
dnstap:
  enabled: true
  listen_unix: "/var/lib/dns2bgp/dnstap.sock"
```

В `unbound.conf` (лучше TCP — unix часто коннектится без data frames):

```
dnstap:
  dnstap-enable: yes
  dnstap-bidirectional: no
  dnstap-ip: "127.0.0.1@9255"
  dnstap-tls: no
  dnstap-log-client-response-messages: yes
```

Соответствующий `config.yaml`:

```
dnstap:
  enabled: true
  listen_unix: ""
  listen_tcp: "127.0.0.1:9255"
```

Альтернатива через unix-сокет:

```
dnstap-socket-path: "/var/lib/dns2bgp/dnstap.sock"
dnstap-bidirectional: no
```

Если unbound ограничен AppArmor, разрешите сокет (например в `/etc/apparmor.d/local/usr.sbin.unbound`):

```
/var/lib/dns2bgp/dnstap.sock rw,
```

Unbound подключается к сокету; dns2bgp слушает. Домены из auto-list никогда не резолвятся пачкой.

В `bird.conf`:

```bird
include "/var/lib/dns2bgp/dns2bgp.routes";
```

См. [deploy/bird.include.example.conf](deploy/bird.include.example.conf) и [deploy/dns2bgp.service](deploy/dns2bgp.service).

Задайте `bird.include_path` и при необходимости `bird.birdc_enable: true` в `config.yaml`. После каждого export сервис вызывает `birdc configure` (best-effort; export не падает из‑за bird). Пользователь `dns2bgp` должен быть в группе `bird` для доступа к control socket (`birdc_socket`, по умолчанию `/run/bird/bird.ctl`); deb-пакет добавляет это сам.

## Web и Telegram

- Web UI: `http://127.0.0.1:8080/` — ручные домены; `/auto` — auto-домены; `/settings` — списки доменов и sync
- REST: `GET/POST /api/domains`, `DELETE /api/domains/{name}`, `POST /api/resolve`
- Auto REST: `GET /api/auto/domains?q=&page=`, `GET/POST/DELETE /api/auto/filters`, `POST /api/auto/sync`
- Lists REST: `GET/POST /api/lists`, `PATCH/DELETE /api/lists/{id}`, `POST /api/lists/{id}/sync|clear`
- Settings REST: `GET/PATCH /api/settings`
- Telegram: UI на кнопках (Reply + Inline); `/start` открывает главное меню

## Списки доменов

Несколько URL/file-списков с интервалом sync на каждый. Bootstrap из конфига при первом запуске:

```yaml
auto_list:
  enabled: true
  url: "https://antifilter.download/list/domains.lst"
  sync_interval: 86400
  sync_on_startup: true
  exclude_keywords: []
```

Управление в runtime: web `/settings`, CLI или Telegram. Отключение sync сохраняет домены; clear удаляет только домены; delete удаляет список и домены.

Маршруты экспортируются как префиксы `/24` (с дедупликацией).

```bash
dns2bgp lists show
dns2bgp lists add-url https://example.com/domains.lst --name mylist
dns2bgp lists add-file ./domains.txt
dns2bgp lists enable|disable|clear|remove|sync [id]
dns2bgp settings sync-interval 86400
dns2bgp sync-auto
```

Ручные и auto-домены хранятся отдельно. `list` показывает только ручные. Keyword-фильтр исключает домены с заданными подстроками.

## PostgreSQL

```yaml
database:
  url: "postgresql+asyncpg://user:pass@localhost/dns2bgp"
```

```bash
pip install 'dns2bgp-resolver[postgres]'
```

Слой команд не меняется — тот же порт `DomainRepository`.

## IPv6

BGP-пул остаётся только IPv4/`A`. Dual-stack сайты предпочитают AAAA и обходят VPN — используйте **suppress**, чтобы клиенты не получали AAAA для перечисленных доменов.

```yaml
ipv6:
  mode: suppress   # off | suppress | announce
  dnsdist_list_path: "/var/lib/dns2bgp/aaaa-suppress.domains"
  dnsdist_reload_enable: true
  dnsdist_console_key_file: "/etc/dns2bgp/dnsdist.key"
  dnsdist_reload_cmd:
    - /usr/lib/dns2bgp/reload-dnsdist.sh
```

Ключ консоли dnsdist положите в `/etc/dns2bgp/dnsdist.key` (читаемый для `dns2bgp`). При установке пакета: helper-скрипт + `ExecStartPost` в `dns2bgp.service` + `ExecReload` в `dnsdist.service.d`, чтобы изменения списка и systemd-рестарты обновляли in-memory SuffixMatchNode.

- **`off`** — без политики IPv6 (по умолчанию).
- **`suppress`** — после каждой пересборки DomainIndex пишет список доменов и best-effort reload dnsdist. dnsdist перед unbound; AAAA (и желательно HTTPS/SVCB) для совпавших имён → NODATA. Пример: [deploy/dnsdist.example.conf](deploy/dnsdist.example.conf).
- **`announce`** — заготовка на потом: собирать AAAA и экспортировать Bird IPv6, когда VPN это поддерживает. Тот же DomainIndex; без suppress в dnsdist.

Проверка:

```bash
dig AAAA matched.example @127.0.0.1 -p 5353   # NOERROR, 0 answers
dig A matched.example @127.0.0.1 -p 5353      # обычный A через unbound
dig AAAA other.example @127.0.0.1 -p 5353     # реальный AAAA (нормальный IPv6-путь)
```

Статические IPv4-префиксы без домена suppress не покрывает.

В строках адресов уже есть `family`; полноценный `announce` — расширение DNS/dnstap + Bird `ipv6` позже.

## Тесты

```bash
pytest
```

## Лицензия

[MIT](LICENSE)
