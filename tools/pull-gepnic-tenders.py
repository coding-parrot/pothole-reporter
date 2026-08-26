#!/usr/bin/env python3
"""Collect public GePNIC procurement notices with explicit road-surface scope.

The Central Public Procurement Portal exposes a public "Tenders by Organisation"
directory.  Its organisation links are session-bound, so a live pull must open the
directory and follow those links with the same cookie jar.  This tool only performs
those public GET requests.  It never submits the CAPTCHA-backed search form and stops
if GePNIC presents a CAPTCHA instead of the public listing.

The returned rows are *procurement notices*.  A listed tender is not evidence of an
award, a winning contractor, an active maintenance contract, or a defect-liability
period.  Consequently this tool deliberately emits none of those fields.
"""

from __future__ import annotations

import argparse
import http.cookiejar
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
import time
from typing import Callable, Iterable, Sequence
import urllib.error
import urllib.parse
import urllib.request

from tender_scope import is_road_surface_contract


ORGANISATION_URL = (
    "https://eprocure.gov.in/eprocure/app?component=clear"
    "&page=FrontEndTendersByOrganisation&service=direct"
)
DEFAULT_SOURCE_NAME = "GePNIC public procurement portal"
USER_AGENT = (
    "PotholeReporter-GePNIC/1.0 "
    "(+https://github.com/coding-parrot/pothole-reporter)"
)
INDIA_TIMEZONE = timezone(timedelta(hours=5, minutes=30))
DATE_FORMAT = "%d-%b-%Y %I:%M %p"
LIFECYCLE = "procurement_notice"


class CrawlerError(RuntimeError):
    """A fail-closed error while fetching or interpreting a GePNIC listing."""


def _clean_text(value: object) -> str:
    return " ".join(str(value or "").split())


def _normalise_label(value: object) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(value or "").lower()))


@dataclass(frozen=True)
class Link:
    href: str
    title: str
    text: str


@dataclass(frozen=True)
class Cell:
    text: str
    links: tuple[Link, ...]


@dataclass(frozen=True)
class Row:
    cells: tuple[Cell, ...]


@dataclass(frozen=True)
class Table:
    rows: tuple[Row, ...]


@dataclass
class _LinkBuilder:
    href: str
    title: str
    text_parts: list[str] = field(default_factory=list)

    def freeze(self) -> Link:
        return Link(self.href, self.title, _clean_text("".join(self.text_parts)))


@dataclass
class _CellBuilder:
    text_parts: list[str] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)
    current_link: _LinkBuilder | None = None

    def freeze(self) -> Cell:
        if self.current_link is not None:
            self.links.append(self.current_link.freeze())
            self.current_link = None
        return Cell(_clean_text("".join(self.text_parts)), tuple(self.links))


@dataclass
class _TableBuilder:
    rows: list[Row] = field(default_factory=list)
    current_cells: list[Cell] | None = None
    current_cell: _CellBuilder | None = None


class _TableParser(HTMLParser):
    """Small dependency-free parser for the two tabular GePNIC pages."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[Table] = []
        self._stack: list[_TableBuilder] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attributes = {key.lower(): value or "" for key, value in attrs}
        if tag == "table":
            self._stack.append(_TableBuilder())
            return
        if not self._stack:
            return
        table = self._stack[-1]
        if tag == "tr":
            table.current_cells = []
            table.current_cell = None
        elif tag in {"td", "th"} and table.current_cells is not None:
            table.current_cell = _CellBuilder()
        elif tag == "a" and table.current_cell is not None:
            table.current_cell.current_link = _LinkBuilder(
                href=attributes.get("href", ""),
                title=attributes.get("title", ""),
            )
        elif tag == "br" and table.current_cell is not None:
            table.current_cell.text_parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if not self._stack:
            return
        table = self._stack[-1]
        if tag == "a" and table.current_cell is not None:
            link = table.current_cell.current_link
            if link is not None:
                table.current_cell.links.append(link.freeze())
                table.current_cell.current_link = None
        elif tag in {"td", "th"} and table.current_cell is not None:
            if table.current_cells is not None:
                table.current_cells.append(table.current_cell.freeze())
            table.current_cell = None
        elif tag == "tr" and table.current_cells is not None:
            if table.current_cell is not None:
                table.current_cells.append(table.current_cell.freeze())
                table.current_cell = None
            table.rows.append(Row(tuple(table.current_cells)))
            table.current_cells = None
        elif tag == "table":
            complete = self._stack.pop()
            self.tables.append(Table(tuple(complete.rows)))

    def handle_data(self, data: str) -> None:
        if not self._stack:
            return
        cell = self._stack[-1].current_cell
        if cell is None:
            return
        cell.text_parts.append(data)
        if cell.current_link is not None:
            cell.current_link.text_parts.append(data)


def _parse_tables(document: str) -> list[Table]:
    parser = _TableParser()
    parser.feed(document)
    parser.close()
    return parser.tables


def _origin(url: str) -> tuple[str, str]:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise CrawlerError(f"source URL is not an HTTP(S) URL: {url!r}")
    return parsed.scheme.lower(), parsed.netloc.lower()


def _same_origin_url(base_url: str, href: str) -> str:
    absolute = urllib.parse.urljoin(base_url, href)
    if _origin(absolute) != _origin(base_url):
        raise CrawlerError(f"refusing a cross-origin GePNIC link: {absolute}")
    return absolute


@dataclass(frozen=True)
class OrganisationLink:
    name: str
    tender_count: int
    url: str


def _header_index(table: Table, required: dict[str, tuple[str, ...]]) -> tuple[int, dict[str, int]] | None:
    for row_index, row in enumerate(table.rows):
        labels = [_normalise_label(cell.text) for cell in row.cells]
        mapping: dict[str, int] = {}
        for field_name, accepted_labels in required.items():
            match = next(
                (
                    index
                    for index, label in enumerate(labels)
                    if any(label == candidate or label.startswith(candidate + " ")
                           for candidate in accepted_labels)
                ),
                None,
            )
            if match is None:
                break
            mapping[field_name] = match
        if len(mapping) == len(required):
            return row_index, mapping
    return None


_ORGANISATION_HEADERS = {
    "name": ("organisation name", "organization name"),
    "count": ("tender count",),
}


def parse_organisation_index(document: str, source_url: str) -> list[OrganisationLink]:
    """Parse every public, non-CAPTCHA organisation listing link."""
    links: list[OrganisationLink] = []
    for table in _parse_tables(document):
        located = _header_index(table, _ORGANISATION_HEADERS)
        if located is None:
            continue
        header_row, columns = located
        for row in table.rows[header_row + 1 :]:
            if len(row.cells) <= max(columns.values()):
                continue
            name = _clean_text(row.cells[columns["name"]].text)
            count_text = _clean_text(row.cells[columns["count"]].text)
            if not name and not count_text:
                continue
            if not count_text.isdigit():
                raise CrawlerError(
                    f"invalid tender count {count_text!r} for organisation {name!r}"
                )
            count = int(count_text)
            public_links = [
                link for link in row.cells[columns["count"]].links
                if "FrontEndTendersByOrganisation" in link.href
            ]
            if count and len(public_links) != 1:
                raise CrawlerError(
                    f"expected one public listing link for {name!r}, found {len(public_links)}"
                )
            if not count:
                continue
            links.append(
                OrganisationLink(
                    name=name,
                    tender_count=count,
                    url=_same_origin_url(source_url, public_links[0].href),
                )
            )
        break
    if not links:
        raise CrawlerError("GePNIC organisation directory table was not found or was empty")
    return links


_TENDER_HEADERS = {
    "published": ("e published date", "published date"),
    "closing": ("closing date",),
    "opening": ("opening date",),
    "title": ("title and ref no tender id", "title and ref no"),
    "organisation": ("organisation chain", "organization chain"),
}


@dataclass(frozen=True)
class TenderRow:
    published_at: str
    closing_at: str
    opening_at: str
    title: str
    tender_reference: str
    tender_id: str
    organisation_chain: str
    detail_url: str
    listing_url: str
    road_surface_scope: bool

    @property
    def organisation_path(self) -> list[str]:
        return [part.strip() for part in self.organisation_chain.split("||") if part.strip()]

    @property
    def root_organisation(self) -> str:
        path = self.organisation_path
        return path[0] if path else ""

    def as_notice(
        self, *, source_name: str, state_code: str, retrieved_at: str
    ) -> dict[str, object]:
        if not self.road_surface_scope:
            raise CrawlerError("attempted to emit a tender outside strict road-surface scope")
        return {
            "lifecycle": LIFECYCLE,
            "source_name": source_name,
            "state_code": state_code,
            "retrieved_at": retrieved_at,
            "title": self.title,
            "tender_reference": self.tender_reference,
            "tender_id": self.tender_id,
            "published_at": self.published_at,
            "closing_at": self.closing_at,
            "opening_at": self.opening_at,
            "organisation_chain": self.organisation_chain,
            "organisation_path": self.organisation_path,
            "scope": "road_surface",
            "listing_url": self.listing_url,
            "detail_url": self.detail_url,
            "source_url": self.detail_url,
        }


def _parse_gepnic_date(value: str, field_name: str, tender_id: str) -> str:
    try:
        parsed = datetime.strptime(value, DATE_FORMAT).replace(tzinfo=INDIA_TIMEZONE)
    except ValueError as error:
        raise CrawlerError(
            f"invalid {field_name} {value!r} for tender {tender_id or '<unknown>'}"
        ) from error
    return parsed.isoformat(timespec="seconds")


def _captcha_text(document: str) -> bool:
    text = _normalise_label(re.sub(r"<[^>]+>", " ", document))
    return "enter captcha" in text or "provide captcha" in text or "invalid captcha" in text


def _title_fields(cell: Cell, listing_url: str) -> tuple[str, str, str, str]:
    detail_links = [
        link for link in cell.links
        if "view tender information" in link.title.lower()
        or "FrontEndViewTender" in link.href
    ]
    if len(detail_links) != 1:
        raise CrawlerError(f"expected one tender detail link, found {len(detail_links)}")
    detail_link = detail_links[0]
    linked_title = _clean_text(detail_link.text)
    title = linked_title[1:-1].strip() if linked_title.startswith("[") and linked_title.endswith("]") else linked_title
    offset = cell.text.find(linked_title)
    remainder = cell.text[offset + len(linked_title) :] if offset >= 0 else cell.text
    bracketed = re.findall(r"\[([^\[\]]*)\]", remainder)
    if len(bracketed) < 2:
        raise CrawlerError(f"tender reference and ID were not found after title {title!r}")
    tender_reference, tender_id = map(_clean_text, bracketed[-2:])
    if not title or not tender_reference or not tender_id:
        raise CrawlerError(f"incomplete tender identity after title {title!r}")
    return title, tender_reference, tender_id, _same_origin_url(listing_url, detail_link.href)


def parse_tender_listing(document: str, listing_url: str) -> list[TenderRow]:
    """Parse all rows in an organisation's public active-tender listing."""
    located_tables: list[tuple[Table, int, dict[str, int]]] = []
    for table in _parse_tables(document):
        located = _header_index(table, _TENDER_HEADERS)
        if located is not None:
            located_tables.append((table, located[0], located[1]))
    if not located_tables:
        if _captcha_text(document):
            raise CrawlerError(
                "GePNIC presented a CAPTCHA instead of the public organisation listing; "
                "this crawler will not bypass it"
            )
        raise CrawlerError("GePNIC tender listing table was not found")

    parsed_rows: list[TenderRow] = []
    for table, header_row, columns in located_tables:
        for row in table.rows[header_row + 1 :]:
            if not row.cells or not any(cell.text for cell in row.cells):
                continue
            if len(row.cells) <= max(columns.values()):
                raise CrawlerError("malformed row in GePNIC tender listing")
            title, tender_reference, tender_id, detail_url = _title_fields(
                row.cells[columns["title"]], listing_url
            )
            organisation_chain = _clean_text(row.cells[columns["organisation"]].text)
            if not organisation_chain:
                raise CrawlerError(f"missing organisation chain for tender {tender_id}")
            parsed_rows.append(
                TenderRow(
                    published_at=_parse_gepnic_date(
                        row.cells[columns["published"]].text, "published date", tender_id
                    ),
                    closing_at=_parse_gepnic_date(
                        row.cells[columns["closing"]].text, "closing date", tender_id
                    ),
                    opening_at=_parse_gepnic_date(
                        row.cells[columns["opening"]].text, "opening date", tender_id
                    ),
                    title=title,
                    tender_reference=tender_reference,
                    tender_id=tender_id,
                    organisation_chain=organisation_chain,
                    detail_url=detail_url,
                    listing_url=listing_url,
                    road_surface_scope=is_road_surface_contract(title, tender_reference),
                )
            )
    return parsed_rows


def compile_allowlist(expressions: Sequence[str]) -> tuple[re.Pattern[str], ...]:
    if not expressions:
        raise CrawlerError("at least one --organisation-regex is required")
    patterns: list[re.Pattern[str]] = []
    for expression in expressions:
        try:
            patterns.append(re.compile(expression, re.IGNORECASE))
        except re.error as error:
            raise CrawlerError(f"invalid organisation regex {expression!r}: {error}") from error
    return tuple(patterns)


def organisation_allowed(name: str, allowlist: Sequence[re.Pattern[str]]) -> bool:
    """Regexes are full matches so a short substring cannot expand scope accidentally."""
    return any(pattern.fullmatch(_clean_text(name)) is not None for pattern in allowlist)


class GePNICSession:
    """One cookie-preserving, same-origin session for all public GePNIC GETs."""

    def __init__(self, source_url: str, timeout: float = 60.0) -> None:
        self.source_url = source_url
        self.source_origin = _origin(source_url)
        self.timeout = timeout
        self.cookie_jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookie_jar)
        )

    def get(self, url: str, *, referer: str | None = None) -> str:
        if _origin(url) != self.source_origin:
            raise CrawlerError(f"refusing a cross-origin GePNIC request: {url}")
        headers = {
            "Accept": "text/html,application/xhtml+xml",
            "User-Agent": USER_AGENT,
        }
        if referer:
            headers["Referer"] = referer
        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                final_url = response.geturl()
                if _origin(final_url) != self.source_origin:
                    raise CrawlerError(f"refusing a cross-origin GePNIC redirect: {final_url}")
                content_type = response.headers.get_content_charset() or "utf-8"
                return response.read().decode(content_type, errors="replace")
        except (OSError, urllib.error.URLError) as error:
            raise CrawlerError(f"failed to fetch {url}: {error}") from error


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _deduplicate(rows: Iterable[TenderRow]) -> list[TenderRow]:
    by_id: dict[str, TenderRow] = {}
    ordered: list[TenderRow] = []
    for row in rows:
        previous = by_id.get(row.tender_id)
        if previous is None:
            by_id[row.tender_id] = row
            ordered.append(row)
        elif previous != row:
            raise CrawlerError(f"conflicting duplicate GePNIC tender ID: {row.tender_id}")
    return ordered


def normalise_output(
    rows: Iterable[TenderRow],
    *,
    source_name: str = DEFAULT_SOURCE_NAME,
    source_url: str,
    retrieved_at: str,
    state_code: str,
    allowlist: Sequence[re.Pattern[str]],
    selected_organisations: Iterable[str] = (),
) -> dict[str, object]:
    source_name = _clean_text(source_name)
    if not source_name:
        raise CrawlerError("source name must not be empty")
    selected_rows = [
        row for row in rows
        if organisation_allowed(row.root_organisation, allowlist)
    ]
    selected_rows = _deduplicate(selected_rows)
    eligible = [row for row in selected_rows if row.road_surface_scope]
    organisations = {
        _clean_text(name) for name in selected_organisations if _clean_text(name)
    }
    organisations.update(row.root_organisation for row in selected_rows)
    return {
        "format": "gepnic-road-surface-procurement-notices",
        "schema_version": 1,
        "source_name": source_name,
        "source_url": source_url,
        "retrieved_at": retrieved_at,
        "state_code": state_code,
        "lifecycle": LIFECYCLE,
        "organisations": sorted(organisations, key=str.casefold),
        "rows_scanned": len(selected_rows),
        "rows_excluded_by_scope": len(selected_rows) - len(eligible),
        "notices": [
            row.as_notice(
                source_name=source_name,
                state_code=state_code,
                retrieved_at=retrieved_at,
            )
            for row in eligible
        ],
    }


SessionFactory = Callable[[str, float], GePNICSession]


def crawl_live(
    *,
    source_name: str = DEFAULT_SOURCE_NAME,
    source_url: str,
    state_code: str,
    allowlist: Sequence[re.Pattern[str]],
    timeout: float,
    request_delay: float,
    retrieved_at: str | None = None,
    session_factory: SessionFactory = GePNICSession,
) -> dict[str, object]:
    """Fetch the index and selected listings through one session/cookie jar."""
    session = session_factory(source_url, timeout)
    index_document = session.get(source_url)
    organisation_links = parse_organisation_index(index_document, source_url)
    selected = [
        link for link in organisation_links
        if organisation_allowed(link.name, allowlist)
    ]
    if not selected:
        raise CrawlerError("no GePNIC organisations matched the explicit regex allowlist")

    rows: list[TenderRow] = []
    for index, organisation in enumerate(selected):
        if index and request_delay:
            time.sleep(request_delay)
        document = session.get(organisation.url, referer=source_url)
        organisation_rows = parse_tender_listing(document, organisation.url)
        unexpected = sorted({
            row.root_organisation for row in organisation_rows
            if not organisation_allowed(row.root_organisation, allowlist)
        })
        if unexpected:
            raise CrawlerError(
                f"listing for {organisation.name!r} contained non-allowlisted roots: {unexpected}"
            )
        rows.extend(organisation_rows)

    return normalise_output(
        rows,
        source_name=source_name,
        source_url=source_url,
        retrieved_at=retrieved_at or _utc_timestamp(),
        state_code=state_code,
        allowlist=allowlist,
        selected_organisations=(link.name for link in selected),
    )


def parse_offline(
    paths: Sequence[Path],
    *,
    source_name: str = DEFAULT_SOURCE_NAME,
    source_url: str,
    state_code: str,
    allowlist: Sequence[re.Pattern[str]],
    retrieved_at: str | None = None,
) -> dict[str, object]:
    """Parse saved organisation tender listings without constructing a network session."""
    rows: list[TenderRow] = []
    for path in paths:
        try:
            document = path.read_text(encoding="utf-8", errors="replace")
        except OSError as error:
            raise CrawlerError(f"failed to read offline HTML {path}: {error}") from error
        rows.extend(parse_tender_listing(document, source_url))
    output = normalise_output(
        rows,
        source_name=source_name,
        source_url=source_url,
        retrieved_at=retrieved_at or _utc_timestamp(),
        state_code=state_code,
        allowlist=allowlist,
    )
    if not output["rows_scanned"]:
        raise CrawlerError("offline listings contained no allowlisted organisation rows")
    return output


def _state_code(value: str) -> str:
    value = value.strip().upper()
    if not re.fullmatch(r"[A-Z]{2}", value):
        raise argparse.ArgumentTypeError("state code must contain exactly two letters")
    return value


def _source_name(value: str) -> str:
    value = _clean_text(value)
    if not value:
        raise argparse.ArgumentTypeError("source name must not be empty")
    return value


def _nonnegative_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a number") from error
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return parsed


def _retrieved_at(value: str) -> str:
    value = value.strip()
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "retrieved timestamp must use YYYY-MM-DDTHH:MM:SSZ"
        ) from error
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--organisation-regex",
        action="append",
        required=True,
        help=(
            "case-insensitive full-match allowlist regex for an organisation; "
            "repeat for more organisations"
        ),
    )
    parser.add_argument("--state-code", required=True, type=_state_code)
    parser.add_argument(
        "--source-name",
        default=DEFAULT_SOURCE_NAME,
        type=_source_name,
        help="official portal name recorded in output provenance",
    )
    parser.add_argument(
        "--organisation-url",
        default=ORGANISATION_URL,
        help="public GePNIC Tenders by Organisation URL",
    )
    parser.add_argument(
        "--input-organisation-html",
        action="append",
        type=Path,
        default=[],
        help=(
            "saved organisation-specific tender-listing HTML; repeatable and fully "
            "offline (public directory links are not followed in this mode)"
        ),
    )
    parser.add_argument("--timeout", type=_nonnegative_float, default=60.0)
    parser.add_argument(
        "--retrieved-at",
        type=_retrieved_at,
        help="fixed UTC snapshot timestamp in YYYY-MM-DDTHH:MM:SSZ form",
    )
    parser.add_argument(
        "--request-delay",
        type=_nonnegative_float,
        default=0.25,
        help="seconds between organisation listing GETs (default: 0.25)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="JSON destination; omit to write to stdout",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        allowlist = compile_allowlist(args.organisation_regex)
        if args.input_organisation_html:
            output = parse_offline(
                args.input_organisation_html,
                source_name=args.source_name,
                source_url=args.organisation_url,
                state_code=args.state_code,
                allowlist=allowlist,
                retrieved_at=args.retrieved_at,
            )
        else:
            output = crawl_live(
                source_name=args.source_name,
                source_url=args.organisation_url,
                state_code=args.state_code,
                allowlist=allowlist,
                timeout=args.timeout,
                request_delay=args.request_delay,
                retrieved_at=args.retrieved_at,
            )
        rendered = json.dumps(output, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            args.output.write_text(rendered, encoding="utf-8")
        else:
            sys.stdout.write(rendered)
    except (CrawlerError, OSError) as error:
        print(f"GePNIC pull failed: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
