"""Bol.com adapter — uses curl_cffi to bypass Akamai bot protection.

Bol.com uses Akamai for bot detection which blocks standard httpx/requests
based on TLS fingerprint. curl_cffi impersonates Chrome's TLS stack.

Three strategies for product data (tried in order):
1. Prijsoverzicht page — lightweight price-comparison page with all signals
   in __reactRouterContext: purchaseType, price, revisionId, offerUid, name.
2. Direct product pages — JSON-LD (name, price, avail, seller).
3. Search fallback — extract data from search results by product ID.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

import httpx
from curl_cffi import requests as cffi_requests

from monitor.scraper import ProductData
from monitor.shops.base import ShopAdapter, parse_json_ld_product, availability_from_schema_url

logger = logging.getLogger(__name__)

RE_REVISION_ID = re.compile(r'\\?"revisionId\\?"\s*:\s*\\?"([a-f0-9-]+)\\?"')
RE_OFFER_UID = re.compile(r'\\?"offerUid\\?"\s*:\s*\\?"([a-f0-9-]+)\\?"')
RE_PRODUCT_ID = re.compile(r'/nl/nl/p/[^"]*?/(\d{7,})/')
RE_PURCHASE_TYPE = re.compile(r'"purchaseType"\s*:\s*"([^"]+)"')
RE_AMOUNT = re.compile(r'"amount"\s*:\s*"?(\d+(?:\.\d+)?)"?')
RE_NAME = re.compile(r'"name"\s*:\s*"([^"]{5,120})"')

PRIJSOVERZICHT_SLUG = "ppmonitor"

# Cookie file path (optional — fallback if DB has no cookies)
_COOKIE_FILE = Path(__file__).resolve().parent.parent.parent / "bol_cookies.json"

# Shared session
_session: cffi_requests.Session | None = None
_session_ready: bool = False
# StateManager reference, set by poller before first fetch
_state_manager = None


def set_state_manager(state) -> None:
    """Called by poller to give bol adapter access to DB cookies."""
    global _state_manager
    _state_manager = state


def _get_session() -> cffi_requests.Session:
    global _session, _session_ready
    if _session is None:
        from config import settings
        _session = cffi_requests.Session(impersonate="chrome131")
        if settings.bol_proxy_url:
            _session.proxies = {"https": settings.bol_proxy_url, "http": settings.bol_proxy_url}
            logger.info("Bol.com using proxy: %s", settings.bol_proxy_url.split("@")[-1] if "@" in settings.bol_proxy_url else "configured")
        _load_cookies_from_file(_session)
    return _session


def _load_cookies_from_file(session: cffi_requests.Session) -> None:
    """Load Akamai cookies from bol_cookies.json (fallback if DB empty)."""
    global _session_ready
    if not _COOKIE_FILE.exists():
        logger.debug("No bol_cookies.json found — will try DB or search fallback")
        return
    try:
        cookies_data = json.loads(_COOKIE_FILE.read_text(encoding="utf-8"))
        for c in cookies_data:
            session.cookies.set(
                c["name"], c["value"],
                domain=c.get("domain", ".bol.com"),
            )
        _session_ready = True
        logger.info("Loaded %d Akamai cookies from bol_cookies.json", len(cookies_data))
    except Exception:
        logger.warning("Failed to load bol_cookies.json", exc_info=True)


async def _load_cookies_from_db(session: cffi_requests.Session) -> bool:
    """Load cookies from DB. Returns True if cookies were loaded."""
    global _session_ready
    if _state_manager is None:
        return False
    try:
        cookies = await _state_manager.get_shop_cookies("bol")
        if not cookies:
            return False
        for c in cookies:
            session.cookies.set(
                c["cookie_name"], c["cookie_value"],
                domain=c.get("domain", ".bol.com"),
            )
        _session_ready = True
        logger.info("Loaded %d Akamai cookies from DB", len(cookies))
        return True
    except Exception:
        logger.warning("Failed to load cookies from DB", exc_info=True)
        return False


async def _ensure_session() -> None:
    """Ensure the session has valid cookies — DB first, then file, then warmup."""
    global _session_ready
    if _session_ready:
        return
    session = _get_session()

    # Try DB cookies first
    if not _session_ready:
        await _load_cookies_from_db(session)

    if _session_ready:
        return

    # Warmup via allowed category page (robots.txt: Allow /*/c/*/*+8299/$)
    try:
        resp = session.get(
            "https://www.bol.com/nl/nl/l/pokemon-kaarten/N/8299+16410/",
            timeout=15,
        )
        if resp.status_code == 200 and len(resp.text) > 10000:
            _session_ready = True
            logger.info("Bol.com session warmed via category page")
        else:
            logger.warning("Bol.com warmup: HTTP %d (%d bytes)", resp.status_code, len(resp.text))
    except Exception:
        logger.warning("Bol.com session warmup failed")


class BolAdapter(ShopAdapter):
    shop_id = "bol"
    base_url = "https://www.bol.com"

    def parse_product(self, html: str, url: str = "") -> ProductData:
        """Parse a product page — JSON-LD primary."""
        json_ld = parse_json_ld_product(html)

        product_id = ""
        name = None
        price = None
        availability = "Unknown"
        seller = None

        if json_ld:
            product_id = json_ld.get("productID", "")
            name = json_ld.get("name")
            offers = json_ld.get("offers", {})
            if isinstance(offers, dict):
                price = offers.get("price")
                if price is not None:
                    price = str(price)
                availability = availability_from_schema_url(
                    offers.get("availability", "")
                )
                seller_obj = offers.get("seller", {})
                if isinstance(seller_obj, dict):
                    seller = seller_obj.get("name")

        revision_match = RE_REVISION_ID.search(html)
        revision_id = revision_match.group(1) if revision_match else None

        offer_match = RE_OFFER_UID.search(html)
        offer_uid = offer_match.group(1) if offer_match else None

        return ProductData(
            product_id=product_id,
            name=name,
            price=price,
            availability=availability,
            offer_uid=offer_uid,
            revision_id=revision_id,
            seller=seller,
        )

    def parse_prijsoverzicht(self, html: str, url: str = "") -> ProductData | None:
        """Parse a prijsoverzicht page — extracts data from __reactRouterContext."""
        purchase_match = RE_PURCHASE_TYPE.search(html)
        purchase_type = purchase_match.group(1) if purchase_match else None

        price_match = RE_AMOUNT.search(html)
        price = price_match.group(1) if price_match else None

        revision_match = RE_REVISION_ID.search(html)
        revision_id = revision_match.group(1) if revision_match else None

        offer_match = RE_OFFER_UID.search(html)
        offer_uid = offer_match.group(1) if offer_match else None

        name_match = RE_NAME.search(html)
        name = name_match.group(1) if name_match else None

        # Extract product ID from URL
        pid_match = re.search(r"(\d{7,})", url)
        product_id = pid_match.group(1) if pid_match else ""

        if not revision_id and not price:
            return None

        if purchase_type == "STANDARD":
            availability = "InStock"
        elif purchase_type:
            availability = "OutOfStock"
        else:
            availability = "Unknown"

        return ProductData(
            product_id=product_id,
            name=name,
            price=price,
            availability=availability,
            offer_uid=offer_uid,
            revision_id=revision_id,
            seller="bol" if purchase_type == "STANDARD" else None,
        )

    def parse_category(self, html: str) -> set[str]:
        return set(RE_PRODUCT_ID.findall(html))

    def build_product_url(self, product_id: str) -> str:
        return f"{self.base_url}/nl/nl/prijsoverzicht/{PRIJSOVERZICHT_SLUG}/{product_id}/"

    def build_direct_product_url(self, product_id: str) -> str:
        """Build the regular product page URL (fallback)."""
        return f"{self.base_url}/nl/nl/p/-/{product_id}/"

    def build_category_urls(self) -> list[str]:
        """Category pages allowed by robots.txt (Allow: /*/c/*/*+8299/$)."""
        return [
            f"{self.base_url}/nl/nl/l/pokemon-kaarten/N/8299+16410/",
            f"{self.base_url}/nl/nl/l/pokemon-kaarten/N/8299+16410/?sortering=4",
        ]

    def get_search_url(self, term: str) -> str:
        # Search URLs (/s/) are disallowed by robots.txt — use category instead
        return f"{self.base_url}/nl/nl/l/pokemon-kaarten/N/8299+16410/"

    async def fetch_product(
        self, client: httpx.AsyncClient, url: str
    ) -> ProductData:
        """Fetch product data via curl_cffi.

        Strategy order:
        1. Prijsoverzicht page (lightweight, has all signals)
        2. Direct product page (JSON-LD, full data)
        """
        start = time.monotonic()
        await _ensure_session()
        session = _get_session()

        # Extract product ID from any bol.com URL format
        pid_match = re.search(r"(\d{7,})", url)
        pid = pid_match.group(1) if pid_match else ""

        try:
            # Strategy 1: Prijsoverzicht page
            prijs_url = self.build_product_url(pid) if pid else url
            resp = session.get(prijs_url, timeout=15)
            latency_ms = int((time.monotonic() - start) * 1000)

            if resp.status_code == 200 and len(resp.text) >= 5000:
                data = self.parse_prijsoverzicht(resp.text, url=prijs_url)
                if data and (data.revision_id or data.price):
                    data.latency_ms = latency_ms
                    if not data.product_id and pid:
                        data.product_id = pid
                    logger.debug("Prijsoverzicht OK for %s", pid)
                    return data

            # Strategy 2: Direct product page (if prijsoverzicht failed)
            if pid:
                direct_url = self.build_direct_product_url(pid)
                resp = session.get(direct_url, timeout=15)
                latency_ms = int((time.monotonic() - start) * 1000)

                if resp.status_code == 200 and len(resp.text) >= 5000:
                    data = self.parse_product(resp.text, url=direct_url)
                    data.latency_ms = latency_ms
                    if not data.product_id:
                        data.product_id = pid
                    logger.debug("Direct product page OK for %s", pid)
                    return data

            # Both strategies failed — reset session
            global _session_ready, _session
            _session_ready = False
            _session = None
            raise httpx.HTTPStatusError(
                "All fetch strategies failed (prijsoverzicht, direct)",
                request=httpx.Request("GET", url),
                response=httpx.Response(403),
            )

        except httpx.HTTPStatusError:
            raise
        except Exception as exc:
            raise httpx.HTTPStatusError(
                f"curl_cffi error: {exc}",
                request=httpx.Request("GET", url),
                response=httpx.Response(500),
            ) from exc

    async def fetch_category(
        self, client: httpx.AsyncClient, url: str
    ) -> set[str]:
        """Fetch search/category page via curl_cffi."""
        await _ensure_session()
        try:
            session = _get_session()
            resp = session.get(url, timeout=15)
            if resp.status_code != 200:
                logger.warning("Bol.com category HTTP %d", resp.status_code)
                return set()
            return self.parse_category(resp.text)
        except Exception:
            logger.exception("Bol.com category fetch failed: %s", url)
            return set()

    async def diagnose(self) -> dict:
        """Run diagnostic fetch sequence and return raw debug info."""
        from config import settings
        debug: dict = {}
        await _ensure_session()
        session = _get_session()

        debug["session_ready"] = _session_ready
        debug["cookies_count"] = len(session.cookies)
        debug["proxy"] = bool(settings.bol_proxy_url)

        # Category page (robots.txt allowed: /*/c/*/*+8299/$)
        cat_url = self.build_category_urls()[0]
        debug["category_url"] = cat_url
        product_ids: set[str] = set()
        try:
            resp = session.get(cat_url, timeout=15)
            debug["category_status"] = resp.status_code
            debug["category_body_length"] = len(resp.text)
            debug["category_body_snippet"] = resp.text[:200]
            debug["category_akamai_blocked"] = len(resp.text) < 5000
            product_ids = self.parse_category(resp.text)
            debug["category_product_ids_found"] = len(product_ids)
        except Exception as exc:
            debug["category_error"] = f"{type(exc).__name__}: {exc}"

        # Test prijsoverzicht + direct product page
        if product_ids:
            pid = next(iter(product_ids))

            # Prijsoverzicht
            prijs_url = self.build_product_url(pid)
            debug["prijsoverzicht_url"] = prijs_url
            try:
                resp = session.get(prijs_url, timeout=15)
                debug["prijs_status"] = resp.status_code
                debug["prijs_body_length"] = len(resp.text)
                debug["prijs_body_snippet"] = resp.text[:200]
                debug["prijs_akamai_blocked"] = len(resp.text) < 5000
                if len(resp.text) >= 5000:
                    data = self.parse_prijsoverzicht(resp.text, url=prijs_url)
                    if data:
                        debug["prijs_name"] = data.name
                        debug["prijs_price"] = data.price
                        debug["prijs_availability"] = data.availability
                        debug["prijs_revision_id"] = data.revision_id
                        debug["prijs_offer_uid"] = data.offer_uid
                        pt_match = RE_PURCHASE_TYPE.search(resp.text)
                        debug["prijs_purchase_type"] = pt_match.group(1) if pt_match else None
                    else:
                        debug["prijs_parse"] = "failed — no revision_id or price found"
            except Exception as exc:
                debug["prijs_error"] = f"{type(exc).__name__}: {exc}"

            # Direct product page (for comparison)
            direct_url = self.build_direct_product_url(pid)
            debug["direct_product_url"] = direct_url
            try:
                resp = session.get(direct_url, timeout=15)
                debug["direct_status"] = resp.status_code
                debug["direct_body_length"] = len(resp.text)
                debug["direct_akamai_blocked"] = len(resp.text) < 5000
            except Exception as exc:
                debug["direct_error"] = f"{type(exc).__name__}: {exc}"

        return debug
