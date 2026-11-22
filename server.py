from flask import Flask, request, jsonify, g
import os, json, hmac, hashlib, time, random
import psycopg
from psycopg.rows import dict_row
from urllib.parse import parse_qsl
from functools import wraps
from datetime import datetime, timezone

from market import market, init_market_db

app = Flask(__name__)
app.register_blueprint(market)

@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "https://neit926-crypto.github.io"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Telegram-Init-Data"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


def db():
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("Не задан DATABASE_URL")
    return psycopg.connect(url, row_factory=dict_row)

def init_db():
    with db() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS cases (
                id SERIAL PRIMARY KEY,
                name TEXT UNIQUE NOT NULL,
                price_coins INTEGER DEFAULT 0,
                description TEXT NOT NULL,
                active INTEGER DEFAULT 1
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS case_rewards (
                id SERIAL PRIMARY KEY,
                case_id INTEGER REFERENCES cases(id),
                reward_name TEXT NOT NULL,
                description TEXT
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS inventory (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT NOT NULL,
                item_name TEXT NOT NULL,
                source TEXT NOT NULL,
                acquired_at TEXT NOT NULL
            )
        """)


        con.execute("""
            CREATE TABLE IF NOT EXISTS case_cooldowns (
                telegram_id BIGINT NOT NULL,
                case_id INTEGER REFERENCES cases(id),
                last_opened_at TIMESTAMPTZ NOT NULL,
                PRIMARY KEY (telegram_id, case_id)
            )
        """)


def telegram_auth():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        return None, "Сервер ещё не настроен"

    init_data = request.headers.get("X-Telegram-Init-Data", "")
    if not init_data:
        return None, "Нет данных Telegram"

    try:
        fields = dict(parse_qsl(init_data, keep_blank_values=True))
        received_hash = fields.pop("hash", "")
        auth_date = int(fields.get("auth_date", "0"))

        if not received_hash or abs(time.time() - auth_date) > 86400:
            return None, "Данные устарели"

        check_string = "\n".join(
            f"{key}={value}" for key, value in sorted(fields.items())
        )
        secret = hmac.new(
            b"WebAppData", token.encode(), hashlib.sha256
        ).digest()
        calculated = hmac.new(
            secret, check_string.encode(), hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(calculated, received_hash):
            return None, "Проверка Telegram не пройдена"

        user = json.loads(fields["user"])
        return int(user["id"]), None
    except Exception:
        return None, "Некорректные данные Telegram"

def require_telegram(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user_id, error = telegram_auth()
        if error:
            return jsonify({"error": error}), 401

        g.telegram_id = user_id

        fields = dict(parse_qsl(
            request.headers.get("X-Telegram-Init-Data", ""),
            keep_blank_values=True
        ))
        user = json.loads(fields.get("user", "{}"))

        with db() as con:
            con.execute("""
                INSERT INTO user_balances (telegram_id, balance)
                VALUES (%s, 0)
                ON CONFLICT (telegram_id) DO NOTHING
            """, (user_id,))

            con.execute("""
                INSERT INTO user_profiles
                    (telegram_id, first_name, last_name, username, photo_url)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (telegram_id) DO UPDATE SET
                    first_name = EXCLUDED.first_name,
                    last_name = EXCLUDED.last_name,
                    username = EXCLUDED.username,
                    photo_url = EXCLUDED.photo_url
            """, (
                user_id,
                user.get("first_name", ""),
                user.get("last_name", ""),
                user.get("username", ""),
                user.get("photo_url", "")
            ))

        return fn(*args, **kwargs)
    return wrapper

@app.get("/")
def home():
    return "HARD GIFT API работает!"

@app.get("/cases")
def cases():
    with db() as con:
        rows = con.execute(
            "SELECT id,name,price_coins,description FROM cases WHERE active=1 ORDER BY id"
        ).fetchall()
    return jsonify([dict(r) for r in rows])

@app.get("/case-cooldowns")
@require_telegram
def case_cooldowns():
    with db() as con:
        rows = con.execute(
            """
            SELECT c.name AS case_name,
                   COALESCE(
                       GREATEST(
                           0,
                           CEIL(EXTRACT(EPOCH FROM
                               (cc.last_opened_at + INTERVAL '24 hours' - NOW())
                           ))::INTEGER
                       ), 0
                   ) AS remaining_seconds
            FROM cases c
            LEFT JOIN case_cooldowns cc
                ON cc.case_id = c.id
                AND cc.telegram_id = %s
            WHERE c.active = 1 AND c.price_coins = 0
            """,
            (g.telegram_id,)
        ).fetchall()

    return jsonify([dict(r) for r in rows])


@app.post("/open-free-case")
@require_telegram
def open_free_case():
    data = request.get_json(silent=True) or {}
    case_name = data.get("case_name")

    if not case_name:
        return jsonify({"error": "Укажи название кейса"}), 400

    with db() as con:
        case = con.execute(
            "SELECT id,price_coins FROM cases WHERE name=%s AND active=1",
            (case_name,)
        ).fetchone()

        if not case:
            return jsonify({"error": "Кейс не найден"}), 404

        if case["price_coins"] != 0:
            return jsonify({"error": "Этот кейс не бесплатный"}), 403

        rewards = con.execute(
            "SELECT reward_name,description FROM case_rewards WHERE case_id=%s",
            (case["id"],)
        ).fetchall()

        if not rewards:
            return jsonify({"error": "В кейсе пока нет наград"}), 400

        cooldown = con.execute(
            """
            INSERT INTO case_cooldowns
                (telegram_id, case_id, last_opened_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (telegram_id, case_id)
            DO UPDATE SET last_opened_at = NOW()
            WHERE case_cooldowns.last_opened_at <= NOW() - INTERVAL '24 hours'
            RETURNING last_opened_at
            """,
            (g.telegram_id, case["id"])
        ).fetchone()

        if not cooldown:
            previous = con.execute(
                """
                SELECT last_opened_at FROM case_cooldowns
                WHERE telegram_id=%s AND case_id=%s
                """,
                (g.telegram_id, case["id"])
            ).fetchone()

            remaining = max(
                0,
                86400 - int(
                    (datetime.now(timezone.utc) - previous["last_opened_at"]).total_seconds()
                )
            )

            return jsonify({
                "success": False,
                "error": "Кейс пока на перезарядке",
                "retry_after_seconds": remaining
            }), 429

        reward = random.choice(rewards)

        con.execute(
            """
            INSERT INTO inventory
                (telegram_id,item_name,source,acquired_at)
            VALUES(%s,%s,%s,%s)
            """,
            (
                g.telegram_id,
                reward["reward_name"],
                case_name,
                datetime.now(timezone.utc).isoformat()
            )
        )

    return jsonify({
        "success": True,
        "reward": reward["reward_name"],
        "description": reward["description"],
        "cooldown_seconds": 86400
    })


@app.get("/inventory")
@require_telegram
def inventory():
    with db() as con:
        rows = con.execute(
            """SELECT id,item_name,source,acquired_at FROM inventory
            WHERE telegram_id=%s ORDER BY id DESC""",
            (g.telegram_id,)
        ).fetchall()
    return jsonify([dict(r) for r in rows])



@app.post("/sell-item")
@require_telegram
def sell_item():
    data = request.get_json(silent=True) or {}
    item_id = data.get("item_id")

    if not isinstance(item_id, int) or isinstance(item_id, bool):
        return jsonify({"error": "Некорректный предмет"}), 400

    with db() as con:
        item = con.execute("""
            DELETE FROM inventory
            WHERE id=%s AND telegram_id=%s
            RETURNING item_name
        """, (item_id, g.telegram_id)).fetchone()

        if not item:
            return jsonify({"error": "Подарок уже продан или не найден"}), 404

        row = con.execute("""
            INSERT INTO user_balances (telegram_id, balance)
            VALUES (%s, 0.5)
            ON CONFLICT (telegram_id)
            DO UPDATE SET balance = user_balances.balance + 0.5
            RETURNING balance
        """, (g.telegram_id,)).fetchone()

    return jsonify({
        "success": True,
        "sold_item": item["item_name"],
        "earned": 0.5,
        "balance": float(row["balance"])
    })



@app.get("/balance")
@require_telegram
def get_balance():
    with db() as con:
        row = con.execute(
            "SELECT balance FROM user_balances WHERE telegram_id=%s",
            (g.telegram_id,)
        ).fetchone()
    return jsonify({"balance": float(row["balance"]) if row else 0.0})


def init_balance_db():
    with db() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS user_balances (
                telegram_id BIGINT PRIMARY KEY,
                balance NUMERIC(18,1) NOT NULL DEFAULT 0
            )
        """)


def init_profile_db():
    with db() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS user_profiles (
                telegram_id BIGINT PRIMARY KEY,
                first_name TEXT DEFAULT '',
                last_name TEXT DEFAULT '',
                username TEXT DEFAULT '',
                photo_url TEXT DEFAULT ''
            )
        """)

def init_online_db():
    with db() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS online_sessions (
                telegram_id BIGINT PRIMARY KEY,
                last_seen TIMESTAMPTZ NOT NULL
            )
        """)

@app.get("/rating")
@require_telegram
def get_rating():
    with db() as con:
        rows = con.execute("""
            SELECT
                b.telegram_id,
                b.balance,
                COALESCE(p.first_name, '') AS first_name,
                COALESCE(p.last_name, '') AS last_name,
                COALESCE(p.username, '') AS username,
                COALESCE(p.photo_url, '') AS photo_url,
                ROW_NUMBER() OVER (
                    ORDER BY b.balance DESC, b.telegram_id ASC
                ) AS rank
            FROM user_balances b
            LEFT JOIN user_profiles p
                ON p.telegram_id = b.telegram_id
            ORDER BY b.balance DESC, b.telegram_id ASC
            LIMIT 100
        """).fetchall()

        me = con.execute("""
            SELECT rank
            FROM (
                SELECT telegram_id,
                       ROW_NUMBER() OVER (
                           ORDER BY balance DESC, telegram_id ASC
                       ) AS rank
                FROM user_balances
            ) ranked
            WHERE telegram_id = %s
        """, (g.telegram_id,)).fetchone()

    return jsonify({
        "users": [
            {
                "rank": int(r["rank"]),
                "telegram_id": r["telegram_id"],
                "balance": float(r["balance"]),
                "first_name": r["first_name"],
                "last_name": r["last_name"],
                "username": r["username"],
                "photo_url": r["photo_url"]
            }
            for r in rows
        ],
        "my_rank": int(me["rank"])
    })


@app.post("/online/heartbeat")
@require_telegram
def online_heartbeat():
    with db() as con:
        con.execute("""
            INSERT INTO online_sessions (telegram_id, last_seen)
            VALUES (%s, NOW())
            ON CONFLICT (telegram_id)
            DO UPDATE SET last_seen = NOW()
        """, (g.telegram_id,))
    return jsonify({"success": True})

@app.get("/online/count")
def online_count():
    with db() as con:
        row = con.execute("""
            SELECT COUNT(*) AS total
            FROM online_sessions
            WHERE last_seen >= NOW() - INTERVAL '90 seconds'
        """).fetchone()
    return jsonify({"count": row["total"]})

init_db()
init_balance_db()
init_profile_db()
init_online_db()
init_market_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
