from flask import Flask, request, jsonify, g
import os, json, hmac, hashlib, time, random
import psycopg
from psycopg.rows import dict_row
from urllib.parse import parse_qsl
from functools import wraps
from datetime import datetime, timezone

app = Flask(__name__)

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
            """SELECT item_name,source,acquired_at FROM inventory
            WHERE telegram_id=%s ORDER BY id DESC""",
            (g.telegram_id,)
        ).fetchall()
    return jsonify([dict(r) for r in rows])

init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
