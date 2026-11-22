from flask import Blueprint, request, jsonify
import os
import psycopg
from psycopg.rows import dict_row

market = Blueprint("market", __name__)

def market_db():
    return psycopg.connect(
        os.environ["DATABASE_URL"],
        row_factory=dict_row
    )

def init_market_db():
    with market_db() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS market_listings (
                id SERIAL PRIMARY KEY,
                demo_key TEXT UNIQUE,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL CHECK (category IN ('gift', 'nft')),
                seller_username TEXT NOT NULL,
                price NUMERIC(20, 9) NOT NULL DEFAULT 0,
                currency TEXT NOT NULL DEFAULT 'TON',
                status TEXT NOT NULL DEFAULT 'active',
                is_demo BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                image_url TEXT NOT NULL DEFAULT ''
            )
        """)

        con.execute("ALTER TABLE market_listings ADD COLUMN IF NOT EXISTS image_url TEXT NOT NULL DEFAULT ''")

        demos = [
            ("gift_bear", "Золотой мишка", "Коллекционный Telegram Gift", "gift", "market_demo", 10, "TON", "gift_bear"),
            ("gift_rose", "Тёмная роза", "Коллекционный Telegram Gift", "gift", "gift_store", 15, "TON", "gift_rose"),
            ("nft_crystal", "Crystal", "Демонстрационный TON NFT", "nft", "ton_gallery", 2, "TON", "nft_crystal"),
            ("nft_moon", "Black Moon", "Демонстрационный TON NFT", "nft", "dark_collection", 3, "TON", "nft_moon"),
            ("gift_star", "Звёздный подарок", "Коллекционный Telegram Gift", "gift", "star_market", 8, "TON", "gift_star"),
            ("nft_gem", "Purple Gem", "Демонстрационный TON NFT", "nft", "nft_demo", 1, "TON", "nft_gem")
        ]

        for item in demos:
            con.execute("""
                INSERT INTO market_listings
                (demo_key, title, description, category,
                 seller_username, price, currency, is_demo, image_url)
                VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE, %s)
                ON CONFLICT (demo_key) DO UPDATE
                SET image_url = EXCLUDED.image_url
            """, item[:7] + (
                "https://neit926-crypto.github.io/hardgift/assets/" + item[7] + ".svg",
            ))

@market.get("/market/categories")
def market_categories():
    return jsonify([
        {"id": "gift", "name": "Telegram Gifts"},
        {"id": "nft", "name": "TON NFT"}
    ])

@market.get("/market/listings")
def market_listings():
    category = request.args.get("category", "all")
    search = request.args.get("search", "").strip()[:80]

    if category not in ("all", "gift", "nft"):
        return jsonify({"error": "Неизвестная категория"}), 400

    query = """
        SELECT id, title, description, category,
               seller_username, price, currency,
               is_demo, image_url, created_at
        FROM market_listings
        WHERE status = 'active'
    """
    params = []

    if category != "all":
        query += " AND category = %s"
        params.append(category)

    if search:
        query += " AND title ILIKE %s"
        params.append("%" + search + "%")

    query += " ORDER BY created_at DESC, id DESC LIMIT 100"

    with market_db() as con:
        rows = con.execute(query, params).fetchall()

    result = []
    for row in rows:
        item = dict(row)
        item["price"] = float(item["price"])
        item["is_demo"] = bool(item["is_demo"])
        result.append(item)

    return jsonify({"listings": result, "count": len(result)})

@market.get("/market/listing/<int:listing_id>")
def market_listing_detail(listing_id):
    with market_db() as con:
        row = con.execute("""
            SELECT id, title, description, category,
                   seller_username, price, currency,
                   is_demo, image_url, created_at
            FROM market_listings
            WHERE id = %s AND status = 'active'
        """, (listing_id,)).fetchone()

    if not row:
        return jsonify({"error": "Объявление не найдено"}), 404

    item = dict(row)
    item["price"] = float(item["price"])
    item["is_demo"] = bool(item["is_demo"])
    return jsonify(item)
