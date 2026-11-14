#!/usr/bin/env python3
"""Build the demo project "beispielprojekt" for the Tabrion Studio 1.0 usability test QA-S1.

Standard library only and deterministic (fixed seed, fixed dates): running it again gives the same content.

    python3 build_demo.py [output-folder]      # default: ./beispielprojekt next to this script

Result:
    beispielprojekt/
      .tabrion/studio.toml          two project profiles: "Shop (DEV)" (dev) and "Shop (PROD)" (prod)
      alembic.ini                   script_location = %(here)s/migrations
      migrations/env.py, script.py.mako, versions/0001_..., 0002_..., 0003_...
      daten/shop.sqlite             DEV: alembic_version = 0002 (one revision pending)
      daten/shop-prod.sqlite        PROD: same data, revision 0003 applied (up to date)
      skripte/*.sql                 example scripts for the SQL editor

Vector data (for Studio's "Vektordaten" view, STU-055): revision 0002 also creates a product search index in Orkexa's
SQLite layout, plain tables that need no sqlite-vec: index_spaces (one vector space "produkte" with its embedding
identity, 8 dimensions) and index_vectors (one 8-dimensional float32 BLOB per product, 120 rows). Studio detects it as
a BLOB vector column with "exact search" and lists the embedding model.

Money is stored as INTEGER cents (*_cents columns). Percentages (discount_pct, vat_rate_pct) are TEXT holding a
decimal number with two places ('10.00'). Dates are ISO 8601 TEXT ('2025-03-14' or '2025-03-14 09:30:00').
"""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
import random
import shutil
import sqlite3
import struct
import sys
import zlib

SEED = 20251113
BASE_DATE = dt.datetime(2025, 10, 31, 18, 0, 0)  # "today" of the demo data
ORDER_START = dt.datetime(2024, 1, 1, 8, 0, 0)

N_CUSTOMERS = 200
N_PRODUCTS = 120
N_ORDERS = 1500

REV1, REV2, REV3 = "0001", "0002", "0003"

VECTOR_DIMENSIONS = 8
VECTOR_SPACE = ("shop", "produkte")  # namespace, space
VECTOR_IDENTITY = (  # Orkexa's EmbeddingIdentity.to_dict(), as JSON text; a made-up local demo model
    '{"provider": "beispiel", "model": "shop-embed-mini", "digest": "2025-10-01", '
    f'"dimensions": {VECTOR_DIMENSIONS}, "normalized": true}}'
)

# --------------------------------------------------------------------------------------------------------------------
# Schema, split by Alembic revision. The revision files are generated from these same statements, so they match.
# --------------------------------------------------------------------------------------------------------------------

REV1_DDL = [
    """CREATE TABLE categories (
    category_id INTEGER PRIMARY KEY,
    parent_id   INTEGER REFERENCES categories (category_id) ON DELETE RESTRICT,
    name        TEXT NOT NULL UNIQUE,
    slug        TEXT NOT NULL UNIQUE CHECK (slug = lower(slug) AND slug NOT LIKE '% %')
)""",
    """CREATE TABLE customers (
    customer_id  INTEGER PRIMARY KEY,
    email        TEXT NOT NULL UNIQUE CHECK (email LIKE '%_@_%._%'),
    first_name   TEXT NOT NULL,
    last_name    TEXT NOT NULL,
    phone        TEXT,                      -- NULL: no phone number given
    street       TEXT NOT NULL,
    postal_code  TEXT NOT NULL,
    city         TEXT NOT NULL,
    country      TEXT NOT NULL CHECK (country IN ('AT', 'DE')),
    birth_date   TEXT CHECK (birth_date IS NULL OR birth_date = date(birth_date)),  -- ISO date, optional
    newsletter   INTEGER NOT NULL DEFAULT 0 CHECK (newsletter IN (0, 1)),
    created_at   TEXT NOT NULL CHECK (created_at = datetime(created_at))
)""",
    """CREATE TABLE products (
    product_id     INTEGER PRIMARY KEY,
    sku            TEXT NOT NULL UNIQUE,
    category_id    INTEGER NOT NULL REFERENCES categories (category_id),
    name           TEXT NOT NULL,
    description    TEXT,
    price_cents    INTEGER NOT NULL CHECK (price_cents > 0),            -- gross price in euro cents
    vat_rate_pct   TEXT NOT NULL DEFAULT '20.00' CHECK (vat_rate_pct IN ('10.00', '13.00', '20.00')),  -- decimal text
    weight_grams   INTEGER CHECK (weight_grams IS NULL OR weight_grams > 0),
    stock          INTEGER NOT NULL DEFAULT 0 CHECK (stock >= 0),
    active         INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    thumbnail      BLOB,                                               -- small PNG placeholder, NULL for some
    created_at     TEXT NOT NULL
)""",
    "CREATE INDEX ix_products_category ON products (category_id)",
    """CREATE TABLE orders (
    order_id        INTEGER PRIMARY KEY,
    customer_id     INTEGER NOT NULL REFERENCES customers (customer_id),
    order_date      TEXT NOT NULL CHECK (order_date = datetime(order_date)),
    status          TEXT NOT NULL CHECK (status IN ('pending', 'paid', 'shipped', 'delivered', 'cancelled', 'returned')),
    currency        TEXT NOT NULL DEFAULT 'EUR' CHECK (currency = 'EUR'),
    shipping_cents  INTEGER NOT NULL DEFAULT 0 CHECK (shipping_cents >= 0),
    total_cents     INTEGER NOT NULL CHECK (total_cents >= 0),          -- items after discount + shipping
    voucher_code    TEXT,                                               -- NULL: no voucher
    note            TEXT                                                -- customer's note, mostly NULL
)""",
    "CREATE INDEX ix_orders_customer ON orders (customer_id)",
    "CREATE INDEX ix_orders_date ON orders (order_date)",
    """CREATE TABLE order_items (
    order_id          INTEGER NOT NULL REFERENCES orders (order_id) ON DELETE CASCADE,
    line_no           INTEGER NOT NULL CHECK (line_no >= 1),
    product_id        INTEGER NOT NULL REFERENCES products (product_id),
    quantity          INTEGER NOT NULL CHECK (quantity BETWEEN 1 AND 99),
    unit_price_cents  INTEGER NOT NULL CHECK (unit_price_cents > 0),
    discount_pct      TEXT NOT NULL DEFAULT '0.00'
                      CHECK (CAST(discount_pct AS REAL) >= 0 AND CAST(discount_pct AS REAL) <= 50),  -- decimal text
    PRIMARY KEY (order_id, line_no)
)""",
    "CREATE INDEX ix_order_items_product ON order_items (product_id)",
]

REV2_DDL = [
    """CREATE TABLE payments (
    payment_id    INTEGER PRIMARY KEY,
    order_id      INTEGER NOT NULL REFERENCES orders (order_id),
    method        TEXT NOT NULL CHECK (method IN ('Kreditkarte', 'PayPal', 'SEPA-Lastschrift', 'Rechnung', 'EPS', 'Sofortüberweisung')),
    amount_cents  INTEGER NOT NULL CHECK (amount_cents <> 0),          -- negative: refund
    status        TEXT NOT NULL CHECK (status IN ('authorized', 'captured', 'refunded', 'failed')),
    paid_at       TEXT                                                 -- NULL until captured
)""",
    "CREATE INDEX ix_payments_order ON payments (order_id)",
    """CREATE TABLE shipments (
    shipment_id   INTEGER PRIMARY KEY,
    order_id      INTEGER NOT NULL UNIQUE REFERENCES orders (order_id),
    carrier       TEXT NOT NULL CHECK (carrier IN ('Österreichische Post', 'DHL', 'DPD', 'GLS', 'Hermes')),
    tracking_no   TEXT NOT NULL,
    shipped_at    TEXT NOT NULL,
    delivered_at  TEXT,                                                -- NULL: still on its way
    CHECK (delivered_at IS NULL OR delivered_at >= shipped_at)
)""",
    """CREATE TABLE reviews (
    review_id    INTEGER PRIMARY KEY,
    product_id   INTEGER NOT NULL REFERENCES products (product_id),
    customer_id  INTEGER NOT NULL REFERENCES customers (customer_id),
    rating       INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    title        TEXT NOT NULL,
    body         TEXT,
    created_at   TEXT NOT NULL,
    UNIQUE (product_id, customer_id)
)""",
    # Product search index in Orkexa's SQLite layout (plain tables; no sqlite-vec needed).
    """CREATE TABLE index_spaces (
    namespace   TEXT NOT NULL,
    space       TEXT NOT NULL,
    identity    TEXT NOT NULL,                                        -- JSON: provider, model, digest, dimensions
    dimensions  INTEGER NOT NULL,
    state       TEXT NOT NULL CHECK (state IN ('active', 'building')),
    PRIMARY KEY (namespace, space)
)""",
    """CREATE TABLE index_vectors (
    space     TEXT NOT NULL,
    chunk_id  INTEGER NOT NULL,                                       -- here: the product_id
    embedding BLOB NOT NULL,                                          -- float32 little endian, one per dimension
    PRIMARY KEY (space, chunk_id)
)""",
    """CREATE VIEW monthly_revenue AS
SELECT strftime('%Y-%m', o.order_date)       AS month,
       count(*)                               AS orders,
       sum(o.total_cents)                     AS revenue_cents,
       round(sum(o.total_cents) / 100.0, 2)   AS revenue_eur,
       round(avg(o.total_cents) / 100.0, 2)   AS avg_order_eur
FROM orders AS o
WHERE o.status NOT IN ('cancelled', 'returned')
GROUP BY strftime('%Y-%m', o.order_date)""",
    """CREATE VIEW top_customers AS
SELECT c.customer_id,
       c.first_name || ' ' || c.last_name     AS customer,
       c.city,
       c.country,
       count(o.order_id)                      AS orders,
       round(sum(o.total_cents) / 100.0, 2)   AS revenue_eur
FROM customers AS c
JOIN orders AS o ON o.customer_id = c.customer_id
WHERE o.status NOT IN ('cancelled', 'returned')
GROUP BY c.customer_id
ORDER BY sum(o.total_cents) DESC
LIMIT 25""",
]

# Revision 3 is the one that is still pending on DEV.
REV3_DDL = [
    "ALTER TABLE products ADD COLUMN discontinued_at TEXT",
    "CREATE INDEX ix_orders_status_date ON orders (status, order_date)",
]
REV3_DOWN = [
    "DROP INDEX ix_orders_status_date",
    "ALTER TABLE products DROP COLUMN discontinued_at",
]

ALEMBIC_TABLE = "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL, CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"

# --------------------------------------------------------------------------------------------------------------------
# Content
# --------------------------------------------------------------------------------------------------------------------

FIRST_NAMES = [
    "Anna", "Lukas", "Sophie", "Maximilian", "Lena", "Jonas", "Katharina", "Florian", "Julia", "Tobias",
    "Johanna", "Matthias", "Theresa", "Stefan", "Magdalena", "Andreas", "Elisabeth", "Michael", "Barbara", "Thomas",
    "Jürgen", "Jörg", "Björn", "Sören", "Ännchen", "Günther", "Hölger", "Brigitte", "Ursula", "Dörte",
    "Felix", "Hannah", "Paul", "Marie", "Leon", "Emma", "Elias", "Mia", "Valentina", "David",
    "Severin", "Leonie", "Raphael", "Laura", "Fabian", "Clara", "Dominik", "Verena", "Bernhard", "Gerlinde",
]
LAST_NAMES = [
    "Müller", "Gruber", "Huber", "Wagner", "Pichler", "Steiner", "Moser", "Mayer", "Hofer", "Leitner",
    "Schmidt", "Schneider", "Fischer", "Weber", "Becker", "Schäfer", "Köhler", "Großmann", "Bäcker", "Krüger",
    "Brandstätter", "Wimmer", "Egger", "Lehner", "Haas", "Schwarz", "Wolf", "Fuchs", "Aigner", "Reiter",
    "Öztürk", "Nowak", "Horváth", "Kovačević", "Lindner", "Strauß", "Weiß", "Jäger", "Bauer", "Hoffmann",
]
STREETS = [
    "Hauptstraße", "Bahnhofstraße", "Schulgasse", "Lindenweg", "Kirchenplatz", "Mühlgasse", "Gartenstraße",
    "Am Anger", "Rosenweg", "Wiener Straße", "Grazer Straße", "Dorfstraße", "Färbergasse", "Schloßberg",
    "Untere Weißgerberstraße", "Kärntner Ring", "Bergstraße", "Seestraße", "Höhenweg", "Brückengasse",
]
CITIES_AT = [
    ("1010", "Wien"), ("1070", "Wien"), ("1100", "Wien"), ("1180", "Wien"), ("8010", "Graz"), ("4020", "Linz"),
    ("5020", "Salzburg"), ("6020", "Innsbruck"), ("9020", "Klagenfurt am Wörthersee"), ("3100", "St. Pölten"),
    ("6900", "Bregenz"), ("7000", "Eisenstadt"), ("9500", "Villach"), ("4600", "Wels"), ("2700", "Wiener Neustadt"),
    ("8700", "Leoben"), ("6330", "Kufstein"), ("3500", "Krems an der Donau"),
]
CITIES_DE = [
    ("10115", "Berlin"), ("80331", "München"), ("50667", "Köln"), ("20095", "Hamburg"), ("60311", "Frankfurt am Main"),
    ("70173", "Stuttgart"), ("40213", "Düsseldorf"), ("04109", "Leipzig"), ("01067", "Dresden"),
    ("90402", "Nürnberg"), ("28195", "Bremen"), ("30159", "Hannover"), ("93047", "Regensburg"),
    ("79098", "Freiburg im Breisgau"), ("97070", "Würzburg"), ("83022", "Rosenheim"), ("94032", "Passau"),
]
MAIL_DOMAINS = ["gmx.at", "gmx.de", "web.de", "aon.at", "a1.net", "t-online.de", "posteo.de", "outlook.com", "chello.at"]

# (name, slug, parent slug or None)
CATEGORIES = [
    ("Küche & Haushalt", "kueche-haushalt", None),
    ("Kochgeschirr", "kochgeschirr", "kueche-haushalt"),
    ("Kaffee & Tee", "kaffee-tee", "kueche-haushalt"),
    ("Garten", "garten", None),
    ("Gartenwerkzeug", "gartenwerkzeug", "garten"),
    ("Bewässerung", "bewaesserung", "garten"),
    ("Büro & Schreibwaren", "buero-schreibwaren", None),
    ("Freizeit & Sport", "freizeit-sport", None),
    ("Wandern & Bergsport", "wandern-bergsport", "freizeit-sport"),
    ("Radsport", "radsport", "freizeit-sport"),
    ("Wohnen", "wohnen", None),
    ("Heimtextilien", "heimtextilien", "wohnen"),
    ("Beleuchtung", "beleuchtung", "wohnen"),
]

# category slug -> (base product names, price range in cents, vat rate, weight range in grams)
PRODUCT_BASES = {
    "kochgeschirr": (["Bratpfanne Gusseisen", "Kochtopf Edelstahl", "Schnellkochtopf", "Wok mit Deckel",
                      "Auflaufform Keramik", "Milchtopf emailliert"], (1990, 14990), "20.00", (600, 4200)),
    "kaffee-tee": (["Espressokocher", "French Press", "Teekanne Glas", "Kaffeemühle Handbetrieb",
                    "Bio-Kaffeebohnen Röstung Wien", "Kräutertee Alpenwiese"], (590, 8990), "10.00", (100, 1800)),
    "gartenwerkzeug": (["Gartenschere", "Spaten Eschenholz", "Rechen verzinkt", "Astsäge", "Pflanzkelle",
                        "Heckenschere"], (990, 6990), "20.00", (200, 2600)),
    "bewaesserung": (["Gartenschlauch 20 m", "Gießkanne 10 l", "Regentonne 200 l", "Rasensprenger",
                      "Tropfbewässerung Set"], (1290, 12990), "20.00", (400, 9000)),
    "buero-schreibwaren": (["Notizbuch A5 punktiert", "Füllfederhalter", "Ordner breit", "Schreibtischlampe",
                            "Kugelschreiber Set", "Briefpapier Büttenpapier"], (290, 7990), "20.00", (50, 1500)),
    "wandern-bergsport": (["Wanderrucksack 30 l", "Trinkflasche Edelstahl", "Stirnlampe", "Wanderstöcke Carbon",
                           "Biwaksack", "Grödel"], (1490, 18990), "20.00", (90, 1600)),
    "radsport": (["Fahrradschloss", "Fahrradhelm", "Satteltasche", "Luftpumpe", "Fahrradlicht Set"],
                 (990, 11990), "20.00", (120, 1400)),
    "heimtextilien": (["Leinen-Bettwäsche", "Wolldecke Lodenstoff", "Zierkissen", "Geschirrtuch Halbleinen",
                       "Badetuch Frottee"], (890, 13990), "13.00", (150, 2200)),
    "beleuchtung": (["Stehleuchte Messing", "Tischleuchte", "LED-Lichterkette", "Hängeleuchte Holz"],
                    (1990, 24990), "20.00", (300, 5500)),
}
VARIANTS = ["Classic", "Größe M", "Größe L", "Natur", "Anthrazit", "Grün", "Premium", "Kompakt", "Weiß", "Öko"]

REVIEW_TEXTS = {
    5: [("Sehr zufrieden", "Top Qualität, schnelle Lieferung. Gerne wieder!"), ("Ausgezeichnet", None),
        ("Genau wie beschrieben", "Passt perfekt, Verarbeitung ist hervorragend.")],
    4: [("Gutes Produkt", "Kleine Abzüge bei der Verpackung, sonst super."), ("Empfehlenswert", None),
        ("Preis-Leistung passt", "Etwas größer als erwartet, aber gut.")],
    3: [("Okay", "Erfüllt seinen Zweck, mehr aber nicht."), ("Durchschnittlich", None)],
    2: [("Eher enttäuscht", "Die Farbe weicht deutlich vom Foto ab."), ("Nicht ganz überzeugt", "Wirkt billig.")],
    1: [("Leider kaputt angekommen", "Rücksendung war aber unkompliziert."), ("Mangelhaft", None)],
}
NOTES = ["Bitte beim Nachbarn abgeben.", "Bitte nicht vor 9 Uhr läuten.", "Geschenk – bitte ohne Rechnung im Paket.",
         "Lieferung an die Firmenadresse.", "Hintereingang benutzen, Tür klemmt.", "Stiege 2, Tür 14"]
VOUCHERS = ["WILLKOMMEN10", "HERBST15", "TREUE5", "FRÜHLING20"]


def png(rgb: tuple[int, int, int], size: int = 16) -> bytes:
    """A tiny solid-color PNG with a darker border: the product thumbnail placeholder."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    dark = tuple(max(0, c - 70) for c in rgb)
    rows = []
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            row += bytes(dark if x in (0, size - 1) or y in (0, size - 1) else rgb)
        rows.append(bytes(row))
    raw = b"".join(rows)
    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def ascii_mail(text: str) -> str:
    table = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "č": "c",
                           "á": "a", " ": ""})
    return text.translate(table).lower()


def fmt(ts: dt.datetime) -> str:
    return ts.strftime("%Y-%m-%d %H:%M:%S")


def generate(rnd: random.Random) -> dict[str, list[tuple]]:
    data: dict[str, list[tuple]] = {}

    # Categories
    slug_id: dict[str, int] = {}
    cats = []
    for index, (name, slug, parent) in enumerate(CATEGORIES, start=1):
        slug_id[slug] = index
        cats.append((index, slug_id[parent] if parent else None, name, slug))
    data["categories"] = cats

    # Customers
    customers = []
    emails: set[str] = set()
    for cid in range(1, N_CUSTOMERS + 1):
        first, last = rnd.choice(FIRST_NAMES), rnd.choice(LAST_NAMES)
        country = "AT" if rnd.random() < 0.55 else "DE"
        postal, city = rnd.choice(CITIES_AT if country == "AT" else CITIES_DE)
        base = f"{ascii_mail(first)}.{ascii_mail(last)}"
        email = f"{base}@{rnd.choice(MAIL_DOMAINS)}"
        n = 2
        while email in emails:
            email = f"{base}{n}@{rnd.choice(MAIL_DOMAINS)}"
            n += 1
        emails.add(email)
        if rnd.random() < 0.3:
            phone = None
        elif country == "AT":
            phone = f"+43 6{rnd.choice([60, 64, 76, 99, 50])} {rnd.randint(1000000, 9999999)}"
        else:
            phone = f"+49 1{rnd.choice([51, 52, 60, 70, 76])} {rnd.randint(10000000, 99999999)}"
        street = f"{rnd.choice(STREETS)} {rnd.randint(1, 140)}"
        if country == "AT" and rnd.random() < 0.35:
            street += f"/{rnd.randint(1, 9)}/{rnd.randint(1, 30)}"
        birth = None if rnd.random() < 0.25 else (dt.date(1950, 1, 1) + dt.timedelta(days=rnd.randint(0, 19000))).isoformat()
        created = dt.datetime(2023, 1, 1, 7) + dt.timedelta(minutes=rnd.randint(0, 60 * 24 * 600))
        customers.append((cid, email, first, last, phone, street, postal, city, country, birth,
                          1 if rnd.random() < 0.4 else 0, fmt(created)))
    data["customers"] = customers
    customer_created = {c[0]: dt.datetime.fromisoformat(c[11]) for c in customers}

    # Products
    palette = [(200, 80, 60), (70, 130, 180), (90, 160, 90), (210, 170, 60), (140, 90, 160), (120, 120, 120)]
    product_slugs = list(PRODUCT_BASES)
    combos = [(slug, base) for slug in product_slugs for base in PRODUCT_BASES[slug][0]]
    products = []
    pid = 0
    variant_round = 0
    while len(products) < N_PRODUCTS:
        for slug, base in combos:
            if len(products) >= N_PRODUCTS:
                break
            pid += 1
            _, (lo, hi), vat, (wlo, whi) = PRODUCT_BASES[slug]
            variant = VARIANTS[(variant_round * 3 + pid) % len(VARIANTS)]
            name = f"{base} „{variant}“"
            price = rnd.randrange(lo, hi, 10) // 100 * 100 + 90  # x,90 prices
            description = None if rnd.random() < 0.2 else (
                f"{base} in der Ausführung {variant}. Langlebig, in Europa gefertigt, {rnd.randint(2, 5)} Jahre Garantie.")
            weight = None if rnd.random() < 0.1 else rnd.randint(wlo, whi)
            stock = 0 if rnd.random() < 0.08 else rnd.randint(1, 250)
            active = 0 if rnd.random() < 0.05 else 1
            thumb = None if rnd.random() < 0.15 else png(palette[slug_id[slug] % len(palette)])
            created = dt.datetime(2023, 6, 1, 9) + dt.timedelta(days=rnd.randint(0, 200))
            sku = f"{slug[:3].upper()}-{pid:04d}"
            products.append((pid, sku, slug_id[slug], name, description, price, vat, weight, stock, active, thumb,
                             fmt(created)))
        variant_round += 1
    data["products"] = products
    price_of = {p[0]: p[5] for p in products}
    popular = [p[0] for p in products]
    weights = [rnd.choice([1, 1, 2, 3, 5, 8]) for _ in popular]

    # Orders, items, payments, shipments
    orders, items, payments, shipments = [], [], [], []
    span = int((BASE_DATE - ORDER_START).total_seconds() // 60)
    order_times = sorted(ORDER_START + dt.timedelta(minutes=rnd.randint(0, span)) for _ in range(N_ORDERS))
    cust_weights = [rnd.choice([1, 1, 1, 2, 3, 6]) for _ in customers]
    cust_ids = [c[0] for c in customers]
    payment_id = shipment_id = 0
    for oid, when in enumerate(order_times, start=1):
        cid = rnd.choices(cust_ids, cust_weights)[0]
        if customer_created[cid] > when:
            when = customer_created[cid] + dt.timedelta(hours=rnd.randint(1, 72))
        if when > BASE_DATE:
            when = BASE_DATE - dt.timedelta(hours=rnd.randint(1, 48))
        age_days = (BASE_DATE - when).days
        r = rnd.random()
        if age_days < 3:
            status = "pending" if r < 0.5 else "paid"
        elif age_days < 10:
            status = "shipped" if r < 0.7 else ("paid" if r < 0.85 else "cancelled")
        else:
            status = "delivered" if r < 0.86 else ("cancelled" if r < 0.93 else "returned")
        n_lines = rnd.choices([1, 2, 3, 4, 5, 6], [24, 30, 22, 12, 7, 5])[0]
        chosen: list[int] = []
        while len(chosen) < n_lines:
            p = rnd.choices(popular, weights)[0]
            if p not in chosen:
                chosen.append(p)
        subtotal = 0
        voucher = rnd.choice(VOUCHERS) if rnd.random() < 0.08 else None
        for line_no, p in enumerate(chosen, start=1):
            qty = rnd.choices([1, 2, 3, 4, 6], [70, 18, 7, 3, 2])[0]
            disc = "0.00"
            if voucher:
                disc = {"WILLKOMMEN10": "10.00", "HERBST15": "15.00", "TREUE5": "5.00", "FRÜHLING20": "20.00"}[voucher]
            elif rnd.random() < 0.06:
                disc = rnd.choice(["2.50", "7.50", "12.50"])
            unit = price_of[p]
            # half-up in integers, like SQLite round() for positive numbers
            line = (qty * unit * (10000 - int(disc.replace(".", ""))) + 5000) // 10000
            subtotal += line
            items.append((oid, line_no, p, qty, unit, disc))
        shipping = 0 if subtotal >= 5000 else rnd.choice([490, 590])
        total = subtotal + shipping
        note = rnd.choice(NOTES) if rnd.random() < 0.06 else None
        orders.append((oid, cid, fmt(when), status, "EUR", shipping, total, voucher, note))

        if status != "pending":
            payment_id += 1
            method = rnd.choices(["Kreditkarte", "PayPal", "SEPA-Lastschrift", "Rechnung", "EPS", "Sofortüberweisung"],
                                 [30, 28, 12, 12, 10, 8])[0]
            if status == "cancelled" and rnd.random() < 0.4:
                payments.append((payment_id, oid, method, total, "failed", None))
            else:
                paid = when + dt.timedelta(minutes=rnd.randint(1, 600))
                payments.append((payment_id, oid, method, total, "captured", fmt(paid)))
                if status in ("cancelled", "returned"):
                    payment_id += 1
                    refund = paid + dt.timedelta(days=rnd.randint(2, 20))
                    payments.append((payment_id, oid, method, -total, "refunded", fmt(min(refund, BASE_DATE))))
        else:
            if rnd.random() < 0.5:
                payment_id += 1
                payments.append((payment_id, oid, "Rechnung", total, "authorized", None))
        if status in ("shipped", "delivered", "returned"):
            shipment_id += 1
            carrier = rnd.choice(["Österreichische Post", "DPD", "GLS"]) if customers[cid - 1][8] == "AT" \
                else rnd.choice(["DHL", "DPD", "Hermes", "GLS"])
            shipped = when + dt.timedelta(hours=rnd.randint(12, 60))
            delivered = None if status == "shipped" else fmt(shipped + dt.timedelta(hours=rnd.randint(20, 110)))
            tracking = {"Österreichische Post": "AT", "DHL": "JJD", "DPD": "DPD", "GLS": "GLS", "Hermes": "H"}[carrier]
            shipments.append((shipment_id, oid, carrier, f"{tracking}{rnd.randint(10**11, 10**12 - 1)}",
                              fmt(shipped), delivered))
    data["orders"], data["order_items"] = orders, items
    data["payments"], data["shipments"] = payments, shipments

    # Reviews: only by customers who received the product
    received: list[tuple[int, int, str]] = []
    status_of = {o[0]: (o[1], o[3], o[2]) for o in orders}
    seen: set[tuple[int, int]] = set()
    for oid, _line, p, *_ in items:
        cid, status, when = status_of[oid]
        if status == "delivered" and (p, cid) not in seen:
            seen.add((p, cid))
            received.append((p, cid, when))
    rnd.shuffle(received)
    reviews = []
    for rid, (p, cid, when) in enumerate(sorted(received[:620], key=lambda t: t[2]), start=1):
        rating = rnd.choices([5, 4, 3, 2, 1], [45, 28, 13, 8, 6])[0]
        title, body = rnd.choice(REVIEW_TEXTS[rating])
        created = min(dt.datetime.fromisoformat(when) + dt.timedelta(days=rnd.randint(4, 40)), BASE_DATE)
        reviews.append((rid, p, cid, rating, title, body, fmt(created)))
    data["reviews"] = reviews
    return data


def vectors(products: list[tuple]) -> dict[str, list[tuple]]:
    """The product search index: a separate random stream, so the shop data stays as before."""
    rnd = random.Random(SEED + 1)
    namespace, space = VECTOR_SPACE
    rows = []
    for product in products:
        values = [rnd.gauss(0.0, 1.0) for _ in range(VECTOR_DIMENSIONS)]
        norm = sum(v * v for v in values) ** 0.5
        rows.append((space, product[0], struct.pack(f"<{VECTOR_DIMENSIONS}f", *(v / norm for v in values))))
    return {
        "index_spaces": [(namespace, space, VECTOR_IDENTITY, VECTOR_DIMENSIONS, "active")],
        "index_vectors": rows,
    }


def create_database(path: Path, data: dict[str, list[tuple]], revision: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode = DELETE")
    con.execute("PRAGMA foreign_keys = ON")
    with con:
        for statement in REV1_DDL + REV2_DDL:
            con.execute(statement)
        con.execute(ALEMBIC_TABLE)
        con.execute("INSERT INTO alembic_version (version_num) VALUES (?)", (REV2,))
        for table in ["categories", "customers", "products", "orders", "order_items", "payments", "shipments",
                      "reviews", "index_spaces", "index_vectors"]:
            rows = data[table]
            con.executemany(f"INSERT INTO {table} VALUES ({', '.join('?' * len(rows[0]))})", rows)
        if revision == REV3:
            # PROD: revision 3 applied after the data, as `alembic upgrade head` would; its column gets content.
            for statement in REV3_DDL:
                con.execute(statement)
            con.execute("UPDATE alembic_version SET version_num = ?", (REV3,))
            con.execute("UPDATE products SET discontinued_at = '2025-09-30 00:00:00' WHERE active = 0")
    problems = con.execute("PRAGMA foreign_key_check").fetchall()
    if problems:
        raise SystemExit(f"foreign key problems in {path}: {problems[:5]}")
    if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise SystemExit(f"integrity check failed for {path}")
    con.execute("VACUUM")
    con.close()
    os.chmod(path, 0o644)


# --------------------------------------------------------------------------------------------------------------------
# Project files
# --------------------------------------------------------------------------------------------------------------------

STUDIO_TOML = """\
# Project profiles for Tabrion Studio (schema version 1). Paths are relative to the project folder.
version = 1

[[profile]]
name = "Shop (DEV)"
engine = "sqlite"
path = "daten/shop.sqlite"
environment = "dev"

[[profile]]
name = "Shop (PROD)"
engine = "sqlite"
path = "daten/shop-prod.sqlite"
environment = "prod"
"""

ALEMBIC_INI = """\
# Alembic configuration of the demo shop.
[alembic]
script_location = %(here)s/migrations
prepend_sys_path = .
path_separator = os
sqlalchemy.url = sqlite:///daten/shop.sqlite

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARNING
handlers = console
qualname =

[logger_sqlalchemy]
level = WARNING
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
"""

ENV_PY = '''\
"""Alembic environment of the demo shop (SQLite)."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None


def run_migrations_offline() -> None:
    context.configure(url=config.get_main_option("sqlalchemy.url"), literal_binds=True, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.",
                                     poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
'''

SCRIPT_MAKO = '''\
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""
from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
'''


def revision_file(rev: str, down: str | None, message: str, date: str, up: list[str], downs: list[str]) -> str:
    def block(statements: list[str]) -> str:
        return "\n".join(f'    op.execute(\n        """\n{s}"""\n    )' for s in statements)

    return f'''"""{message}

Revision ID: {rev}
Revises: {down or ""}
Create Date: {date}
"""
from alembic import op

revision = {rev!r}
down_revision = {down!r}
branch_labels = None
depends_on = None


def upgrade() -> None:
{block(up)}


def downgrade() -> None:
{block(downs)}
'''


def drop_statements(ddl: list[str]) -> list[str]:
    drops = []
    for statement in reversed(ddl):
        words = statement.split()
        kind, name = words[1], words[2]
        if kind == "INDEX":
            drops.append(f"DROP INDEX {name}")
        elif kind in ("TABLE", "VIEW"):
            drops.append(f"DROP {kind} {name}")
    return drops


SCRIPTS = {
    "01_bestellungen_eines_kunden.sql": """\
-- Bestellungen eines Kunden mit Positionen (Join mit Parametern).
-- In Studio: :customer_id als Number (z. B. 17) und :since als Text (z. B. 2025-01-01) setzen.
-- Beträge sind in Cent gespeichert; hier in Euro umgerechnet.
SELECT o.order_id,
       o.order_date,
       o.status,
       c.first_name || ' ' || c.last_name                      AS customer,
       i.line_no,
       p.name                                                   AS product,
       i.quantity,
       round(i.unit_price_cents / 100.0, 2)                     AS unit_price_eur,
       i.discount_pct,
       round(i.quantity * i.unit_price_cents
             * (1 - CAST(i.discount_pct AS REAL) / 100) / 100.0, 2) AS line_total_eur
FROM orders AS o
JOIN customers AS c   ON c.customer_id = o.customer_id
JOIN order_items AS i ON i.order_id = o.order_id
JOIN products AS p    ON p.product_id = i.product_id
WHERE o.customer_id = :customer_id
  AND o.order_date >= :since
ORDER BY o.order_date DESC, i.line_no;
""",
    "02_umsatz_pro_kategorie.sql": """\
-- Umsatz pro Hauptkategorie und Land (Aggregation), ohne stornierte und retournierte Bestellungen.
SELECT coalesce(parent.name, cat.name)                      AS main_category,
       c.country,
       count(DISTINCT o.order_id)                           AS orders,
       sum(i.quantity)                                      AS units,
       round(sum(i.quantity * i.unit_price_cents
                 * (1 - CAST(i.discount_pct AS REAL) / 100)) / 100.0, 2) AS revenue_eur,
       round(avg(r.rating), 2)                              AS avg_rating
FROM order_items AS i
JOIN orders AS o          ON o.order_id = i.order_id
JOIN customers AS c       ON c.customer_id = o.customer_id
JOIN products AS p        ON p.product_id = i.product_id
JOIN categories AS cat    ON cat.category_id = p.category_id
LEFT JOIN categories AS parent ON parent.category_id = cat.parent_id
LEFT JOIN reviews AS r    ON r.product_id = p.product_id AND r.customer_id = c.customer_id
WHERE o.status NOT IN ('cancelled', 'returned')
GROUP BY main_category, c.country
ORDER BY revenue_eur DESC;
""",
    "03_schreibversuch_preiserhoehung.sql": """\
-- ACHTUNG: Absichtlich schreibende Anweisung (Testfall). Tabrion Studio ist nur lesend
-- und muss diese Anweisung ablehnen, bevor sie die Datenbank erreicht.
-- Preiserhöhung um 5 % für alle Produkte der Kategorie "Kochgeschirr".
UPDATE products
SET price_cents = CAST(round(price_cents * 1.05) AS INTEGER)
WHERE category_id = (SELECT category_id FROM categories WHERE slug = 'kochgeschirr');
""",
}


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    os.chmod(path, 0o644)


def build(out: Path) -> None:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    rnd = random.Random(SEED)
    data = generate(rnd)
    data.update(vectors(data["products"]))

    write(out / ".tabrion" / "studio.toml", STUDIO_TOML)
    write(out / "alembic.ini", ALEMBIC_INI)
    write(out / "migrations" / "env.py", ENV_PY)
    write(out / "migrations" / "script.py.mako", SCRIPT_MAKO)
    versions = out / "migrations" / "versions"
    write(versions / f"{REV1}_initial_shop_schema.py",
          revision_file(REV1, None, "Initial shop schema: categories, customers, products, orders, order_items",
                        "2024-01-08 10:15:00", REV1_DDL, drop_statements(REV1_DDL)))
    write(versions / f"{REV2}_payments_shipments_reviews.py",
          revision_file(REV2, REV1, "Payments, shipments, reviews, the reporting views and the product search index",
                        "2024-06-03 14:40:00", REV2_DDL, drop_statements(REV2_DDL)))
    write(versions / f"{REV3}_discontinued_products.py",
          revision_file(REV3, REV2, "products.discontinued_at and index ix_orders_status_date",
                        "2025-10-20 09:05:00", REV3_DDL, REV3_DOWN))

    create_database(out / "daten" / "shop.sqlite", data, REV2)
    create_database(out / "daten" / "shop-prod.sqlite", data, REV3)

    for name, text in SCRIPTS.items():
        write(out / "skripte" / name, text)

    for table, rows in data.items():
        print(f"{table:12s} {len(rows):5d}")


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "beispielprojekt"
    build(target)
    print(f"built {target}")
