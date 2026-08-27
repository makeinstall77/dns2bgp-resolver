from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import dns.message
import dns.name
import dns.rdatatype
import pytest

from dns2bgp_resolver.application.services.domain_index_service import DomainIndexService
from dns2bgp_resolver.application.services.passive_dns import PassiveDnsCollector
from dns2bgp_resolver.application.services.resolve_pipeline import ResolvePipeline
from dns2bgp_resolver.config import BirdSettings, RefreshSettings
from dns2bgp_resolver.domain import Domain, DomainName, IpAddress, Prefix, ResolvedAddress, StaticPrefix
from dns2bgp_resolver.domain.domain_index import DomainIndex
from dns2bgp_resolver.infrastructure.bird.static_file_exporter import StaticFileBirdExporter
from dns2bgp_resolver.infrastructure.db.sqlite_repository import SqlAlchemyDomainRepository
from dns2bgp_resolver.infrastructure.dnstap.consumer import extract_dnstap_response_wire


class FixedClock:
    def __init__(self, moment: datetime) -> None:
        self._moment = moment

    def now(self) -> datetime:
        return self._moment


class FakeDns:
    async def resolve_a(self, name: DomainName) -> list[ResolvedAddress]:
        return [ResolvedAddress(ip=IpAddress("8.8.8.8"), ttl_seconds=60)]


@pytest.fixture
async def repo(tmp_path: Path):
    repository = SqlAlchemyDomainRepository(f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    await repository.initialize()
    yield repository
    await repository.close()


def test_domain_index_suffix_match():
    idx = DomainIndex()
    idx.rebuild({"example.com", "other.org"})
    assert idx.matches("example.com") == "example.com"
    assert idx.matches("www.example.com") == "example.com"
    assert idx.matches("a.b.example.com") == "example.com"
    assert idx.matches("notlisted.net") is None
    assert idx.matches("com") is None


def test_parse_wildcard():
    from dns2bgp_resolver.domain import format_domain_label, parse_domain_input

    name, mode = parse_domain_input("*.youtube.com")
    assert str(name) == "youtube.com"
    assert mode == "suffix"
    assert format_domain_label(str(name), mode) == "*.youtube.com"

    name, mode = parse_domain_input("youtube.com")
    assert str(name) == "youtube.com"
    assert mode == "exact"
    assert format_domain_label(str(name), mode) == "youtube.com"


def test_domain_index_exact_vs_suffix():
    idx = DomainIndex()
    idx.rebuild(rules=[("exact.com", "exact"), ("suffix.com", "suffix")])
    assert idx.matches("exact.com") == "exact.com"
    assert idx.matches("www.exact.com") is None
    assert idx.matches("a.suffix.com") == "suffix.com"
    assert idx.matches("suffix.com") == "suffix.com"


def test_prefix_keeps_length():
    p = Prefix.parse("149.154.160.0/20", source="static")
    assert p.cidr == "149.154.160.0/20"


@pytest.mark.asyncio
async def test_list_due_skips_auto(repo):
    await repo.add(Domain.create("manual.com", source="manual"))
    await repo.add(Domain.create("auto.com", source="auto"))
    due = await repo.list_due(datetime(2026, 1, 1, tzinfo=timezone.utc))
    names = {str(d.name) for d in due}
    assert "manual.com" in names
    assert "auto.com" not in names


@pytest.mark.asyncio
async def test_static_prefix_export(repo, tmp_path: Path):
    bird_path = tmp_path / "routes.bird"
    pipe = ResolvePipeline(
        repository=repo,
        resolver=FakeDns(),
        exporter=StaticFileBirdExporter(
            BirdSettings(include_path=str(bird_path), birdc_enable=False)
        ),
        clock=FixedClock(datetime(2026, 1, 1, tzinfo=timezone.utc)),
        refresh=RefreshSettings(),
        export_path=str(bird_path),
        export_min_interval=0,
    )
    await repo.add_static_prefix(StaticPrefix(cidr="149.154.160.0/20", name="telegram"))
    summary = await pipe.export_routes()
    assert summary.prefix_count == 1
    text = bird_path.read_text()
    assert "149.154.160.0/20" in text


@pytest.mark.asyncio
async def test_passive_hit_export(repo, tmp_path: Path):
    bird_path = tmp_path / "routes.bird"
    pipe = ResolvePipeline(
        repository=repo,
        resolver=FakeDns(),
        exporter=StaticFileBirdExporter(
            BirdSettings(include_path=str(bird_path), birdc_enable=False)
        ),
        clock=FixedClock(datetime(2026, 1, 1, tzinfo=timezone.utc)),
        refresh=RefreshSettings(),
        export_path=str(bird_path),
        export_min_interval=0,
    )
    idx = DomainIndex()
    idx.rebuild({"blocked.com"})
    collector = PassiveDnsCollector(idx, pipe)
    await collector.on_response("cdn.blocked.com", ["1.2.3.4"])
    await pipe.flush_pending_export()
    text = bird_path.read_text()
    assert "1.2.3.4/32" in text


@pytest.mark.asyncio
async def test_index_rebuild_from_repo(repo):
    await repo.add(Domain.create("a.example", source="auto", match_mode="suffix"))
    await repo.add(Domain.create("b.example", source="manual"))
    svc = DomainIndexService(repo, DomainIndex())
    size = await svc.rebuild()
    assert size == 2
    assert svc.index.matches("x.a.example") == "a.example"
    assert svc.index.matches("x.b.example") is None


def _encode_varint(value: int) -> bytes:
    out = bytearray()
    while value > 0x7F:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value & 0x7F)
    return bytes(out)


def _field_bytes(field_no: int, data: bytes) -> bytes:
    tag = (field_no << 3) | 2
    return _encode_varint(tag) + _encode_varint(len(data)) + data


def _field_varint(field_no: int, value: int) -> bytes:
    tag = (field_no << 3) | 0
    return _encode_varint(tag) + _encode_varint(value)


def test_extract_dnstap_response_wire():
    qname = dns.name.from_text("www.example.com")
    msg = dns.message.make_response(dns.message.make_query(qname, dns.rdatatype.A))
    rrset = msg.find_rrset(
        msg.answer, qname, dns.rdataclass.IN, dns.rdatatype.A, create=True, force_unique=True
    )
    from dns.rdtypes.IN.A import A

    rrset.add(A(dns.rdataclass.IN, dns.rdatatype.A, "93.184.216.34"))
    wire = msg.to_wire()
    # Message: type=CLIENT_RESPONSE(6) field 1, response_message field 15
    message = _field_varint(1, 6) + _field_bytes(15, wire)
    # Dnstap: message field 14
    dnstap = _field_bytes(14, message)
    extracted = extract_dnstap_response_wire(dnstap)
    assert extracted == wire
