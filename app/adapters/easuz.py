from __future__ import annotations

import asyncio
import json
import re
import socket
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Iterable
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup, Tag

from app.models import CategorySnapshot, Tender


BASE_URL = "https://easuz.mosreg.ru"
TENDER_HREF_RE = re.compile(r"^/tenders/(\d+)(?:$|[?#])")
TOTAL_RE = re.compile(r"Найдено\s+([\d\s]+)\s+закуп", re.IGNORECASE)
BLOCK_MARKERS = (
    "captcha",
    "доступ ограничен",
    "access denied",
    "слишком много запросов",
)


class EasuzError(RuntimeError):
    pass


class SourceBlocked(EasuzError):
    pass


def _detect_physical_local_ip() -> str | None:
    """Find local physical LAN IP address to bypass VPN tunnels that block RU sites."""
    try:
        ips = socket.gethostbyname_ex(socket.gethostname())[2]
        lan_ips = [ip for ip in ips if ip.startswith(("192.168.", "10."))]
        if lan_ips:
            return lan_ips[0]
        for ip in ips:
            if not ip.startswith(("127.", "172.19.")):
                return ip
    except Exception:
        pass
    return None


@dataclass(slots=True)
class EasuzConfig:
    timeout_seconds: float = 25.0
    delay_seconds: float = 0.7
    max_concurrency: int = 3
    local_address: str | None = None
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    )


class EasuzAdapter:
    """Read-only adapter for public EASUZ tender pages."""

    def __init__(self, config: EasuzConfig | None = None) -> None:
        self.config = config or EasuzConfig()
        local_addr = self.config.local_address or _detect_physical_local_ip()
        transport = httpx.AsyncHTTPTransport(local_address=local_addr) if local_addr else None

        self._client = httpx.AsyncClient(
            base_url=BASE_URL,
            timeout=self.config.timeout_seconds,
            follow_redirects=True,
            transport=transport,
            headers={
                "User-Agent": self.config.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.7",
            },
        )
        self._sem = asyncio.Semaphore(self.config.max_concurrency)

    async def __aenter__(self) -> "EasuzAdapter":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    async def _get_html(self, path_or_url: str) -> str:
        async with self._sem:
            response = await self._client.get(path_or_url)
            if response.status_code == 429:
                raise SourceBlocked("EASUZ returned HTTP 429")
            response.raise_for_status()
            text = response.text
            lowered = text.lower()
            if any(marker in lowered for marker in BLOCK_MARKERS):
                raise SourceBlocked("EASUZ returned an access restriction page")
            if self.config.delay_seconds > 0:
                await asyncio.sleep(self.config.delay_seconds)
            return text

    async def fetch_category(self, category_id: int, page: int = 1) -> CategorySnapshot:
        path = f"/tenders/catalog/{category_id}" if page == 1 else f"/tenders/catalog/{category_id}?page={page}"
        html = await self._get_html(path)
        return self.parse_category_html(category_id, html, page=page)

    async def fetch_tender(self, url: str) -> Tender:
        html = await self._get_html(url)
        return self.parse_tender_html(urljoin(BASE_URL, url), html)

    async def fetch_category_tenders(
        self,
        category_id: int,
        *,
        limit: int | None = None,
        max_pages: int = 1,
    ) -> tuple[CategorySnapshot, list[Tender]]:
        all_urls: list[str] = []
        last_snapshot: CategorySnapshot | None = None

        for p in range(1, max_pages + 1):
            snapshot = await self.fetch_category(category_id, page=p)
            if last_snapshot is None:
                last_snapshot = snapshot
            for u in snapshot.tender_urls:
                u_str = str(u)
                if u_str not in all_urls:
                    all_urls.append(u_str)
            if limit is not None and len(all_urls) >= limit:
                all_urls = all_urls[:limit]
                break
            if not snapshot.tender_urls:
                break

        tenders = await asyncio.gather(*(self.fetch_tender(url) for url in all_urls))
        merged_snapshot = CategorySnapshot(
            category_id=category_id,
            category_url=f"{BASE_URL}/tenders/catalog/{category_id}",
            total_found=last_snapshot.total_found if last_snapshot else None,
            tender_urls=all_urls,
        )
        return merged_snapshot, list(tenders)

    @staticmethod
    def parse_category_html(category_id: int, html: str, page: int = 1) -> CategorySnapshot:
        # Fast path: extract from TransferState JSON if available
        state_data = _extract_transfer_state(html)
        total_found = None
        urls: list[str] = []
        seen: set[str] = set()

        if state_data:
            for v in state_data.values():
                if isinstance(v, dict) and v.get("u") == "/api/v1-web/Tender/GetTenderPage":
                    body = v.get("b", {})
                    pagination = body.get("pagination", {})
                    if "countTotal" in pagination:
                        total_found = int(pagination["countTotal"])
                    for obj in body.get("objects", []):
                        tender_id = obj.get("id")
                        if tender_id:
                            url = f"{BASE_URL}/tenders/{tender_id}"
                            if url not in seen:
                                seen.add(url)
                                urls.append(url)

        # Fallback / HTML parser path
        if not urls:
            soup = BeautifulSoup(html, "html.parser")
            page_text = _clean_text(soup.get_text(" ", strip=True))

            if total_found is None:
                match = TOTAL_RE.search(page_text)
                if match:
                    total_found = int(match.group(1).replace(" ", ""))

            for anchor in soup.find_all("a", href=True):
                href = str(anchor.get("href", ""))
                match = TENDER_HREF_RE.match(href)
                if not match:
                    continue
                url = urljoin(BASE_URL, f"/tenders/{match.group(1)}")
                if url not in seen:
                    seen.add(url)
                    urls.append(url)

        category_url = f"{BASE_URL}/tenders/catalog/{category_id}" if page == 1 else f"{BASE_URL}/tenders/catalog/{category_id}?page={page}"
        return CategorySnapshot(
            category_id=category_id,
            category_url=category_url,
            total_found=total_found,
            tender_urls=urls,
        )

    @staticmethod
    def parse_tender_html(url: str, html: str) -> Tender:
        external_id = _external_id_from_url(url)
        soup = BeautifulSoup(html, "html.parser")
        full_text = _clean_text(soup.get_text("\n", strip=True))

        # Check for TransferState JSON
        state_data = _extract_transfer_state(html)
        if state_data:
            for v in state_data.values():
                if isinstance(v, dict):
                    body = v.get("b")
                    if isinstance(body, dict) and "GetTenderById" in str(v.get("u")):
                        title = body.get("subjectName") or body.get("name")
                        if title:
                            cust_raw = body.get("customer")
                            customer = cust_raw.get("name") if isinstance(cust_raw, dict) else (cust_raw if isinstance(cust_raw, str) else body.get("customerName"))
                            customer_inn = cust_raw.get("inn") if isinstance(cust_raw, dict) else None
                            address = cust_raw.get("factAddress") or cust_raw.get("postAddress") if isinstance(cust_raw, dict) else None
                            status = body.get("stateName") or body.get("status")
                            eis_number = body.get("oosRegistryNumber") or body.get("registryNumber")
                            cost = body.get("cost") or body.get("price")
                            price = Decimal(str(cost)) if cost is not None else None
                            pub_str = body.get("oosPublishDate") or body.get("publishDate")
                            end_str = body.get("requestEndDate") or body.get("endDate")
                            return Tender(
                                external_id=external_id,
                                source_url=url,
                                title=_clean_text(title),
                                customer=_clean_text(customer) if customer else None,
                                customer_inn=customer_inn,
                                address=_clean_text(address) if address else None,
                                price=price,
                                budget_max=price,
                                status=_clean_text(status) if status else None,
                                eis_number=_clean_text(eis_number) if eis_number else None,
                                published_at=_parse_iso_or_custom(pub_str),
                                deadline=_parse_iso_or_custom(end_str),
                                raw_text=full_text,
                            )

        title = _extract_title(soup)
        customer = _find_labeled_value(soup, ("Заказчик",))
        status = _find_labeled_value(soup, ("Статус",))
        eis_number = _find_labeled_value(soup, ("Реестровый номер ЕИС",))

        price_text = _find_labeled_value(
            soup,
            ("Начальная цена", "Начальная (максимальная) цена", "Цена"),
            value_pattern=re.compile(r"[\d\s.,]+\s*₽"),
        )
        price = _parse_price(price_text)

        published_text = _find_labeled_value(
            soup,
            ("Размещено", "Дата размещения"),
            value_pattern=re.compile(r"\d{2}\.\d{2}\.\d{4}(?:,?\s+\d{2}:\d{2})?"),
        )
        deadline_text = _find_labeled_value(
            soup,
            ("Подать заявку до", "Окончание подачи заявок"),
            value_pattern=re.compile(r"\d{2}\.\d{2}\.\d{4}(?:,?\s+\d{2}:\d{2})?"),
        )

        return Tender(
            external_id=external_id,
            source_url=url,
            title=title,
            customer=customer,
            price=price,
            status=status,
            eis_number=eis_number,
            published_at=_parse_datetime(published_text),
            deadline=_parse_datetime(deadline_text),
            raw_text=full_text,
        )


def _extract_transfer_state(html: str) -> dict | None:
    match = re.search(r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', html, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except (json.JSONDecodeError, ValueError):
            return None
    return None


def _clean_text(text: str) -> str:
    return re.sub(r"[\t\r\f\v ]+", " ", text).strip()


def _extract_title(soup: BeautifulSoup) -> str:
    for selector in ("h1", "main h2", "article h1", "article h2"):
        tag = soup.select_one(selector)
        if tag:
            value = _clean_text(tag.get_text(" ", strip=True))
            if value and value.lower() not in {"закупки", "объект закупки"}:
                return value

    marker = soup.find(string=lambda s: bool(s and _clean_text(s) == "Объект закупки"))
    if marker:
        node = marker.parent if isinstance(marker.parent, Tag) else None
        for candidate in _following_texts(node):
            value = _clean_text(candidate)
            if value and value != "Объект закупки":
                return value

    raise EasuzError("Could not extract tender title")


def _following_texts(node: Tag | None, max_items: int = 20) -> Iterable[str]:
    if node is None:
        return []
    result: list[str] = []
    for element in node.next_elements:
        if isinstance(element, str):
            value = _clean_text(element)
            if value:
                result.append(value)
                if len(result) >= max_items:
                    break
    return result


def _find_labeled_value(
    soup: BeautifulSoup,
    labels: tuple[str, ...],
    value_pattern: re.Pattern[str] | None = None,
) -> str | None:
    label_set = {_clean_text(label).casefold() for label in labels}

    for text_node in soup.find_all(string=True):
        current = _clean_text(str(text_node))
        if current.casefold() not in label_set:
            continue

        parent = text_node.parent if isinstance(text_node.parent, Tag) else None
        if parent is None:
            continue

        for value in _following_texts(parent, max_items=16):
            cleaned = _clean_text(value)
            if not cleaned or cleaned.casefold() in label_set:
                continue
            if value_pattern:
                match = value_pattern.search(cleaned)
                if match:
                    return match.group(0)
            else:
                return cleaned

    flat = _clean_text(soup.get_text(" ", strip=True))
    if value_pattern:
        for label in labels:
            idx = flat.casefold().find(label.casefold())
            if idx >= 0:
                match = value_pattern.search(flat[idx + len(label) : idx + len(label) + 500])
                if match:
                    return match.group(0)
    return None


def _parse_price(value: str | None) -> Decimal | None:
    if not value:
        return None
    normalized = value.replace("₽", "").replace("\xa0", "").replace(" ", "")
    normalized = normalized.replace(",", ".")
    normalized = re.sub(r"[^0-9.]", "", normalized)
    if not normalized:
        return None
    try:
        return Decimal(normalized)
    except InvalidOperation:
        return None


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip().replace(",", "")
    for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            pass
    return None


def _parse_iso_or_custom(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return _parse_datetime(value)


def _external_id_from_url(url: str) -> str:
    match = re.search(r"/tenders/(\d+)", url)
    if not match:
        raise EasuzError(f"Could not extract tender id from URL: {url}")
    return match.group(1)
