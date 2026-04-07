"""Tests for shop adapters: parse_product, parse_category, registry."""

import pytest

from monitor.shops.bol import BolAdapter
from monitor.shops.mediamarkt import MediaMarktAdapter
from monitor.shops.pocketgames import PocketGamesAdapter
from monitor.shops.catchyourcards import CatchYourCardsAdapter
from monitor.shops.games_island import GamesIslandAdapter
from monitor.shops.dreamland import DreamlandAdapter
from monitor.shops.registry import SHOP_REGISTRY, get_adapter


# ---------------------------------------------------------------------------
# Bol.com
# ---------------------------------------------------------------------------

BOL_PRODUCT_HTML = """\
<html><head></head><body>
<script type="application/ld+json">
{
  "@type": "Product",
  "productID": "9300000239014079",
  "name": "Pokemon TCG Prismatic Evolutions Elite Trainer Box",
  "offers": {
    "@type": "Offer",
    "price": "59.99",
    "availability": "https://schema.org/InStock",
    "seller": {"@type": "Organization", "name": "bol"}
  }
}
</script>
<script>window.__REACT_ROUTER_CONTEXT__={"revisionId":"aaa-bbb-ccc","offerUid":"ddd-eee-fff"}</script>
</body></html>
"""

BOL_CATEGORY_HTML = """\
<html><body>
<a href="/nl/nl/p/pokemon-tcg-product-one/1234567890/">Product One</a>
<a href="/nl/nl/p/pokemon-tcg-product-two/9876543210/">Product Two</a>
<a href="/nl/nl/p/pokemon-tcg-product-two/9876543210/">Duplicate</a>
</body></html>
"""


def test_bol_parse_json_ld():
    adapter = BolAdapter()
    data = adapter.parse_product(BOL_PRODUCT_HTML)
    assert data.product_id == "9300000239014079"
    assert data.name == "Pokemon TCG Prismatic Evolutions Elite Trainer Box"
    assert data.price == "59.99"
    assert data.availability == "InStock"
    assert data.seller == "bol"
    assert data.revision_id == "aaa-bbb-ccc"
    assert data.offer_uid == "ddd-eee-fff"


def test_bol_build_product_url_prijsoverzicht():
    adapter = BolAdapter()
    url = adapter.build_product_url("9300000271683065")
    assert "prijsoverzicht/ppmonitor/9300000271683065" in url


def test_bol_parse_category():
    adapter = BolAdapter()
    ids = adapter.parse_category(BOL_CATEGORY_HTML)
    assert ids == {"1234567890", "9876543210"}


# ---------------------------------------------------------------------------
# Bol.com — prijsoverzicht parsing
# ---------------------------------------------------------------------------

BOL_PRIJSOVERZICHT_HTML = """\
<html><body>
<script>window.__REACT_ROUTER_CONTEXT__={
  "purchaseType":"STANDARD",
  "revisionId":"abc-def-123",
  "offerUid":"ddd-eee-fff",
  "name":"Pokemon TCG Perfect Order Booster Pack",
  "amount":"6.99"
}</script>
</body></html>
"""

BOL_PRIJSOVERZICHT_OOS_HTML = """\
<html><body>
<script>window.__REACT_ROUTER_CONTEXT__={
  "purchaseType":"NOT_AVAILABLE",
  "revisionId":"abc-def-789",
  "name":"Pokemon TCG Perfect Order ETB",
  "amount":"59.99"
}</script>
</body></html>
"""


def test_bol_parse_prijsoverzicht_in_stock():
    adapter = BolAdapter()
    data = adapter.parse_prijsoverzicht(
        BOL_PRIJSOVERZICHT_HTML,
        url="https://www.bol.com/nl/nl/prijsoverzicht/ppmonitor/9300000271683065/",
    )
    assert data is not None
    assert data.product_id == "9300000271683065"
    assert data.name == "Pokemon TCG Perfect Order Booster Pack"
    assert data.price == "6.99"
    assert data.availability == "InStock"
    assert data.revision_id == "abc-def-123"
    assert data.offer_uid == "ddd-eee-fff"
    assert data.seller == "bol"


def test_bol_parse_prijsoverzicht_out_of_stock():
    adapter = BolAdapter()
    data = adapter.parse_prijsoverzicht(
        BOL_PRIJSOVERZICHT_OOS_HTML,
        url="https://www.bol.com/nl/nl/prijsoverzicht/ppmonitor/9300000272060999/",
    )
    assert data is not None
    assert data.availability == "OutOfStock"
    assert data.name == "Pokemon TCG Perfect Order ETB"
    assert data.seller is None


def test_bol_parse_prijsoverzicht_empty():
    adapter = BolAdapter()
    data = adapter.parse_prijsoverzicht("<html><body>Nothing here</body></html>")
    assert data is None


# ---------------------------------------------------------------------------
# MediaMarkt
# ---------------------------------------------------------------------------

MEDIAMARKT_HTML = """\
<html><head><title>Pokemon Elite Trainer Box | MediaMarkt</title></head><body>
<meta property="product:price:amount" content="54.99">
<script>
window.__PRELOADED_STATE__ = {"Availability:Media:1234567":{"uber":null}};
</script>
</body></html>
"""


def test_mediamarkt_parse_preloaded_state():
    adapter = MediaMarktAdapter()
    data = adapter.parse_product(
        MEDIAMARKT_HTML,
        url="https://www.mediamarkt.nl/nl/product/_pokemon-elite-trainer-box_1234567.html",
    )
    assert data.product_id == "1234567"
    assert data.name == "Pokemon Elite Trainer Box"
    assert data.availability == "InStock"
    assert data.price == "54.99"


# ---------------------------------------------------------------------------
# PocketGames (Shopify)
# ---------------------------------------------------------------------------

SHOPIFY_PRODUCT_HTML = """\
<html><head></head><body>
<script type="application/ld+json">
{
  "@type": "Product",
  "productID": "shop-12345",
  "name": "Pokemon Booster Box",
  "offers": {
    "@type": "Offer",
    "price": "149.95",
    "availability": "https://schema.org/InStock"
  }
}
</script>
</body></html>
"""

SHOPIFY_CATEGORY_HTML = """\
<html><body>
<a href="/products/pokemon-booster-box">Box</a>
<a href="/products/pokemon-etb">ETB</a>
<a href="/collections/all">All</a>
</body></html>
"""


def test_shopify_parse_json_ld():
    adapter = PocketGamesAdapter()
    data = adapter.parse_product(SHOPIFY_PRODUCT_HTML)
    assert data.product_id == "shop-12345"
    assert data.name == "Pokemon Booster Box"
    assert data.price == "149.95"
    assert data.availability == "InStock"


def test_shopify_parse_category():
    adapter = PocketGamesAdapter()
    handles = adapter.parse_category(SHOPIFY_CATEGORY_HTML)
    assert handles == {"pokemon-booster-box", "pokemon-etb"}


# ---------------------------------------------------------------------------
# CatchYourCards
# ---------------------------------------------------------------------------

CYC_IN_STOCK_HTML = """\
<html><body>
<h1 class="product_title entry-title">Pokemon ETB</h1>
<span class="woocommerce-Price-amount amount"><bdi>&euro;49,99</bdi></span>
<p class="stock in-stock">Op voorraad</p>
</body></html>
"""

CYC_OUT_OF_STOCK_HTML = """\
<html><body>
<h1 class="product_title entry-title">Pokemon ETB</h1>
<p class="stock out-of-stock">Niet op voorraad</p>
</body></html>
"""


def test_catchyourcards_in_stock():
    adapter = CatchYourCardsAdapter()
    data = adapter.parse_product(
        CYC_IN_STOCK_HTML,
        url="https://catchyourcards.nl/product/pokemon-etb/",
    )
    assert data.availability == "InStock"
    assert data.name == "Pokemon ETB"
    assert data.price == "49,99"
    assert data.product_id == "pokemon-etb"


def test_catchyourcards_out_of_stock():
    adapter = CatchYourCardsAdapter()
    data = adapter.parse_product(
        CYC_OUT_OF_STOCK_HTML,
        url="https://catchyourcards.nl/product/pokemon-etb/",
    )
    assert data.availability == "OutOfStock"


# ---------------------------------------------------------------------------
# Games Island
# ---------------------------------------------------------------------------

GAMES_ISLAND_JSON_LD_HTML = """\
<html><body>
<script type="application/ld+json">
{
  "@type": "Product",
  "productID": "gi-99887",
  "name": "Pokemon Display Box",
  "offers": {
    "@type": "Offer",
    "price": "119.99",
    "availability": "https://schema.org/InStock"
  }
}
</script>
</body></html>
"""

GAMES_ISLAND_GERMAN_HTML = """\
<html><head><title>Pokemon Display | Games Island</title></head><body>
<p>Auf Lager - sofort lieferbar</p>
</body></html>
"""


def test_games_island_json_ld():
    adapter = GamesIslandAdapter()
    data = adapter.parse_product(GAMES_ISLAND_JSON_LD_HTML)
    assert data.product_id == "gi-99887"
    assert data.name == "Pokemon Display Box"
    assert data.price == "119.99"
    assert data.availability == "InStock"


def test_games_island_german_fallback():
    adapter = GamesIslandAdapter()
    data = adapter.parse_product(
        GAMES_ISLAND_GERMAN_HTML,
        url="https://games-island.eu/p/pokemon-display",
    )
    assert data.availability == "InStock"
    assert data.name == "Pokemon Display"


# ---------------------------------------------------------------------------
# Dreamland
# ---------------------------------------------------------------------------

DREAMLAND_JSON_LD_HTML = """\
<html><body>
<script type="application/ld+json">
{
  "@type": "Product",
  "productID": "dl-55443",
  "name": "Pokemon Kaarten Bundel",
  "offers": {
    "@type": "Offer",
    "price": "29.99",
    "availability": "https://schema.org/OutOfStock"
  }
}
</script>
</body></html>
"""

DREAMLAND_FALLBACK_HTML = """\
<html><body>
<h1>Pokemon Kaarten Bundel</h1>
<p class="stock">Dit product is uitverkocht</p>
</body></html>
"""


def test_dreamland_json_ld():
    adapter = DreamlandAdapter()
    data = adapter.parse_product(DREAMLAND_JSON_LD_HTML)
    assert data.product_id == "dl-55443"
    assert data.name == "Pokemon Kaarten Bundel"
    assert data.price == "29.99"
    assert data.availability == "OutOfStock"


def test_dreamland_html_fallback():
    adapter = DreamlandAdapter()
    data = adapter.parse_product(
        DREAMLAND_FALLBACK_HTML,
        url="https://www.dreamland.be/e/nl/p/12345",
    )
    assert data.availability == "OutOfStock"
    assert data.name == "Pokemon Kaarten Bundel"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_registry_all_shops():
    assert len(SHOP_REGISTRY) == 7
    expected = {
        "bol", "mediamarkt", "pocketgames", "catchyourcards",
        "games_island", "dreamland", "amazon_uk",
    }
    assert set(SHOP_REGISTRY.keys()) == expected


def test_get_adapter_unknown():
    with pytest.raises(ValueError, match="Unknown shop"):
        get_adapter("unknown")


def test_all_adapters_build_urls():
    for shop_id, cls in SHOP_REGISTRY.items():
        adapter = cls()
        url = adapter.build_product_url("test-id-123")
        assert url.startswith("http"), f"{shop_id} URL doesn't start with http: {url}"
        assert "test-id-123" in url, f"{shop_id} URL doesn't contain product id: {url}"
