import csv
import io
import os
import re
import secrets
import sqlite3
import sys
from pathlib import Path
from datetime import datetime, date
from decimal import Decimal, InvalidOperation
from functools import wraps

BASE_DIR = Path(__file__).resolve().parent
VENDOR_DIR = BASE_DIR / ".vendor"
if VENDOR_DIR.exists() and str(VENDOR_DIR) not in sys.path:
    sys.path.insert(0, str(VENDOR_DIR))

import jdatetime
from dotenv import load_dotenv
from flask import Flask, Response, flash, redirect, render_template, request, url_for, abort, session, g, jsonify, has_request_context
from flask_login import LoginManager, UserMixin, current_user, login_required, login_user, logout_user
from flask_wtf.csrf import CSRFProtect
from werkzeug.exceptions import HTTPException

try:
    import pyodbc
except ImportError:
    pyodbc = None

LOCAL_DB_PATH = BASE_DIR / "padra_workflow.db"
load_dotenv(dotenv_path=BASE_DIR / ".env", override=True)

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY") or "padra-serkan-bi-secret-key-2026"
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024
app.config["SESSION_COOKIE_SAMESITE"] = os.getenv("SESSION_COOKIE_SAMESITE", "Lax")
app.config["SESSION_COOKIE_SECURE"] = os.getenv("SESSION_COOKIE_SECURE", "false").strip().lower() in {"1", "true", "yes", "on"}
app.config["SESSION_COOKIE_HTTPONLY"] = True

CSRFProtect(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "برای ادامه ابتدا وارد سامانه شوید."
login_manager.login_message_category = "warning"

# ساختار رسمی کاربران و نقش‌های سازمانی شرکت پادرا و گروه صنعتی سرکان
ORGANIZATIONAL_USERS = {
    "padra_expert": {
        "id": 1,
        "username": "padra_expert",
        "full_name": "مهندس امیر حسینی",
        "role_title": "کارشناس سفارش‌گذاری شرکت پادرا",
        "org": "شرکت پادرا",
        "badge_color": "blue",
    },
    "ceo_padra": {
        "id": 2,
        "username": "ceo_padra",
        "full_name": "مهندس محمدرضا صادقی",
        "role_title": "مدیرعامل شرکت پادرا",
        "org": "شرکت پادرا",
        "badge_color": "navy",
    },
    "serkan_purchase_mgr": {
        "id": 3,
        "username": "serkan_purchase_mgr",
        "full_name": "مهندس بهرام کاظمی",
        "role_title": "مدیر خرید داخلی گروه صنعتی سرکان",
        "org": "گروه صنعتی سرکان",
        "badge_color": "teal",
    },
    "kharid_expert_1": {
        "id": 4,
        "username": "kharid_expert_1",
        "full_name": "مهندس علی رضایی",
        "role_title": "کارشناس خرید فنی و تجهیزات (سرکان)",
        "org": "گروه صنعتی سرکان",
        "specialty": "قطعات صنعتی، ماشین‌آلات، ابزار دقیق و تجهیزات خط تولید",
        "recommended_for": "equipment",
    },
    "kharid_expert_2": {
        "id": 5,
        "username": "kharid_expert_2",
        "full_name": "مهندس سارا محمدی",
        "role_title": "کارشناس خرید مواد اولیه و شیمیایی (سرکان)",
        "org": "گروه صنعتی سرکان",
        "specialty": "مواد اولیه فیلتراسیون، کاغذ مدیا، ورق، چسب و پلیمر",
        "recommended_for": "goods",
    },
    "kharid_expert_3": {
        "id": 6,
        "username": "kharid_expert_3",
        "full_name": "مهندس سعید کریمی",
        "role_title": "کارشناس خرید خدمات و ملزومات عمومی (سرکان)",
        "org": "گروه صنعتی سرکان",
        "specialty": "خدمات فنی مهندسی، پیمانکاری، تعمیرات و ملزومات عمومی",
        "recommended_for": "service",
    },
    "admin": {
        "id": 7,
        "username": "admin",
        "full_name": "مدیر سیستم (واحد هوش تجاری سرکان)",
        "role_title": "واحد هوش تجاری گروه صنعتی سرکان (BI)",
        "org": "گروه صنعتی سرکان",
        "badge_color": "gold",
    },
}

PURCHASE_SPECIALIST_FALLBACKS = [
    {"username": "v.eskandari", "full_name": "وحید اسکندری"},
    {"username": "sh.ghasemian", "full_name": "شایان قاسمیان"},
    {"username": "gh.sohrabi", "full_name": "قدرت سهرابی"},
    {"username": "s.yazdani", "full_name": "سجاد یزدانی"},
    {"username": "h.arabi", "full_name": "حسین عربی"},
    {"username": "h.mohebi", "full_name": "حامد محبی"},
    {"username": "j.safaei", "full_name": "جواد صفایی"},
    {"username": "b.eskandari", "full_name": "بهنام اسکندری"},
    {"username": "m.abedi", "full_name": "محمدجواد عابدینی"},
    {"username": "mb.cheraghi", "full_name": "محمدباقر چراغی"},
    {"username": "f.botkan", "full_name": "فرانک بُت‌کَن"},
    {"username": "z.hatami", "full_name": "زهرا حاتمی"},
    {"username": "a.azarli", "full_name": "آرزو آذرلی"},
]


def get_purchase_specialists():
    conn = cur = None
    try:
        conn = auth_db()
        cur = conn.cursor()
        cur.execute(
            """SELECT DISTINCT u.username, COALESCE(NULLIF(u.full_name,N''),N'')
               FROM Project.loginusers u
               INNER JOIN Project.IAM_UserRoles ur ON ur.user_id = u.id
               INNER JOIN Project.IAM_Roles r ON r.id = ur.role_id
               INNER JOIN Project.IAM_Projects p ON p.id = r.project_id
               WHERE r.role_key = 'serkan_purchase_specialist'
                 AND r.is_active = 1
                 AND (ur.valid_from IS NULL OR ur.valid_from <= SYSDATETIME())
                 AND (ur.valid_until IS NULL OR ur.valid_until > SYSDATETIME())
                 AND p.project_key = 'padra-purchase-request'
               ORDER BY u.username"""
        )
        display_names = {item["username"]: item["full_name"] for item in PURCHASE_SPECIALIST_FALLBACKS}
        return [
            {
                "username": item[0],
                "full_name": display_names.get(item[0]) or (item[1] or "").strip() or item[0],
                "role_title": "کارشناس خرید داخلی گروه صنعتی سرکان",
                "org": "گروه صنعتی سرکان",
                "badge_color": "teal",
            }
            for item in cur.fetchall()
        ]
    except Exception:
        app.logger.exception("Unable to load purchase specialists from central IAM")
        return []
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


_ISO_DT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}")


def _parse_value(val):
    if isinstance(val, str) and _ISO_DT_RE.match(val):
        try:
            return datetime.fromisoformat(val)
        except ValueError:
            return val
    return val


class SQLiteCompatCursor:
    """SQLite cursor adapter that translates SQL Server T-SQL idioms."""

    def __init__(self, conn):
        self._conn = conn
        self._cur = conn.cursor()
        self._prefetched = None

    def _normalize_params(self, params):
        if len(params) == 1 and isinstance(params[0], (list, tuple)):
            params = tuple(params[0])
        normalized = []
        for p in params:
            if isinstance(p, Decimal):
                normalized.append(int(p) if p == int(p) else float(p))
            elif isinstance(p, datetime):
                normalized.append(p.strftime("%Y-%m-%d %H:%M:%S"))
            elif isinstance(p, date):
                normalized.append(p.isoformat())
            elif isinstance(p, bool):
                normalized.append(1 if p else 0)
            else:
                normalized.append(p)
        return tuple(normalized)

    def _translate_sql(self, sql):
        q = sql.strip()
        q = re.sub(r"\bN'", "'", q)
        q = re.sub(r"NVARCHAR\s*\(\s*\d+\s*\)", "TEXT", q, flags=re.IGNORECASE)

        if "OUTER APPLY" in q.upper():
            q = re.sub(
                r"FROM\s+Project\.requests\s+r\s+"
                r"OUTER\s+APPLY\s*\(\s*SELECT\s+TOP\s*\(\s*1\s*\)\s+a\.approver_username\s*,\s*a\.approver_title\s*,\s*a\.step_no\s+"
                r"FROM\s+Project\.approvals\s+a\s+WHERE\s+a\.request_id\s*=\s*r\.id\s+AND\s+a\.decision\s*=\s*'pending'\s+ORDER\s+BY\s+a\.step_no\s*\)\s*p\s+"
                r"OUTER\s+APPLY\s*\(\s*SELECT\s+COUNT\(\*\)\s+AS\s+total_steps\s+FROM\s+Project\.approvals\s+a\s+WHERE\s+a\.request_id\s*=\s*r\.id\s*\)\s*st\s+"
                r"OUTER\s+APPLY\s*\(\s*SELECT\s+COUNT\(\*\)\s+AS\s+item_count\s+FROM\s+Project\.request_items\s+i\s+WHERE\s+i\.request_id\s*=\s*r\.id\s*\)\s*it",
                """FROM Project.requests r
                LEFT JOIN (
                    SELECT a1.request_id, a1.approver_username, a1.approver_title, a1.step_no
                    FROM Project.approvals a1
                    WHERE a1.decision = 'pending'
                      AND a1.step_no = (
                          SELECT MIN(a2.step_no) FROM Project.approvals a2
                          WHERE a2.request_id = a1.request_id AND a2.decision = 'pending'
                      )
                ) p ON p.request_id = r.id
                LEFT JOIN (
                    SELECT request_id, COUNT(*) AS total_steps
                    FROM Project.approvals GROUP BY request_id
                ) st ON st.request_id = r.id
                LEFT JOIN (
                    SELECT request_id, COUNT(*) AS item_count
                    FROM Project.request_items GROUP BY request_id
                ) it ON it.request_id = r.id""",
                q,
                flags=re.IGNORECASE | re.DOTALL,
            )

        top_match = re.match(r"^SELECT\s+TOP\s*\(\s*(\d+)\s*\)\s+", q, flags=re.IGNORECASE)
        if top_match:
            limit_n = top_match.group(1)
            q = re.sub(r"^SELECT\s+TOP\s*\(\s*\d+\s*\)\s+", "SELECT ", q, count=1, flags=re.IGNORECASE)
            q = f"{q} LIMIT {limit_n}"

        return q

    def execute(self, sql, *params):
        self._prefetched = None
        bound = self._normalize_params(params)
        has_output_id = bool(re.search(r"\bOUTPUT\s+INSERTED\.id\b", sql, flags=re.IGNORECASE))
        if has_output_id:
            clean_sql = re.sub(r"\bOUTPUT\s+INSERTED\.id\b", "", sql, flags=re.IGNORECASE)
            clean_sql = self._translate_sql(clean_sql)
            self._cur.execute(clean_sql, bound)
            self._prefetched = [(self._cur.lastrowid,)]
            return self

        translated = self._translate_sql(sql)
        self._cur.execute(translated, bound)
        return self

    def fetchone(self):
        if self._prefetched is not None:
            row = self._prefetched[0] if self._prefetched else None
            self._prefetched = None
            return row
        row = self._cur.fetchone()
        if row is None:
            return None
        return tuple(_parse_value(v) for v in row)

    def fetchall(self):
        if self._prefetched is not None:
            rows = self._prefetched
            self._prefetched = None
            return rows
        return [tuple(_parse_value(v) for v in row) for row in self._cur.fetchall()]

    def close(self):
        self._cur.close()


class SQLiteCompatConnection:
    """SQLite connection wrapper compatible with pyodbc connection semantics."""

    def __init__(self, db_path: Path):
        self._raw = sqlite3.connect(str(db_path), check_same_thread=False)
        self._raw.execute("PRAGMA foreign_keys = ON")
        self._raw.execute("ATTACH DATABASE ? AS Project", (str(db_path),))
        now_fn = lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._raw.create_function("GETDATE", 0, now_fn)
        self._raw.create_function("SYSDATETIME", 0, now_fn)
        self.autocommit = True

    def cursor(self):
        return SQLiteCompatCursor(self._raw)

    def commit(self):
        self._raw.commit()

    def rollback(self):
        self._raw.rollback()

    def close(self):
        if self.autocommit:
            try:
                self._raw.commit()
            except Exception:
                pass
        self._raw.close()


def _init_local_sqlite_db():
    conn = sqlite3.connect(str(LOCAL_DB_PATH))
    try:
        cur = conn.cursor()
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS loginusers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                full_name TEXT
            );

            CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_no TEXT UNIQUE,
                request_date TEXT NOT NULL,
                request_date_fa TEXT NOT NULL,
                request_type TEXT NOT NULL,
                unit_name TEXT NOT NULL,
                supply_location TEXT NOT NULL,
                fixed_asset INTEGER NOT NULL DEFAULT 0,
                request_reason TEXT,
                requester_username TEXT NOT NULL,
                workflow_type TEXT NOT NULL DEFAULT 'serkan',
                status TEXT NOT NULL DEFAULT 'pending',
                assigned_specialist TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS request_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id INTEGER NOT NULL,
                line_no INTEGER NOT NULL,
                item_code TEXT,
                item_description TEXT NOT NULL,
                unit_name TEXT NOT NULL,
                quantity REAL NOT NULL,
                needed_date_fa TEXT NOT NULL,
                usage_location TEXT NOT NULL,
                FOREIGN KEY(request_id) REFERENCES requests(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS approval_routes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_type TEXT NOT NULL,
                step_no INTEGER NOT NULL,
                approver_username TEXT NOT NULL,
                approver_title TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                UNIQUE(request_type, step_no)
            );

            CREATE TABLE IF NOT EXISTS approvals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id INTEGER NOT NULL,
                step_no INTEGER NOT NULL,
                approver_username TEXT NOT NULL,
                approver_title TEXT NOT NULL,
                decision TEXT NOT NULL DEFAULT 'waiting',
                comment TEXT,
                decided_at TEXT,
                FOREIGN KEY(request_id) REFERENCES requests(id) ON DELETE CASCADE,
                UNIQUE(request_id, step_no)
            );

            CREATE TABLE IF NOT EXISTS request_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id INTEGER NOT NULL,
                action_name TEXT NOT NULL,
                actor_username TEXT NOT NULL,
                details TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                FOREIGN KEY(request_id) REFERENCES requests(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS request_supplier_offers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id INTEGER NOT NULL,
                request_item_id INTEGER NOT NULL,
                supplier_name TEXT NOT NULL,
                proforma_number TEXT NOT NULL,
                delivery_date_fa TEXT NOT NULL,
                quantity REAL NOT NULL,
                unit_price REAL NOT NULL,
                payment_method TEXT NOT NULL,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                is_selected INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(request_id) REFERENCES requests(id) ON DELETE CASCADE,
                FOREIGN KEY(request_item_id) REFERENCES request_items(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS request_deliveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id INTEGER NOT NULL,
                offer_id INTEGER NOT NULL,
                receipt_no TEXT NOT NULL,
                delivery_date_fa TEXT NOT NULL,
                quantity REAL NOT NULL,
                recorded_by TEXT NOT NULL,
                recorded_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                FOREIGN KEY(request_id) REFERENCES requests(id) ON DELETE CASCADE,
                FOREIGN KEY(offer_id) REFERENCES request_supplier_offers(id) ON DELETE CASCADE
            );
            """
        )

        cur.execute("PRAGMA table_info(request_supplier_offers)")
        offer_columns = {row[1] for row in cur.fetchall()}
        if "is_selected" not in offer_columns:
            cur.execute("ALTER TABLE request_supplier_offers ADD COLUMN is_selected INTEGER NOT NULL DEFAULT 0")

        cur.execute("PRAGMA table_info(requests)")
        request_columns = {row[1] for row in cur.fetchall()}
        if "workflow_type" not in request_columns:
            cur.execute("ALTER TABLE requests ADD COLUMN workflow_type TEXT NOT NULL DEFAULT 'serkan'")

        cur.execute("SELECT COUNT(*) FROM loginusers")
        if cur.fetchone()[0] == 0:
            for u in ORGANIZATIONAL_USERS.values():
                cur.execute(
                    "INSERT INTO loginusers (id, username, password_hash, full_name) VALUES (?, ?, ?, ?)",
                    (u["id"], u["username"], "123456", u["full_name"]),
                )

        cur.execute("SELECT COUNT(*) FROM approval_routes")
        if cur.fetchone()[0] == 0:
            for req_type in ("goods", "equipment", "service", "other"):
                cur.executemany(
                    """INSERT INTO approval_routes
                       (request_type, step_no, approver_username, approver_title, is_active)
                       VALUES (?, ?, ?, ?, 1)""",
                    [
                        (req_type, 1, "ceo_padra", "مدیرعامل شرکت پادرا"),
                        (req_type, 2, "serkan_purchase_mgr", "مدیر خرید داخلی گروه صنعتی سرکان"),
                        (req_type, 3, "UNASSIGNED_SPECIALIST", "کارشناس خرید گروه صنعتی سرکان (منتظر تخصیص)"),
                    ],
                )

        cur.execute("SELECT COUNT(*) FROM requests")
        if cur.fetchone()[0] == 0:
            # نمونه ۱: در مرحله ۲ (تأیید شده توسط مدیرعامل پادرا، در انتظار ارجاع مدیر خرید داخلی سرکان به کارشناس خرید)
            cur.execute(
                """INSERT INTO requests
                   (request_no, request_date, request_date_fa, request_type, unit_name,
                    supply_location, fixed_asset, request_reason, requester_username, status,
                    assigned_specialist, created_at, updated_at)
                   VALUES ('PR-14050809-000001', '2026-10-31', '1405/08/09', 'equipment',
                           'نگهداری و تعمیرات (نت) - پادرا', 'خرید داخلی (گروه صنعتی سرکان)', 1,
                           '[فوری] تأمین بیرینگ‌های دور بالا و شیر برقی هیدرولیک خط تولید فیلتر هوا جهت جلوگیری از توقف خط',
                           'padra_expert', 'pending', NULL, '2026-10-31 08:45:00', '2026-10-31 10:15:00')"""
            )
            r1 = cur.lastrowid
            cur.executemany(
                """INSERT INTO request_items
                   (request_id, line_no, item_code, item_description, unit_name, quantity, needed_date_fa, usage_location)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (r1, 1, "PDR-EQ-402", "بلبرینگ صنعتی دور بالا SKF 6208-2Z/C3 اصلی", "عدد", 20, "1405/08/16", "خط تولید فیلتر هوا - سالن ۱"),
                    (r1, 2, "PDR-EQ-815", "شیر برقی هیدرولیک ۲۴ ولت رکسروت آلمان", "عدد", 4, "1405/08/16", "پرس هیدرولیک خط مونتاژ"),
                ],
            )
            cur.executemany(
                """INSERT INTO approvals
                   (request_id, step_no, approver_username, approver_title, decision, comment, decided_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                [
                    (r1, 1, "ceo_padra", "مدیرعامل شرکت پادرا", "approved", "با توجه به ضرورت خط تولید پادرا مورد تأیید است؛ لطفاً از طریق مدیریت خرید داخلی گروه سرکان با اولویت تأمین گردد.", "2026-10-31 10:15:00"),
                    (r1, 2, "serkan_purchase_mgr", "مدیر خرید داخلی گروه صنعتی سرکان", "pending", None, None),
                    (r1, 3, "UNASSIGNED_SPECIALIST", "کارشناس خرید گروه صنعتی سرکان (منتظر تخصیص)", "waiting", None, None),
                ],
            )
            cur.executemany(
                """INSERT INTO request_history
                   (request_id, action_name, actor_username, details, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                [
                    (r1, "ثبت درخواست در شرکت پادرا", "padra_expert", "درخواست خرید توسط کارشناس سفارش‌گذاری شرکت پادرا ثبت شد.", "2026-10-31 08:45:00"),
                    (r1, "ارجاع به مدیرعامل پادرا", "padra_expert", "درخواست برای مرحله ۱ به مدیرعامل شرکت پادرا (ceo_padra) ارسال شد.", "2026-10-31 08:45:05"),
                    (r1, "تأیید مدیرعامل شرکت پادرا", "ceo_padra", "تأیید مرحله ۱ · مدیرعامل شرکت پادرا — توضیح: با توجه به ضرورت خط تولید پادرا مورد تأیید است؛ لطفاً از طریق مدیریت خرید داخلی گروه سرکان با اولویت تأمین گردد.", "2026-10-31 10:15:00"),
                    (r1, "محول‌سازی به مدیر خرید داخلی سرکان", "SYSTEM", "درخواست پس از تأیید مدیرعامل پادرا به مرحله ۲ (مدیر خرید داخلی گروه صنعتی سرکان - serkan_purchase_mgr) محول شد تا کارشناس خرید تعیین گردد.", "2026-10-31 10:15:02"),
                ],
            )

            # نمونه ۲: در مرحله ۱ (ثبت شده توسط کارشناس پادرا، در انتظار تأیید مدیرعامل شرکت پادرا)
            cur.execute(
                """INSERT INTO requests
                   (request_no, request_date, request_date_fa, request_type, unit_name,
                    supply_location, fixed_asset, request_reason, requester_username, status,
                    assigned_specialist, created_at, updated_at)
                   VALUES ('PR-14050810-000002', '2026-11-01', '1405/08/10', 'goods',
                           'تولید و برنامه‌ریزی مواد - پادرا', 'خرید داخلی (گروه صنعتی سرکان)', 0,
                           '[عادی] سفارش‌گذاری کاغذ مدیا فیلتر روغن و چسب پلی‌اورتان دو جزئی جهت برنامه تولید آبان و آذر شرکت پادرا',
                           'padra_expert', 'pending', NULL, '2026-11-01 08:20:00', '2026-11-01 08:20:00')"""
            )
            r2 = cur.lastrowid
            cur.executemany(
                """INSERT INTO request_items
                   (request_id, line_no, item_code, item_description, unit_name, quantity, needed_date_fa, usage_location)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (r2, 1, "PDR-RM-104", "رول کاغذ مدیا فیلتر روغن عرض ۲۲۰ میلی‌متر گرماژ استاندارد", "کیلوگرم", 1200, "1405/08/22", "سالن چین‌کنی و مونتاژ پادرا"),
                    (r2, 2, "PDR-RM-219", "چسب دو جزئی پلی‌اورتان صنعتی ویژه فیلتر", "کیلوگرم", 350, "1405/08/22", "خط تزریق فوم و مونتاژ"),
                ],
            )
            cur.executemany(
                """INSERT INTO approvals
                   (request_id, step_no, approver_username, approver_title, decision, comment, decided_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                [
                    (r2, 1, "ceo_padra", "مدیرعامل شرکت پادرا", "pending", None, None),
                    (r2, 2, "serkan_purchase_mgr", "مدیر خرید داخلی گروه صنعتی سرکان", "waiting", None, None),
                    (r2, 3, "UNASSIGNED_SPECIALIST", "کارشناس خرید گروه صنعتی سرکان (منتظر تخصیص)", "waiting", None, None),
                ],
            )
            cur.executemany(
                """INSERT INTO request_history
                   (request_id, action_name, actor_username, details, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                [
                    (r2, "ثبت درخواست در شرکت پادرا", "padra_expert", "درخواست خرید توسط کارشناس سفارش‌گذاری شرکت پادرا ثبت شد.", "2026-11-01 08:20:00"),
                    (r2, "ارجاع به مدیرعامل پادرا", "padra_expert", "درخواست برای مرحله ۱ جهت بررسی و تأیید به مدیرعامل شرکت پادرا (ceo_padra) ارسال شد.", "2026-11-01 08:20:03"),
                ],
            )

            # نمونه ۳: در مرحله ۳ (تأیید مدیرعامل پادرا + محول‌شده توسط مدیر خرید داخلی سرکان به کارشناس خرید ۱ - مهندس علی رضایی)
            cur.execute(
                """INSERT INTO requests
                   (request_no, request_date, request_date_fa, request_type, unit_name,
                    supply_location, fixed_asset, request_reason, requester_username, status,
                    assigned_specialist, created_at, updated_at)
                   VALUES ('PR-14050807-000003', '2026-10-29', '1405/08/07', 'equipment',
                           'کنترل کیفیت و آزمایشگاه - پادرا', 'خرید داخلی (گروه صنعتی سرکان)', 1,
                           '[فوری] خرید سنسور دیجیتال فشار تفاضلی و گیج کالیبره تست نفوذپذیری فیلتر',
                           'padra_expert', 'pending', 'kharid_expert_1', '2026-10-29 09:10:00', '2026-10-30 11:40:00')"""
            )
            r3 = cur.lastrowid
            cur.execute(
                """INSERT INTO request_items
                   (request_id, line_no, item_code, item_description, unit_name, quantity, needed_date_fa, usage_location)
                   VALUES (?, 1, 'PDR-QC-509', 'ترانسمیتر فشار تفاضلی دقیق رنج ۰ تا ۲۵۰ میلی‌بار با گواهی کالیبراسیون', 'دستگاه', 2, '1405/08/18', 'آزمایشگاه کنترل کیفیت پادرا')""",
                (r3,),
            )
            cur.executemany(
                """INSERT INTO approvals
                   (request_id, step_no, approver_username, approver_title, decision, comment, decided_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                [
                    (r3, 1, "ceo_padra", "مدیرعامل شرکت پادرا", "approved", "مورد تأیید است؛ جهت تجهیز آزمایشگاه پادرا تأمین شود.", "2026-10-29 14:00:00"),
                    (r3, 2, "serkan_purchase_mgr", "مدیر خرید داخلی گروه صنعتی سرکان", "approved", "به جناب مهندس علی رضایی (کارشناس خرید فنی و تجهیزات) محول گردید؛ لطفاً ۳ فقره استعلام بها اخذ و سریعاً خرید انجام شود.", "2026-10-30 11:40:00"),
                    (r3, 3, "kharid_expert_1", "کارشناس خرید فنی و تجهیزات (سرکان) - مهندس علی رضایی", "pending", None, None),
                ],
            )
            cur.executemany(
                """INSERT INTO request_history
                   (request_id, action_name, actor_username, details, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                [
                    (r3, "ثبت درخواست در شرکت پادرا", "padra_expert", "درخواست خرید توسط کارشناس سفارش‌گذاری شرکت پادرا ثبت شد.", "2026-10-29 09:10:00"),
                    (r3, "تأیید مدیرعامل شرکت پادرا", "ceo_padra", "تأیید مرحله ۱ · مدیرعامل شرکت پادرا — توضیح: مورد تأیید است؛ جهت تجهیز آزمایشگاه پادرا تأمین شود.", "2026-10-29 14:00:00"),
                    (r3, "محول‌سازی به مدیر خرید داخلی سرکان", "SYSTEM", "درخواست به مرحله ۲ (مدیر خرید داخلی گروه صنعتی سرکان) ارجاع شد.", "2026-10-29 14:00:02"),
                    (r3, "تخصیص و محول‌سازی به کارشناس خرید", "serkan_purchase_mgr", "توسط مدیر خرید داخلی گروه صنعتی سرکان به مهندس علی رضایی (kharid_expert_1 - کارشناس خرید فنی و تجهیزات) محول گردید — دستور: لطفاً ۳ فقره استعلام بها اخذ و سریعاً خرید انجام شود.", "2026-10-30 11:40:00"),
                ],
            )

            # نمونه ۴: تکمیل نهایی شده (تمام ۳ مرحله: مدیرعامل پادرا -> مدیر خرید داخلی سرکان -> کارشناس خرید ۲ مواد اولیه)
            cur.execute(
                """INSERT INTO requests
                   (request_no, request_date, request_date_fa, request_type, unit_name,
                    supply_location, fixed_asset, request_reason, requester_username, status,
                    assigned_specialist, created_at, updated_at)
                   VALUES ('PR-14050804-000004', '2026-10-26', '1405/08/04', 'goods',
                           'تولید و مونتاژ - پادرا', 'خرید داخلی (گروه صنعتی سرکان)', 0,
                           '[عادی] تأمین ورق گالوانیزه ۰.۸ میلی‌متر و توری مش فلزی جهت تولید پوسته فیلتر سنگین',
                           'padra_expert', 'approved', 'kharid_expert_2', '2026-10-26 09:00:00', '2026-10-28 15:45:00')"""
            )
            r4 = cur.lastrowid
            cur.executemany(
                """INSERT INTO request_items
                   (request_id, line_no, item_code, item_description, unit_name, quantity, needed_date_fa, usage_location)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (r4, 1, "PDR-RM-310", "کویل ورق گالوانیزه ضخامت ۰.۸ میلی‌متر کششی", "کیلوگرم", 2500, "1405/08/12", "سالن پرسکاری پادرا"),
                    (r4, 2, "PDR-RM-318", "توری فلزی پانچ‌شده گالوانیزه", "مترمربع", 400, "1405/08/12", "خط مونتاژ فیلتر دیزلی"),
                ],
            )
            cur.executemany(
                """INSERT INTO approvals
                   (request_id, step_no, approver_username, approver_title, decision, comment, decided_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                [
                    (r4, 1, "ceo_padra", "مدیرعامل شرکت پادرا", "approved", "مطابق بودجه تولید مهر و آبان تأیید شد.", "2026-10-26 12:30:00"),
                    (r4, 2, "serkan_purchase_mgr", "مدیر خرید داخلی گروه صنعتی سرکان", "approved", "به سرکار خانم مهندس محمدی (کارشناس خرید مواد اولیه) محول شد.", "2026-10-27 09:15:00"),
                    (r4, 3, "kharid_expert_2", "کارشناس خرید مواد اولیه و شیمیایی (سرکان) - مهندس سارا محمدی", "approved", "شماره سفارش/پیش‌فاکتور: PO-1405-9824 — از تأمین‌کننده مجاز (فولاد سازان) ثبت و حواله بارگیری به مقصد انبار پادرا صادر گردید.", "2026-10-28 15:45:00"),
                ],
            )
            cur.executemany(
                """INSERT INTO request_history
                   (request_id, action_name, actor_username, details, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                [
                    (r4, "ثبت درخواست در شرکت پادرا", "padra_expert", "درخواست خرید توسط کارشناس سفارش‌گذاری شرکت پادرا ثبت شد.", "2026-10-26 09:00:00"),
                    (r4, "تأیید مدیرعامل شرکت پادرا", "ceo_padra", "تأیید مرحله ۱ · مدیرعامل شرکت پادرا — توضیح: مطابق بودجه تولید مهر و آبان تأیید شد.", "2026-10-26 12:30:00"),
                    (r4, "تخصیص و محول‌سازی به کارشناس خرید", "serkan_purchase_mgr", "توسط مدیر خرید داخلی گروه صنعتی سرکان به مهندس سارا محمدی (kharid_expert_2) محول شد.", "2026-10-27 09:15:00"),
                    (r4, "انجام سفارش‌گذاری و تکمیل خرید", "kharid_expert_2", "تأیید مرحله ۳ · کارشناس خرید مواد اولیه سرکان — جزئیات تأمین: شماره سفارش/پیش‌فاکتور: PO-1405-9824 — از تأمین‌کننده مجاز ثبت و حواله بارگیری صادر گردید.", "2026-10-28 15:45:00"),
                    (r4, "تکمیل گردش تأیید و خرید", "SYSTEM", "فرآیند تأیید مدیرعامل پادرا، ارجاع مدیر خرید داخلی سرکان و سفارش‌گذاری کارشناس خرید با موفقیت تکمیل شد.", "2026-10-28 15:45:02"),
                ],
            )

            # نمونه ۵: سرویس و خدمات فنی در دست کارشناس خرید ۳ (مهندس سعید کریمی)
            cur.execute(
                """INSERT INTO requests
                   (request_no, request_date, request_date_fa, request_type, unit_name,
                    supply_location, fixed_asset, request_reason, requester_username, status,
                    assigned_specialist, created_at, updated_at)
                   VALUES ('PR-14050805-000005', '2026-10-27', '1405/08/05', 'service',
                           'فنی و مهندسی - پادرا', 'خرید داخلی (گروه صنعتی سرکان)', 0,
                           '[عادی] سرویس دوره‌ای و اورهال کمپرسورهای هوای فشرده اسکرو کارخانه پادرا',
                           'padra_expert', 'approved', 'kharid_expert_3', '2026-10-27 10:00:00', '2026-10-29 16:20:00')"""
            )
            r5 = cur.lastrowid
            cur.execute(
                """INSERT INTO request_items
                   (request_id, line_no, item_code, item_description, unit_name, quantity, needed_date_fa, usage_location)
                   VALUES (?, 1, 'PDR-SRV-204', 'خدمات اورهال، تعویض سپراتور و فیلتر روغن کمپرسور اسکرو ۷۵ کیلووات', 'دستگاه', 2, '1405/08/14', 'موتورخانه و تأسیسات پادرا')""",
                (r5,),
            )
            cur.executemany(
                """INSERT INTO approvals
                   (request_id, step_no, approver_username, approver_title, decision, comment, decided_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                [
                    (r5, 1, "ceo_padra", "مدیرعامل شرکت پادرا", "approved", "تأیید شد؛ پیش از شروع شیفت‌های زمستانی انجام شود.", "2026-10-27 13:10:00"),
                    (r5, 2, "serkan_purchase_mgr", "مدیر خرید داخلی گروه صنعتی سرکان", "approved", "به جناب مهندس سعید کریمی (کارشناس خرید خدمات و پیمانکاری) محول گردید.", "2026-10-28 09:30:00"),
                    (r5, 3, "kharid_expert_3", "کارشناس خرید خدمات و ملزومات عمومی (سرکان) - مهندس سعید کریمی", "approved", "شماره سفارش/پیش‌فاکتور: CNT-1405-312 — قرارداد خدمات با شرکت هواسازان صنعت منعقد و عملیات سرویس تکمیل شد.", "2026-10-29 16:20:00"),
                ],
            )
            cur.executemany(
                """INSERT INTO request_history
                   (request_id, action_name, actor_username, details, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                [
                    (r5, "ثبت درخواست در شرکت پادرا", "padra_expert", "درخواست خرید خدمات توسط کارشناس سفارش‌گذاری شرکت پادرا ثبت شد.", "2026-10-27 10:00:00"),
                    (r5, "تأیید مدیرعامل شرکت پادرا", "ceo_padra", "تأیید مرحله ۱ · مدیرعامل شرکت پادرا.", "2026-10-27 13:10:00"),
                    (r5, "تخصیص و محول‌سازی به کارشناس خرید", "serkan_purchase_mgr", "توسط مدیر خرید داخلی سرکان به مهندس سعید کریمی (kharid_expert_3) محول شد.", "2026-10-28 09:30:00"),
                    (r5, "انجام سفارش‌گذاری و تکمیل خرید", "kharid_expert_3", "تأیید مرحله ۳ · کارشناس خرید خدمات سرکان — قرارداد CNT-1405-312 منعقد و انجام شد.", "2026-10-29 16:20:00"),
                ],
            )

        conn.commit()
    finally:
        conn.close()


_init_local_sqlite_db()


def _has_sqlserver_config(auth=False):
    prefix = "AUTH_DB_" if auth else "DATA_DB_"
    return bool(os.getenv(prefix + "USER", "").strip() or os.getenv(prefix + "PASSWORD", ""))


def conn_string(auth=False):
    """Build a SQL Server connection string using the project's .env settings."""
    prefix = "AUTH_DB_" if auth else "DATA_DB_"
    server = os.getenv(prefix + "SERVER", r"192.168.120.38\odsm")
    database = os.getenv(prefix + "NAME", "bi")
    driver = os.getenv(prefix + "DRIVER", "ODBC Driver 17 for SQL Server")
    user = os.getenv(prefix + "USER", "").strip()
    password = os.getenv(prefix + "PASSWORD", "")
    encrypt = os.getenv(prefix + "ENCRYPT", "no")
    trust_certificate = os.getenv(prefix + "TRUST_SERVER_CERTIFICATE", "yes")

    if not user or not password:
        raise RuntimeError(
            f"{prefix}USER and {prefix}PASSWORD must be configured in .env; "
            "Windows/Trusted_Connection authentication is disabled."
        )

    parts = [
        f"DRIVER={{{driver}}}",
        f"SERVER={server}",
        f"DATABASE={database}",
        f"UID={user}",
        f"PWD={password}",
        f"Encrypt={encrypt}",
        f"TrustServerCertificate={trust_certificate}",
    ]
    return ";".join(parts) + ";"


def db_connect(auth=False):
    if _has_sqlserver_config(auth):
        if pyodbc is None:
            raise RuntimeError("pyodbc is not installed; install requirements-windows.txt.")
        try:
            conn = pyodbc.connect(conn_string(auth), timeout=8)
            if not auth:
                actor_username = (
                    str(current_user.username)
                    if has_request_context() and current_user.is_authenticated
                    else "SYSTEM"
                )
                context_cursor = conn.cursor()
                try:
                    context_cursor.execute(
                        "EXEC sys.sp_set_session_context @key=N'actor_username', @value=?;",
                        actor_username,
                    )
                finally:
                    context_cursor.close()
                conn.commit()
            return conn
        except Exception as exc:
            prefix = "AUTH_DB_" if auth else "DATA_DB_"
            app.logger.exception("%s SQL Server connection failed", prefix)
            raise RuntimeError(prefix + " SQL Server connection failed. Check server, database, ODBC driver, network access and SQL login credentials.") from exc
    return SQLiteCompatConnection(LOCAL_DB_PATH)


def auth_db():
    return db_connect(auth=True)


def data_db():
    return db_connect(auth=False)


def parts_db():
    """اتصال مستقل و فقط خواندنی به دیتابیس کدینگ کالا در SIGD."""
    if pyodbc is None:
        raise RuntimeError("pyodbc is not installed; install requirements-windows.txt.")
    prefix = "PARTS_DB_"
    server = os.getenv(prefix + "SERVER", r"192.168.120.38\odsm").strip()
    database = os.getenv(prefix + "NAME", "SIGD").strip() or "SIGD"
    driver = os.getenv(prefix + "DRIVER", "ODBC Driver 17 for SQL Server").strip()
    user = os.getenv(prefix + "USER", "").strip()
    password = os.getenv(prefix + "PASSWORD", "")
    encrypt = os.getenv(prefix + "ENCRYPT", "no").strip()
    trust_certificate = os.getenv(prefix + "TRUST_SERVER_CERTIFICATE", "yes").strip()
    if not user or not password:
        raise RuntimeError("PARTS_DB_USER and PARTS_DB_PASSWORD must be configured in .env.")
    connection_string = ";".join([
        f"DRIVER={{{driver}}}",
        f"SERVER={server}",
        f"DATABASE={database}",
        f"UID={user}",
        f"PWD={password}",
        f"Encrypt={encrypt}",
        f"TrustServerCertificate={trust_certificate}",
    ]) + ";"
    try:
        return pyodbc.connect(connection_string, timeout=6, readonly=True)
    except Exception as exc:
        app.logger.exception("SIGD parts database connection failed")
        raise RuntimeError("اتصال به دیتابیس SIGD برقرار نشد؛ تنظیمات PARTS_DB و دسترسی شبکه را بررسی کنید.") from exc


def normalize_digits(value):
    table = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    return str(value or "").translate(table).strip()


def validate_jalali_date(value, label):
    normalized = normalize_digits(value).replace("-", "/")
    try:
        jdatetime.datetime.strptime(normalized, "%Y/%m/%d")
    except ValueError:
        raise ValueError(f"{label} باید به‌صورت تاریخ شمسی معتبر YYYY/MM/DD باشد.")
    return normalized


def user_display_info(username: str) -> dict:
    fixed_users = {
        "i.heydari": {
            "username": username,
            "full_name": "ایمان حیدری",
            "role_title": "مدیرعامل پادرا",
            "org": "شرکت پادرا",
            "badge_color": "navy",
        },
        "m.mahdiyon": {
            "username": username,
            "full_name": "محمد مهدیون",
            "role_title": "مدیر خرید داخلی گروه صنعتی سرکان",
            "org": "گروه صنعتی سرکان",
            "badge_color": "teal",
        },
        "kharid_padra": {
            "username": username,
            "full_name": "کارشناس خرید پادرا",
            "role_title": "کارشناس خرید پادرا",
            "org": "شرکت پادرا",
            "badge_color": "blue",
        },
    }
    if username in fixed_users:
        info = dict(fixed_users[username])
        if username == "kharid_padra":
            conn = cur = None
            try:
                conn = auth_db()
                cur = conn.cursor()
                cur.execute("SELECT TOP (1) full_name FROM Project.loginusers WHERE username=?", username)
                row = cur.fetchone()
                if row and row[0]:
                    info["full_name"] = str(row[0]).strip()
            except Exception:
                app.logger.exception("Unable to load Padra requester display name")
            finally:
                if cur:
                    cur.close()
                if conn:
                    conn.close()
        return info

    info = ORGANIZATIONAL_USERS.get(username)
    if info:
        return info
    specialist = next(
        (item for item in PURCHASE_SPECIALIST_FALLBACKS if item["username"] == username),
        None,
    )
    if specialist:
        return {
            **specialist,
            "role_title": "کارشناس خرید داخلی گروه صنعتی سرکان",
            "org": "گروه صنعتی سرکان",
            "badge_color": "teal",
        }

    conn = cur = None
    try:
        conn = auth_db()
        cur = conn.cursor()
        cur.execute("SELECT TOP (1) full_name FROM Project.loginusers WHERE username=?", username)
        row = cur.fetchone()
        if row and row[0]:
            return {
                "username": username,
                "full_name": str(row[0]).strip(),
                "role_title": "کارشناس خرید داخلی گروه صنعتی سرکان",
                "org": "گروه صنعتی سرکان",
                "badge_color": "teal",
            }
    except Exception:
        app.logger.exception("Unable to load organizational user display name")
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()
    return {
        "username": username,
        "full_name": username,
        "role_title": "کاربر سازمانی",
        "org": "شرکت پادرا / گروه صنعتی سرکان",
    }


def compute_bi_analytics(cur) -> dict:
    """محاسبه شاخص‌های تحلیلی هوش تجاری (BI) و بار کاری کارشناسان خرید سرکان."""
    cur.execute(
        """SELECT a.step_no, COUNT(DISTINCT a.request_id)
           FROM Project.approvals a
           INNER JOIN Project.requests r ON r.id = a.request_id
           WHERE r.status = 'pending' AND a.decision = 'pending'
             AND (
                 (a.step_no = 1 AND r.workflow_type = 'padra')
                 OR (a.step_no IN (2,3) AND r.workflow_type IN ('serkan','padra'))
             )
           GROUP BY a.step_no"""
    )
    stage_Waiting = {1: 0, 2: 0, 3: 0}
    for step_no, cnt in cur.fetchall():
        stage_Waiting[int(step_no)] = int(cnt)

    specialist_workloads = {}
    for sp in get_purchase_specialists():
        uname = sp["username"]
        cur.execute(
            """SELECT
                 SUM(CASE WHEN ia.assignment_status = 'assigned' THEN 1 ELSE 0 END),
                 SUM(CASE WHEN ia.assignment_status = 'completed' THEN 1 ELSE 0 END)
               FROM Project.request_item_assignments ia
               WHERE ia.specialist_username = ?""",
            uname,
        )
        row = cur.fetchone()
        active_cnt = int(row[0] or 0) if row else 0
        done_cnt = int(row[1] or 0) if row else 0
        specialist_workloads[uname] = {
            "active": active_cnt,
            "completed": done_cnt,
            "total": active_cnt + done_cnt,
        }

    return {
        "step1_ceo_padra": stage_Waiting.get(1, 0),
        "step2_mgr_serkan": stage_Waiting.get(2, 0),
        "step3_specialists": stage_Waiting.get(3, 0),
        "specialist_workloads": specialist_workloads,
    }


class User(UserMixin):
    def __init__(self, user_id, username):
        self.id = str(user_id)
        self.username = str(username)
        info = user_display_info(self.username)
        self.full_name = info["full_name"]
        self.role_title = info["role_title"]
        self.org = info["org"]


@login_manager.user_loader
def load_user(user_id):
    conn = cur = None
    try:
        conn = auth_db()
        cur = conn.cursor()
        cur.execute("SELECT TOP (1) Id, Username FROM [Project].[loginusers] WHERE CAST(Id AS NVARCHAR(100)) = ?", user_id)
        row = cur.fetchone()
        return User(row[0], row[1]) if row else None
    except Exception:
        app.logger.exception("Unable to load session user")
        return None
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def current_iam_permissions():
    if not current_user.is_authenticated:
        return set()
    cached = getattr(g, "_iam_permissions", None)
    if cached is not None:
        return cached
    conn = cur = None
    try:
        conn = auth_db()
        cur = conn.cursor()
        cur.execute(
            """SELECT DISTINCT p.permission_key
               FROM Project.IAM_UserRoles ur
               INNER JOIN Project.IAM_Roles r ON r.id = ur.role_id AND r.is_active = 1
               INNER JOIN Project.IAM_RolePermissions rp ON rp.role_id = r.id
               INNER JOIN Project.IAM_Permissions p ON p.id = rp.permission_id AND p.is_active = 1
               INNER JOIN Project.IAM_Projects pr ON pr.id = p.project_id AND pr.is_active = 1
               WHERE ur.user_id = ?
                 AND (ur.valid_from IS NULL OR ur.valid_from <= SYSDATETIME())
                 AND (ur.valid_until IS NULL OR ur.valid_until > SYSDATETIME())
                 AND pr.project_key = 'padra-purchase-request'""",
            int(current_user.id),
        )
        permissions = {str(row[0]) for row in cur.fetchall()}
        g._iam_permissions = permissions
        return permissions
    except Exception:
        app.logger.exception("Unable to load permissions from central IAM; access denied")
        g._iam_permissions = set()
        return set()
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def has_iam_permission(permission_key):
    return permission_key in current_iam_permissions()


def request_visibility_clause(permissions):
    clauses = []
    params = []
    if "requests.view_all" in permissions:
        return "1=1", []
    if "requests.view_padra" in permissions:
        clauses.append("r.workflow_type = 'padra'")
    if "requests.view_team" in permissions:
        clauses.append(
            """(r.workflow_type IN ('serkan','padra') AND EXISTS (
                SELECT 1 FROM Project.approvals ax
                WHERE ax.request_id = r.id
                  AND (
                    (ax.step_no = 2 AND ax.approver_username = ?)
                    OR (ax.step_no = 3 AND ax.approver_username <> N'UNASSIGNED_SPECIALIST')
                  )
            ))"""
        )
        params.append(current_user.username)
    if "requests.view_own" in permissions:
        clauses.append("(r.workflow_type = 'padra' AND r.requester_username = ?)")
        params.append(current_user.username)
    if "requests.view_assigned" in permissions:
        clauses.append(
            """(r.workflow_type IN ('serkan','padra') AND (
                EXISTS (
                    SELECT 1 FROM Project.request_item_assignments ia
                    WHERE ia.request_id=r.id AND ia.specialist_username=?
                )
                OR EXISTS (
                    SELECT 1 FROM Project.approvals ax
                    WHERE ax.request_id=r.id AND ax.step_no=3
                      AND ax.approver_username=?
                )
            ))"""
        )
        params.extend([current_user.username, current_user.username])
    if not clauses:
        abort(403)
    return "(" + " OR ".join(clauses) + ")", params


@app.context_processor
def inject_organizational_context():
    return {
        "org_users": ORGANIZATIONAL_USERS,
        "purchase_specialists": get_purchase_specialists(),
        "user_info": user_display_info,
        "current_user_info": user_display_info(current_user.username) if current_user.is_authenticated else None,
        "has_iam_permission": has_iam_permission,
    }


def verify_user_by_username(username):
    conn = cur = None
    try:
        conn = auth_db()
        cur = conn.cursor()
        cur.execute("SELECT TOP (1) id, username FROM [Project].[loginusers] WHERE username = ?", username)
        row = cur.fetchone()
        return User(row[0], row[1]) if row else None
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def verify_user(username, password):
    conn = cur = None
    try:
        conn = auth_db()
        cur = conn.cursor()
        cur.execute(
            """SELECT TOP (1) id, username FROM [Project].[loginusers]
               WHERE username = ? AND password_hash = ?""",
            username,
            password,
        )
        row = cur.fetchone()
        return User(row[0], row[1]) if row else None
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def jalali_today():
    conn = data_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT GETDATE()")
        return jdatetime.datetime.fromgregorian(datetime=cur.fetchone()[0]).strftime("%Y/%m/%d")
    finally:
        conn.close()


def jalali_datetime(value):
    if not value:
        return ""
    try:
        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if isinstance(value, date) and not isinstance(value, datetime):
            value = datetime.combine(value, datetime.min.time())
        return jdatetime.datetime.fromgregorian(datetime=value).strftime("%Y/%m/%d · %H:%M")
    except (TypeError, ValueError):
        return str(value)


app.jinja_env.filters["jalali_datetime"] = jalali_datetime


def get_request(request_id):
    conn = data_db()
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT id, request_no, request_date_fa, request_type, unit_name, supply_location,
                      fixed_asset, request_reason, requester_username, status, created_at, updated_at,
                      workflow_type, assigned_specialist
               FROM Project.requests WHERE id=?""",
            request_id,
        )
        header = cur.fetchone()
        if not header:
            return None
        cur.execute(
            """SELECT line_no,item_code,item_description,unit_name,quantity,needed_date_fa,usage_location,id
               FROM Project.request_items WHERE request_id=? ORDER BY line_no""",
            request_id,
        )
        items = cur.fetchall()
        is_item_specialist = (
            has_iam_permission("requests.view_assigned")
            and not has_iam_permission("requests.view_all")
            and not has_iam_permission("requests.view_team")
            and not has_iam_permission("requests.view_padra")
        )
        cur.execute(
            """SELECT a.id,a.request_item_id,a.specialist_username,a.assignment_status,
                      a.assigned_by,a.assigned_at,a.completed_by,a.completed_at,a.completion_comment,
                      i.line_no,i.item_code,i.item_description,i.unit_name,i.quantity,
                      i.needed_date_fa,i.usage_location
               FROM Project.request_item_assignments a
               INNER JOIN Project.request_items i ON i.id=a.request_item_id
               WHERE a.request_id=?
               ORDER BY i.line_no""",
            request_id,
        )
        item_assignments = cur.fetchall()
        if is_item_specialist:
            item_assignments = [a for a in item_assignments if a[2] == current_user.username]
            visible_item_ids = {a[1] for a in item_assignments}
            items = [item for item in items if item[7] in visible_item_ids]
        assignment_map = {a[1]: a[2] for a in item_assignments}
        cur.execute(
            """SELECT step_no,approver_username,approver_title,decision,comment,decided_at
               FROM Project.approvals WHERE request_id=? ORDER BY step_no""",
            request_id,
        )
        approvals = cur.fetchall()
        cur.execute(
            """SELECT action_name,actor_username,details,created_at FROM Project.request_history
               WHERE request_id=? ORDER BY created_at,id""",
            request_id,
        )
        history = cur.fetchall()
        if is_item_specialist:
            history = []
        audit_log = []
        can_view_audit = (
            current_user.is_authenticated
            and current_user.username != "kharid_padra"
            and (
                has_iam_permission("requests.view_all")
                or has_iam_permission("requests.assign_specialist")
                or has_iam_permission("requests.view_padra")
            )
        )
        if can_view_audit:
            try:
                cur.execute(
                    """SELECT action_type,entity_name,record_id,stage_no,old_values,new_values,
                              actor_username,created_at
                       FROM Project.request_audit_log
                       WHERE request_id=? ORDER BY created_at DESC,id DESC""",
                    request_id,
                )
                audit_log = cur.fetchall()
            except Exception:
                app.logger.warning("Request audit table is not installed yet; run sql/009_request_audit_trail.sql.")
        item_scope = (
            " AND o.request_item_id IN (SELECT request_item_id FROM Project.request_item_assignments WHERE request_id=? AND specialist_username=?)"
            if is_item_specialist else ""
        )
        offer_params = (request_id, request_id, current_user.username) if is_item_specialist else (request_id,)
        cur.execute(
            """SELECT o.supplier_name,o.proforma_number,o.delivery_date_fa,
                      i.item_description,o.quantity,o.unit_price,o.payment_method,
                      o.created_by,o.created_at,o.id,o.is_selected,o.request_item_id
               FROM Project.request_supplier_offers o
               INNER JOIN Project.request_items i ON i.id=o.request_item_id
               WHERE o.request_id=?""" + item_scope + " ORDER BY o.id",
            *offer_params,
        )
        supplier_offers = cur.fetchall()
        delivery_scope = (
            " AND o.request_item_id IN (SELECT request_item_id FROM Project.request_item_assignments WHERE request_id=? AND specialist_username=?)"
            if is_item_specialist else ""
        )
        delivery_params = (request_id, request_id, current_user.username) if is_item_specialist else (request_id,)
        cur.execute(
            """SELECT d.id,d.receipt_no,d.delivery_date_fa,d.quantity,d.recorded_by,d.recorded_at,
                      o.supplier_name,i.item_description,o.id
               FROM Project.request_deliveries d
               INNER JOIN Project.request_supplier_offers o ON o.id=d.offer_id
               INNER JOIN Project.request_items i ON i.id=o.request_item_id
               WHERE d.request_id=?""" + delivery_scope + " ORDER BY d.delivery_date_fa,d.id",
            *delivery_params,
        )
        delivery_rows = cur.fetchall()
        progress_scope = (
            " AND o.request_item_id IN (SELECT request_item_id FROM Project.request_item_assignments WHERE request_id=? AND specialist_username=?)"
            if is_item_specialist else ""
        )
        progress_params = (request_id, request_id, current_user.username) if is_item_specialist else (request_id,)
        cur.execute(
            """SELECT o.id,o.supplier_name,i.item_description,i.unit_name,o.delivery_date_fa,o.quantity,
                      COALESCE(SUM(d.quantity),0) AS delivered_quantity
               FROM Project.request_supplier_offers o
               INNER JOIN Project.request_items i ON i.id=o.request_item_id
               LEFT JOIN Project.request_deliveries d ON d.offer_id=o.id
               WHERE o.request_id=? AND o.is_selected=1""" + progress_scope + """
               GROUP BY o.id,o.supplier_name,i.item_description,i.unit_name,o.delivery_date_fa,o.quantity,i.line_no
               ORDER BY i.line_no,o.id""",
            *progress_params,
        )
        delivery_progress = cur.fetchall()
        bi_stats = compute_bi_analytics(cur) if has_iam_permission("requests.assign_specialist") else None
        return {
            "header": header,
            "items": items,
            "item_assignments": item_assignments,
            "assignment_map": assignment_map,
            "is_item_specialist": is_item_specialist,
            "approvals": approvals,
            "history": history,
            "audit_log": audit_log,
            "supplier_offers": supplier_offers,
            "delivery_rows": delivery_rows,
            "delivery_progress": delivery_progress,
            "bi_stats": bi_stats,
        }
    finally:
        conn.close()


@login_manager.unauthorized_handler
def unauthorized():
    return redirect(url_for("login", next=request.url))


@app.route("/switch-user/<username>")
def switch_user(username):
    global _FALLBACK_ACTIVE_USERNAME
    # Account switching is only a local preview helper; never expose it with SQL authentication.
    if _has_sqlserver_config(auth=True):
        abort(403)
    specialist_usernames = {item["username"] for item in get_purchase_specialists()}
    if username not in ORGANIZATIONAL_USERS and username not in specialist_usernames:
        abort(404)
    user = verify_user_by_username(username)
    if user:
        _FALLBACK_ACTIVE_USERNAME = username
        session.pop("explicit_logout", None)
        login_user(user, remember=False)
        info = user_display_info(username)
        flash(f"کارتابل فعال به «{info['role_title']} ({info['full_name']})» تغییر یافت.", "success")
    else:
        flash("حساب کاربری موردنظر در سامانه احراز هویت پیدا نشد.", "error")
    next_url = request.args.get("next") or request.referrer or url_for("index")
    if "/login" in next_url:
        next_url = url_for("index")
    return redirect(next_url)


@app.route("/login", methods=["GET", "POST"])
def login():
    global _FALLBACK_ACTIVE_USERNAME
    if request.method == "POST":
        username, password = request.form.get("username", "").strip(), request.form.get("password", "")
        try:
            user = verify_user(username, password)
            if user:
                _FALLBACK_ACTIVE_USERNAME = user.username
                session.pop("explicit_logout", None)
                login_user(user, remember=False)
                return redirect(request.args.get("next") or url_for("index"))
            flash("نام کاربری یا رمز عبور صحیح نیست.", "error")
        except Exception:
            app.logger.exception("Login failed")
            flash("ارتباط با پایگاه داده کاربران برقرار نشد؛ تنظیمات اتصال دیتابیس را بررسی کنید.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session["explicit_logout"] = True
    logout_user()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    conn = data_db()
    try:
        cur = conn.cursor()
        status_filter = request.args.get("status", "").strip()
        type_filter = request.args.get("req_type", "").strip()
        search_text = request.args.get("q", "").strip()[:80]
        allowed_statuses = {"pending", "submitted", "approved", "rejected", "cancelled", "my_turn"}
        if status_filter not in allowed_statuses:
            status_filter = ""
        if type_filter not in {"goods", "equipment", "service", "other"}:
            type_filter = ""

        permissions = current_iam_permissions()
        visibility, vis_params = request_visibility_clause(permissions)
        approval_steps = {
            step for step in (1, 2, 3)
            if f"requests.approve.step{step}" in permissions
        }
        if "requests.approve.padra" in permissions:
            approval_steps.add(1)

        cur.execute(
            f"""SELECT r.status,COUNT(*) FROM Project.requests r
                WHERE {visibility} GROUP BY r.status""",
            *vis_params,
        )
        counts = {key: 0 for key in ("all", "pending", "submitted", "approved", "rejected", "cancelled", "my_turn")}
        for status_name, total in cur.fetchall():
            counts["all"] += total
            if status_name in counts:
                counts[status_name] = total

        counts["my_turn"] = 0
        if approval_steps:
            step_placeholders = ",".join("?" for _ in approval_steps)
            cur.execute(
                f"""SELECT COUNT(DISTINCT r.id)
                    FROM Project.requests r
                    INNER JOIN Project.approvals a ON a.request_id = r.id
                    WHERE r.status = 'pending' AND a.decision = 'pending'
                      AND a.approver_username = ? AND a.step_no IN ({step_placeholders})""",
                current_user.username,
                *sorted(approval_steps),
            )
            counts["my_turn"] += cur.fetchone()[0] or 0
        if "requests.view_assigned" in permissions:
            cur.execute(
                """SELECT COUNT(DISTINCT r.id)
                   FROM Project.requests r
                   INNER JOIN Project.request_item_assignments ia ON ia.request_id=r.id
                   WHERE r.status='pending' AND ia.specialist_username=?
                     AND ia.assignment_status='assigned'""",
                current_user.username,
            )
            counts["my_turn"] += cur.fetchone()[0] or 0
        bi_stats = compute_bi_analytics(cur) if "requests.assign_specialist" in permissions else None

        where = visibility
        params = list(vis_params)
        if status_filter == "my_turn":
            turn_clauses = []
            if approval_steps:
                step_placeholders = ",".join("?" for _ in approval_steps)
                turn_clauses.append(f"""EXISTS (
                    SELECT 1 FROM Project.approvals am
                    WHERE am.request_id=r.id AND am.decision='pending'
                      AND am.approver_username=? AND am.step_no IN ({step_placeholders}))""")
                params.append(current_user.username)
                params.extend(sorted(approval_steps))
            if "requests.view_assigned" in permissions:
                turn_clauses.append("""EXISTS (
                    SELECT 1 FROM Project.request_item_assignments iam
                    WHERE iam.request_id=r.id AND iam.specialist_username=?
                      AND iam.assignment_status='assigned')""")
                params.append(current_user.username)
            if not turn_clauses:
                abort(403)
            where += " AND r.status='pending' AND (" + " OR ".join(turn_clauses) + ")"
        elif status_filter:
            where += " AND r.status=?"
            params.append(status_filter)

        if type_filter:
            where += " AND r.request_type=?"
            params.append(type_filter)

        if search_text:
            where += """ AND (
                         r.request_no LIKE ? OR r.unit_name LIKE ?
                         OR r.requester_username LIKE ? OR r.request_reason LIKE ?
                         OR EXISTS (
                             SELECT 1 FROM Project.request_items si
                             WHERE si.request_id = r.id
                               AND (si.item_code LIKE ? OR si.item_description LIKE ?)
                         )
                     )"""
            like = f"%{search_text}%"
            params.extend([like, like, like, like, like, like])

        cur.execute(
            f"""SELECT TOP (100)
                    r.id,r.request_no,r.request_date_fa,r.request_type,r.unit_name,
                    r.status,r.created_at,r.requester_username,r.updated_at,
                    COALESCE(p.approver_username,N'') AS current_approver,
                    COALESCE(p.approver_title,N'') AS current_title,
                    COALESCE(p.step_no,0) AS current_step,
                    COALESCE(st.total_steps,0) AS total_steps,
                    COALESCE(it.item_count,0) AS item_count,
                    COALESCE(r.request_reason,N'') AS request_reason,
                    r.workflow_type,
                    COALESCE(search_items.item_search_text,N'') AS item_search_text
               FROM Project.requests r
               OUTER APPLY (
                   SELECT TOP (1) a.approver_username,a.approver_title,a.step_no
                   FROM Project.approvals a
                   WHERE a.request_id=r.id AND a.decision='pending'
                   ORDER BY a.step_no
               ) p
               OUTER APPLY (
                   SELECT COUNT(*) AS total_steps
                   FROM Project.approvals a WHERE a.request_id=r.id
               ) st
               OUTER APPLY (
                   SELECT COUNT(*) AS item_count
                   FROM Project.request_items i WHERE i.request_id=r.id
               ) it
               OUTER APPLY (
                   SELECT STUFF((
                       SELECT N' ' + COALESCE(CONVERT(NVARCHAR(200),si.item_code),N'')
                              + N' ' + COALESCE(si.item_description,N'')
                       FROM Project.request_items si
                       WHERE si.request_id=r.id
                       FOR XML PATH(''), TYPE
                   ).value('.', 'NVARCHAR(MAX)'),1,1,N'') AS item_search_text
               ) search_items
               WHERE {where}
               ORDER BY r.id DESC""",
            *params,
        )
        rows = cur.fetchall()
        delivery_rows = []
        delivery_summary = {"items": 0, "complete": 0, "pending": 0, "late": 0}
        request_delivery_status = {}
        if any(key in permissions for key in ("requests.view_padra", "requests.view_team", "requests.view_all")):
            cur.execute(
                f"""SELECT r.id,r.request_no,r.unit_name,r.status,o.supplier_name,o.delivery_date_fa,
                           i.item_description,i.unit_name,o.quantity,COALESCE(d.delivered_quantity,0)
                    FROM Project.requests r
                    INNER JOIN Project.request_supplier_offers o ON o.request_id=r.id AND o.is_selected=1
                    INNER JOIN Project.request_items i ON i.id=o.request_item_id
                    LEFT JOIN (
                        SELECT offer_id,SUM(quantity) AS delivered_quantity
                        FROM Project.request_deliveries GROUP BY offer_id
                    ) d ON d.offer_id=o.id
                    WHERE {visibility}
                    ORDER BY r.id DESC,i.line_no""",
                *vis_params,
            )
            today_fa = jalali_today()
            for row in cur.fetchall():
                ordered_qty = float(row[8] or 0)
                delivered_qty = float(row[9] or 0)
                remaining_qty = max(ordered_qty - delivered_qty, 0)
                is_late = remaining_qty > 0 and bool(row[5]) and str(row[5]) < today_fa
                delivery_rows.append({
                    "request_id": row[0], "request_no": row[1], "unit_name": row[2],
                    "status": row[3], "supplier": row[4], "expected_date": row[5],
                    "item": row[6], "unit": row[7], "ordered": ordered_qty, "delivered": delivered_qty,
                    "remaining": remaining_qty, "late": is_late,
                })
                delivery_summary["items"] += 1
                if remaining_qty <= 0:
                    delivery_summary["complete"] += 1
                else:
                    delivery_summary["pending"] += 1
                if is_late:
                    delivery_summary["late"] += 1
            cur.execute(
                """SELECT o.request_id,SUM(o.quantity),SUM(COALESCE(d.delivered_quantity,0))
                   FROM Project.request_supplier_offers o
                   LEFT JOIN (
                       SELECT offer_id,SUM(quantity) AS delivered_quantity
                       FROM Project.request_deliveries GROUP BY offer_id
                   ) d ON d.offer_id=o.id
                   WHERE o.is_selected=1
                   GROUP BY o.request_id"""
            )
            for req_id, ordered_total, delivered_total in cur.fetchall():
                ordered_total = float(ordered_total or 0)
                delivered_total = float(delivered_total or 0)
                if delivered_total >= ordered_total:
                    request_delivery_status[req_id] = "تحویل شده"
                elif delivered_total > 0:
                    request_delivery_status[req_id] = "در حال تحویل"
                else:
                    request_delivery_status[req_id] = "سفارش‌گذاری شده"
        return render_template(
            "index.html",
            rows=rows,
            username=current_user.username,
            counts=counts,
            bi_stats=bi_stats,
            delivery_rows=delivery_rows,
            delivery_summary=delivery_summary,
            request_delivery_status=request_delivery_status,
            status_filter=status_filter,
            type_filter=type_filter,
            search_text=search_text,
        )
    finally:
        conn.close()



def _load_item_report(cur, args, summary_mode=None, visibility=None, visibility_params=None):
    filters = {
        "q": args.get("q", "").strip()[:100],
        "request_type": args.get("request_type", "").strip(),
        "status": args.get("status", "").strip(),
        "specialist": args.get("specialist", "").strip(),
        "assignment_status": args.get("assignment_status", "").strip(),
        "from_date": args.get("from_date", "").strip(),
        "to_date": args.get("to_date", "").strip(),
        "item_code": args.get("item_code", "").strip()[:80],
        "unit": args.get("unit", "").strip()[:100],
        "requester": args.get("requester", "").strip()[:100],
        "supplier": args.get("supplier", "").strip()[:120],
    }
    clauses = []
    params = []
    if filters["request_type"] in {"goods", "equipment", "service", "other"}:
        clauses.append("r.request_type=?")
        params.append(filters["request_type"])
    else:
        filters["request_type"] = ""
    if filters["status"] in {"pending", "submitted", "approved", "rejected", "cancelled"}:
        clauses.append("r.status=?")
        params.append(filters["status"])
    else:
        filters["status"] = ""
    if filters["specialist"]:
        clauses.append("ia.specialist_username=?")
        params.append(filters["specialist"])
    if filters["assignment_status"] in {"assigned", "completed", "rejected", "unassigned"}:
        if filters["assignment_status"] == "unassigned":
            clauses.append("ia.id IS NULL")
        else:
            clauses.append("ia.assignment_status=?")
            params.append(filters["assignment_status"])
    else:
        filters["assignment_status"] = ""
    if filters["from_date"]:
        clauses.append("r.request_date_fa>=?")
        params.append(filters["from_date"])
    if filters["to_date"]:
        clauses.append("r.request_date_fa<=?")
        params.append(filters["to_date"])
    if filters["item_code"]:
        clauses.append("i.item_code LIKE ?")
        params.append("%" + filters["item_code"] + "%")
    if filters["unit"]:
        clauses.append("r.unit_name LIKE ?")
        params.append("%" + filters["unit"] + "%")
    if filters["requester"]:
        clauses.append("r.requester_username LIKE ?")
        params.append("%" + filters["requester"] + "%")
    if filters["supplier"]:
        clauses.append("""EXISTS (
            SELECT 1 FROM Project.request_supplier_offers sf
            WHERE sf.request_item_id=i.id AND sf.supplier_name LIKE ?
        )""")
        params.append("%" + filters["supplier"] + "%")
    if filters["q"]:
        like = "%" + filters["q"] + "%"
        clauses.append("""(
            r.request_no LIKE ? OR r.unit_name LIKE ? OR r.requester_username LIKE ?
            OR i.item_code LIKE ? OR i.item_description LIKE ?
            OR ia.specialist_username LIKE ?
            OR EXISTS (SELECT 1 FROM Project.request_supplier_offers sx
                       WHERE sx.request_item_id=i.id AND sx.supplier_name LIKE ?)
        )""")
        params.extend([like] * 7)
    if visibility:
        clauses.append(f"({visibility})")
        params.extend(visibility_params or [])
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    sql = """
        SELECT TOP (5000)
            r.id,r.request_no,r.request_date_fa,r.request_type,r.unit_name,
            r.requester_username,r.status,i.id,i.line_no,i.item_code,
            i.item_description,i.unit_name,i.quantity,i.needed_date_fa,
            COALESCE(ia.specialist_username,N'') AS specialist_username,
            COALESCE(ia.assignment_status,'unassigned') AS assignment_status,
            COALESCE(ofr.offer_count,0) AS offer_count,
            COALESCE(ofr.ordered_quantity,0) AS ordered_quantity,
            COALESCE(del.delivered_quantity,0) AS delivered_quantity,
            COALESCE(ofr.selected_supplier,N'') AS selected_supplier
        FROM Project.requests r
        INNER JOIN Project.request_items i ON i.request_id=r.id
        LEFT JOIN Project.request_item_assignments ia ON ia.request_item_id=i.id
        OUTER APPLY (
            SELECT COUNT(*) AS offer_count,
                   SUM(CASE WHEN o.is_selected=1 THEN o.quantity ELSE 0 END) AS ordered_quantity,
                   MAX(CASE WHEN o.is_selected=1 THEN o.supplier_name END) AS selected_supplier
            FROM Project.request_supplier_offers o
            WHERE o.request_item_id=i.id
        ) ofr
        OUTER APPLY (
            SELECT SUM(d.quantity) AS delivered_quantity
            FROM Project.request_deliveries d
            INNER JOIN Project.request_supplier_offers od ON od.id=d.offer_id
            WHERE od.request_item_id=i.id
        ) del
    """ + where + " ORDER BY r.id DESC,i.line_no"
    if summary_mode == "products":
        summary_sql = """
            SELECT
                COALESCE(i.item_code,N'') AS item_code,
                i.item_description,
                i.unit_name,
                COUNT(*) AS item_request_count,
                COUNT(DISTINCT r.id) AS distinct_request_count,
                SUM(i.quantity) AS requested_quantity,
                SUM(COALESCE(ofr.ordered_quantity,0)) AS ordered_quantity,
                SUM(COALESCE(del.delivered_quantity,0)) AS delivered_quantity
            FROM Project.requests r
            INNER JOIN Project.request_items i ON i.request_id=r.id
            LEFT JOIN Project.request_item_assignments ia ON ia.request_item_id=i.id
            OUTER APPLY (
                SELECT COUNT(*) AS offer_count,
                       SUM(CASE WHEN o.is_selected=1 THEN o.quantity ELSE 0 END) AS ordered_quantity
                FROM Project.request_supplier_offers o
                WHERE o.request_item_id=i.id
            ) ofr
            OUTER APPLY (
                SELECT SUM(d.quantity) AS delivered_quantity
                FROM Project.request_deliveries d
                INNER JOIN Project.request_supplier_offers od ON od.id=d.offer_id
                WHERE od.request_item_id=i.id
            ) del
        """ + where + """
            GROUP BY i.item_code,i.item_description,i.unit_name
            ORDER BY COUNT(*) DESC,i.item_description
        """
        cur.execute(summary_sql, *params)
        return cur.fetchall(), filters

    if summary_mode == "totals":
        totals_sql = """
            SELECT COUNT(DISTINCT r.id),COUNT(*),
                   SUM(i.quantity),
                   SUM(COALESCE(ofr.ordered_quantity,0)),
                   SUM(COALESCE(del.delivered_quantity,0))
            FROM Project.requests r
            INNER JOIN Project.request_items i ON i.request_id=r.id
            LEFT JOIN Project.request_item_assignments ia ON ia.request_item_id=i.id
            OUTER APPLY (
                SELECT SUM(CASE WHEN o.is_selected=1 THEN o.quantity ELSE 0 END) AS ordered_quantity
                FROM Project.request_supplier_offers o
                WHERE o.request_item_id=i.id
            ) ofr
            OUTER APPLY (
                SELECT SUM(d.quantity) AS delivered_quantity
                FROM Project.request_deliveries d
                INNER JOIN Project.request_supplier_offers od ON od.id=d.offer_id
                WHERE od.request_item_id=i.id
            ) del
        """ + where
        cur.execute(totals_sql, *params)
        return cur.fetchone(), filters

    cur.execute(sql, *params)
    rows = cur.fetchall()
    return rows, filters


def report_access_scope():
    permissions = current_iam_permissions()
    if "requests.export" not in permissions:
        abort(403)
    if "requests.view_all" in permissions:
        return "1=1", []
    if "requests.view_padra" in permissions or "requests.view_team" in permissions:
        return "1=1", []
    if "requests.view_assigned" in permissions or "requests.view_own" in permissions:
        return request_visibility_clause(permissions)
    abort(403)


@app.route("/reports")
@login_required
def reports():
    visibility, visibility_params = report_access_scope()
    conn = data_db()
    try:
        cur = conn.cursor()
        rows, filters = _load_item_report(cur, request.args, visibility=visibility, visibility_params=visibility_params)
        product_summary, _ = _load_item_report(cur, request.args, summary_mode="products", visibility=visibility, visibility_params=visibility_params)
        totals, _ = _load_item_report(cur, request.args, summary_mode="totals", visibility=visibility, visibility_params=visibility_params)
        specialists = get_purchase_specialists()
        report_user_names = {}
        for report_row in rows:
            for report_username in (report_row[5], report_row[14]):
                if report_username and report_username not in report_user_names:
                    report_user_names[report_username] = user_display_info(report_username)["full_name"]
        summary = {
            "requests": int(totals[0] or 0),
            "items": int(totals[1] or 0),
            "requested_quantity": float(totals[2] or 0),
            "ordered_quantity": float(totals[3] or 0),
            "delivered_quantity": float(totals[4] or 0),
        }
        return render_template(
            "reports.html", rows=rows, filters=filters,
            specialists=specialists, summary=summary,
            product_summary=product_summary,
            report_user_names=report_user_names,
        )
    finally:
        conn.close()


@app.route("/reports/products.csv")
@login_required
def export_product_summary():
    visibility, visibility_params = report_access_scope()
    conn = data_db()
    try:
        cur = conn.cursor()
        rows, _filters = _load_item_report(cur, request.args, summary_mode="products", visibility=visibility, visibility_params=visibility_params)
        buf = io.StringIO()
        buf.write("\ufeff")
        writer = csv.writer(buf)
        writer.writerow([
            "کد کالا","شرح کالا","واحد","تعداد دفعات درخواست",
            "تعداد درخواست مستقل","جمع مقدار درخواستی","جمع سفارش قطعی","جمع تحویل‌شده"
        ])
        for row in rows:
            writer.writerow(row)
        return Response(
            buf.getvalue(), mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition":"attachment; filename=padra_product_summary.csv"},
        )
    finally:
        conn.close()


@app.route("/reports/items.csv")
@login_required
def export_item_report():
    visibility, visibility_params = report_access_scope()
    conn = data_db()
    try:
        cur = conn.cursor()
        rows, filters = _load_item_report(cur, request.args, visibility=visibility, visibility_params=visibility_params)
        buf = io.StringIO()
        buf.write("\ufeff")
        writer = csv.writer(buf)
        writer.writerow([
            "شماره درخواست","تاریخ درخواست","نوع درخواست","واحد درخواست‌کننده",
            "ثبت‌کننده","وضعیت درخواست","ردیف","کد کالا","شرح کالا","واحد",
            "مقدار درخواستی","تاریخ نیاز","کارشناس خرید","وضعیت تخصیص",
            "تعداد استعلام","مقدار سفارش","مقدار تحویل","تأمین‌کننده منتخب"
        ])
        type_map = {"goods":"مواد اولیه","equipment":"تجهیزات","service":"خدمات","other":"سایر"}
        status_map = {"pending":"در جریان","submitted":"ثبت‌شده","approved":"تکمیل","rejected":"رد شده","cancelled":"لغو شده"}
        assignment_map = {"assigned":"در حال اقدام","completed":"تکمیل‌شده","rejected":"رد شده","unassigned":"بدون تخصیص"}
        report_user_names = {}
        for report_row in rows:
            for report_username in (report_row[5], report_row[14]):
                if report_username and report_username not in report_user_names:
                    report_user_names[report_username] = user_display_info(report_username)["full_name"]
        for row in rows:
            writer.writerow([
                row[1],row[2],type_map.get(row[3],row[3]),row[4],
                report_user_names.get(row[5], row[5]),status_map.get(row[6],row[6]),
                row[8],row[9],row[10],row[11],row[12],row[13],
                report_user_names.get(row[14], "—") if row[14] else "—",
                assignment_map.get(row[15],row[15]),row[16],row[17],row[18],row[19]
            ])
        return Response(
            buf.getvalue(), mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition":"attachment; filename=padra_item_level_report.csv"},
        )
    finally:
        conn.close()


@app.route("/export/csv")
@login_required
def export_csv():
    """خروجی اکسل (CSV با UTF-8 BOM) ویژه گزارش‌گیری مدیریتی واحد هوش تجاری سرکان."""
    if not has_iam_permission("requests.export") or not has_iam_permission("requests.view_all"):
        abort(403)
    conn = data_db()
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT TOP (500)
                    r.id,r.request_no,r.request_date_fa,r.request_type,r.unit_name,
                    r.status,r.created_at,r.requester_username,r.updated_at,
                    COALESCE(p.approver_username,N'') AS current_approver,
                    COALESCE(p.approver_title,N'') AS current_title,
                    COALESCE(p.step_no,0) AS current_step,
                    COALESCE(st.total_steps,0) AS total_steps,
                    COALESCE(it.item_count,0) AS item_count
               FROM Project.requests r
               OUTER APPLY (
                   SELECT TOP (1) a.approver_username,a.approver_title,a.step_no
                   FROM Project.approvals a
                   WHERE a.request_id=r.id AND a.decision='pending'
                   ORDER BY a.step_no
               ) p
               OUTER APPLY (
                   SELECT COUNT(*) AS total_steps
                   FROM Project.approvals a WHERE a.request_id=r.id
               ) st
               OUTER APPLY (
                   SELECT COUNT(*) AS item_count
                   FROM Project.request_items i WHERE i.request_id=r.id
               ) it
               ORDER BY r.id DESC"""
        )
        rows = cur.fetchall()
        type_map = {"goods": "مواد اولیه", "service": "خدمات فنی", "equipment": "تجهیزات و قطعات", "other": "سایر"}
        status_map = {
            "pending": "در جریان گردش",
            "submitted": "بدون مسیر تأیید",
            "approved": "تکمیل و سفارش‌گذاری شد",
            "rejected": "رد شده",
            "cancelled": "لغو شده توسط ثبت‌کننده",
        }

        buf = io.StringIO()
        buf.write("\ufeff")
        writer = csv.writer(buf)
        writer.writerow([
            "شماره درخواست",
            "تاریخ درخواست (شمسی)",
            "نوع اقلام",
            "واحد درخواست‌کننده (پادرا)",
            "ثبت‌کننده",
            "وضعیت کلی",
            "مرحله فعلی",
            "مسئول اقدام جاری",
            "تعداد اقلام",
            "آخرین بروزرسانی",
        ])
        for r in rows:
            req_u = user_display_info(r[7])["full_name"]
            cur_u = user_display_info(r[9])["full_name"] if r[9] else "—"
            step_str = f"مرحله {r[11]} از {r[12]} ({r[10]})" if r[5] == "pending" and r[11] else status_map.get(r[5], r[5])
            upd_str = jalali_datetime(r[8]) if r[8] else ""
            writer.writerow([
                r[1] or f"#{r[0]}",
                r[2],
                type_map.get(r[3], r[3]),
                r[4],
                req_u,
                status_map.get(r[5], r[5]),
                step_str,
                cur_u,
                r[13],
                upd_str,
            ])

        return Response(
            buf.getvalue(),
            mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=padra_purchase_requests_bi_report.csv"},
        )
    finally:
        conn.close()


@app.route("/export/deliveries.csv")
@login_required
def export_deliveries_csv():
    permissions = current_iam_permissions()
    if not any(key in permissions for key in ("requests.view_padra", "requests.view_team", "requests.view_all")):
        abort(403)
    visibility, vis_params = request_visibility_clause(permissions)
    conn = data_db()
    try:
        cur = conn.cursor()
        cur.execute(
            f"""SELECT r.request_no,r.unit_name,i.item_description,i.unit_name,o.supplier_name,
                       o.delivery_date_fa,o.quantity,COALESCE(t.delivered_quantity,0),
                       d.receipt_no,d.delivery_date_fa,d.quantity,d.recorded_by,d.recorded_at
                FROM Project.requests r
                INNER JOIN Project.request_supplier_offers o ON o.request_id=r.id AND o.is_selected=1
                INNER JOIN Project.request_items i ON i.id=o.request_item_id
                LEFT JOIN (
                    SELECT offer_id,SUM(quantity) AS delivered_quantity
                    FROM Project.request_deliveries GROUP BY offer_id
                ) t ON t.offer_id=o.id
                LEFT JOIN Project.request_deliveries d ON d.offer_id=o.id
                WHERE {visibility}
                ORDER BY r.id DESC,i.line_no,d.delivery_date_fa,d.id""",
            *vis_params,
        )
        buf = io.StringIO()
        buf.write("\ufeff")
        writer = csv.writer(buf)
        writer.writerow([
            "شماره درخواست", "واحد درخواست‌کننده", "شرح قلم", "تأمین‌کننده منتخب",
            "واحد سنجش", "موعد تحویل (شمسی)", "تعداد سفارش", "مجموع تحویل‌شده", "شماره رسید",
            "تاریخ رسید (شمسی)", "تعداد این رسید", "ثبت‌کننده رسید", "زمان ثبت رسید (شمسی)",
        ])
        for row in cur.fetchall():
            writer.writerow([
                row[0], row[1], row[2], row[4], row[3], row[5], row[6], row[7],
                row[8] or "", row[9] or "", row[10] or "",
                user_display_info(row[11])["full_name"] if row[11] else "",
                jalali_datetime(row[12]) if row[12] else "",
            ])
        return Response(
            buf.getvalue(),
            mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=padra_delivery_tracking_report.csv"},
        )
    finally:
        conn.close()


@app.route("/api/parts")
@login_required
def search_parts():
    if not has_iam_permission("requests.create"):
        abort(403)
    term = (request.args.get("q") or "").strip()
    if len(term) < 2:
        return jsonify([])
    term = term[:100]
    conn = cur = None
    try:
        conn = parts_db()
        cur = conn.cursor()
        pattern = "%" + term + "%"
        cur.execute(
            """SELECT TOP (25)
                      CONVERT(NVARCHAR(100), [Code]) AS [Code],
                      CONVERT(NVARCHAR(500), [Name]) AS [Name]
               FROM [LGS].[DimPart_Padra]
               WHERE CONVERT(NVARCHAR(100), [Code]) LIKE ?
                  OR [Name] LIKE ?
               ORDER BY CASE
                   WHEN CONVERT(NVARCHAR(100), [Code]) = ? THEN 0
                   WHEN CONVERT(NVARCHAR(100), [Code]) LIKE ? THEN 1
                   ELSE 2 END, [Name]""",
            (pattern, pattern, term, term + "%"),
        )
        return jsonify([
            {"code": str(row[0] or ""), "name": str(row[1] or "")}
            for row in cur.fetchall()
        ])
    except Exception:
        app.logger.exception("Live part search failed")
        return jsonify({"error": "جستجوی کالا در دیتابیس SIGD انجام نشد. تنظیمات اتصال را بررسی کنید."}), 503
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


@app.route("/request/new", methods=["GET", "POST"])
@login_required
def new_request():
    if not has_iam_permission("requests.create"):
        abort(403)
    workflow_type = "padra" if has_iam_permission("requests.workflow.padra") else "serkan"
    if request.method == "POST":
        request_type = request.form.get("request_type", "goods")
        if request_type not in {"goods", "service", "equipment", "other"}:
            flash("نوع درخواست نامعتبر است.", "error")
            return redirect(url_for("new_request"))
        priority = request.form.get("priority", "عادی").strip()
        if priority not in {"عادی", "فوری", "آنی / توقف خط"}:
            priority = "عادی"
        unit_name = request.form.get("unit_name", "").strip()
        supply_location = request.form.get("supply_location", "").strip()
        allowed_supply_locations = {
            "خرید داخلی گروه صنعتی سرکان",
            "خرید خارجی گروه صنعتی سرکان",
        }
        if supply_location not in allowed_supply_locations:
            flash("لطفاً یکی از دو روش تأمین داخلی یا خارجی گروه صنعتی سرکان را انتخاب نمایید.", "error")
            return redirect(url_for("new_request"))
        fixed_asset = request.form.get("fixed_asset") == "yes"
        raw_reason = request.form.get("request_reason", "").strip()
        reason = f"[{priority}] {raw_reason}".strip() if raw_reason else f"[{priority}]"
        fields = [
            request.form.getlist(k)
            for k in (
                "item_code[]",
                "item_description[]",
                "item_unit[]",
                "quantity[]",
                "needed_date[]",
                "usage_location[]",
            )
        ]
        codes, descriptions, units, quantities, needed_dates, locations = fields
        if not unit_name:
            flash("نام واحد درخواست‌کننده را تایپ نمایید.", "error")
            return redirect(url_for("new_request"))
        items = []
        try:
            for i, desc in enumerate(descriptions):
                desc = desc.strip()
                if not desc:
                    continue
                qty = Decimal(normalize_digits(quantities[i]).replace(",", ""))
                if qty <= 0:
                    raise ValueError(f"مقدار ردیف {i+1} باید بیشتر از صفر باشد.")
                needed = validate_jalali_date(needed_dates[i], f"تاریخ نیاز ردیف {i+1}")
                unit, destination = units[i].strip(), locations[i].strip()
                if not unit:
                    raise ValueError(f"واحد سنجش ردیف {i+1} الزامی است.")
                items.append((len(items) + 1, codes[i].strip() or None, desc, unit, qty, needed, destination))
        except (InvalidOperation, IndexError, AttributeError):
            flash("مقدار یا اطلاعات یکی از ردیف‌های کالا معتبر یا کامل نیست.", "error")
            return redirect(url_for("new_request"))
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("new_request"))
        if not items:
            flash("حداقل یک قلم کالا یا خدمت باید ثبت شود.", "error")
            return redirect(url_for("new_request"))

        conn = cur = None
        try:
            conn = data_db()
            conn.autocommit = False
            cur = conn.cursor()
            cur.execute("SELECT GETDATE()")
            now = cur.fetchone()[0]
            jalali = jdatetime.datetime.fromgregorian(datetime=now).strftime("%Y/%m/%d")
            cur.execute(
                """SET NOCOUNT ON;
                   INSERT INTO Project.requests
                   (request_date,request_date_fa,request_type,unit_name,supply_location,fixed_asset,
                    request_reason,requester_username,workflow_type,status)
                   VALUES (?,?,?,?,?,?,?,?,?,'pending');
                   SELECT CAST(SCOPE_IDENTITY() AS BIGINT) AS request_id;""",
                now.date(),
                jalali,
                request_type,
                unit_name,
                supply_location,
                fixed_asset,
                reason or None,
                current_user.username,
                workflow_type,
            )
            while cur.description is None:
                if not cur.nextset():
                    break
            inserted_request = cur.fetchone() if cur.description is not None else None
            if not inserted_request or inserted_request[0] is None:
                raise RuntimeError("درج درخواست انجام شد اما شناسه آن از همان تراکنش دریافت نشد.")
            request_id = int(inserted_request[0])
            request_no = f"PR-{jalali.replace('/', '')}-{request_id:06d}"
            cur.execute("UPDATE Project.requests SET request_no=? WHERE id=?", request_no, request_id)
            for item in items:
                cur.execute(
                    """INSERT INTO Project.request_items
                       (request_id,line_no,item_code,item_description,unit_name,quantity,needed_date_fa,usage_location)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    request_id,
                    *item,
                )
            if workflow_type == "padra":
                cur.execute(
                    """INSERT INTO Project.approvals
                       (request_id,step_no,approver_username,approver_title,decision)
                       VALUES (?,1,N'i.heydari',N'مدیرعامل پادرا - ایمان حیدری','waiting')""",
                    request_id,
                )
            else:
                cur.execute(
                    """INSERT INTO Project.approvals(request_id,step_no,approver_username,approver_title,decision)
                       SELECT ?,step_no,approver_username,approver_title,'waiting'
                       FROM Project.approval_routes WHERE request_type=? AND is_active=1""",
                    request_id,
                    request_type,
                )
            cur.execute(
                """INSERT INTO Project.request_history(request_id,action_name,actor_username,details)
                   VALUES (?,N'ثبت درخواست در شرکت پادرا',?,?)""",
                request_id,
                current_user.username,
                f"درخواست خرید با اولویت «{priority}» در شرکت پادرا ثبت شد.",
            )
            cur.execute(
                """SELECT TOP (1) id,step_no,approver_username,approver_title
                   FROM Project.approvals WHERE request_id=? ORDER BY step_no""",
                request_id,
            )
            first_approval = cur.fetchone()
            if first_approval:
                cur.execute("UPDATE Project.approvals SET decision='pending' WHERE id=?", first_approval[0])
                cur.execute(
                    """INSERT INTO Project.request_history(request_id,action_name,actor_username,details)
                       VALUES (?,CASE WHEN ?='padra' THEN N'ارجاع به مدیرعامل شرکت پادرا' ELSE N'ارجاع به مسئول گردش اولیه سرکان' END,?,?)""",
                    request_id,
                    workflow_type,
                    current_user.username,
                    f"درخواست برای مرحله {first_approval[1]} جهت بررسی و تأیید به {first_approval[3]} ({first_approval[2]}) ارسال شد.",
                )
            else:
                cur.execute("UPDATE Project.requests SET status='submitted' WHERE id=?", request_id)
            conn.commit()
            if workflow_type == "padra":
                flash(f"درخواست خرید {request_no} با موفقیت ثبت و فقط برای تأیید نهایی به کارتابل مدیرعامل پادرا ارسال شد.", "success")
            elif first_approval:
                flash(f"درخواست خرید {request_no} با موفقیت ثبت و به کارتابل مسئول مرحله اول ({first_approval[3]} - {first_approval[2]}) ارسال شد.", "success")
            else:
                flash(f"درخواست خرید {request_no} ثبت شد، اما برای این نوع درخواست مسیر تأیید فعالی تعریف نشده است.", "warning")
            return redirect(url_for("view_request", request_id=request_id))
        except Exception:
            if conn:
                conn.rollback()
            app.logger.exception("Purchase request save failed")
            flash("ثبت درخواست انجام نشد؛ تراکنش برگشت داده شد. اتصال دیتابیس و ساختار جداول را بررسی کنید.", "error")
            return redirect(url_for("new_request"))
        finally:
            if cur:
                cur.close()
            if conn:
                conn.close()
    return render_template("request_form.html", username=current_user.username, today=jalali_today())


@app.route("/request/<int:request_id>")
@login_required
def view_request(request_id):
    data = get_request(request_id)
    if not data:
        abort(404)
    h = data["header"]
    permissions = current_iam_permissions()
    can_view = (
        "requests.view_all" in permissions
        or ("requests.view_padra" in permissions and h[12] == "padra")
        or (
            "requests.view_own" in permissions
            and h[12] == "padra"
            and h[8] == current_user.username
        )
        or (
            "requests.view_team" in permissions
            and h[12] in {"serkan", "padra"}
            and any(
                (a[0] == 2 and a[1] == current_user.username)
                or (a[0] == 3 and a[1] != "UNASSIGNED_SPECIALIST")
                for a in data["approvals"]
            )
        )
        or (
            "requests.view_assigned" in permissions
            and h[12] in {"serkan", "padra"}
            and any(a[0] == 3 and a[1] == current_user.username for a in data["approvals"])
        )
        or (h[12] == "padra" and current_user.username == "kharid_padra")
    )
    can_view_item_assignment = any(
        a[2] == current_user.username for a in data.get("item_assignments", [])
    )
    if not can_view and has_iam_permission("requests.view_assigned") and can_view_item_assignment:
        can_view = True
    if not can_view:
        abort(403)

    current_approval = next((a for a in data["approvals"] if a[3] == "pending"), None)
    previous_approval = None
    can_withdraw_referral = False
    withdraw_target_title = ""
    can_return_to_previous = False
    return_target_title = ""
    can_resubmit_request = False

    if h[12] == "padra" and h[9] == "pending" and current_approval:
        if (
            current_approval[0] == 1
            and h[8] == current_user.username
            and current_approval[1] == "i.heydari"
            and has_iam_permission("requests.create")
        ):
            can_withdraw_referral = True
            withdraw_target_title = "کارتابل ثبت‌کننده پادرا"
        elif current_approval[0] in (2, 3):
            previous_approval = next(
                (a for a in data["approvals"] if a[0] == current_approval[0] - 1),
                None,
            )
            if previous_approval and previous_approval[3] == "approved":
                if previous_approval[0] == 1:
                    actor_allowed = (
                        previous_approval[1] == current_user.username
                        and has_iam_permission("requests.approve.padra")
                    )
                else:
                    actor_allowed = (
                        previous_approval[1] == current_user.username
                        and has_iam_permission("requests.approve.step2")
                        and has_iam_permission("requests.assign_specialist")
                    )
                can_withdraw_referral = bool(actor_allowed)
                withdraw_target_title = previous_approval[2]

        if current_approval[0] == 1:
            can_return_to_previous = (
                current_approval[1] == current_user.username
                and has_iam_permission("requests.approve.padra")
            )
            return_target_title = "ثبت‌کننده درخواست پادرا"
        elif current_approval[0] == 2:
            previous_approval = next((a for a in data["approvals"] if a[0] == 1), None)
            can_return_to_previous = bool(
                current_approval[1] == current_user.username
                and has_iam_permission("requests.approve.step2")
                and has_iam_permission("requests.assign_specialist")
                and previous_approval
                and previous_approval[3] == "approved"
            )
            return_target_title = previous_approval[2] if previous_approval else "مرحله قبل"
        elif current_approval[0] == 3:
            previous_approval = next((a for a in data["approvals"] if a[0] == 2), None)
            can_return_to_previous = bool(
                current_approval[1] == current_user.username
                and has_iam_permission("requests.approve.step3")
                and previous_approval
                and previous_approval[3] == "approved"
            )
            return_target_title = previous_approval[2] if previous_approval else "مرحله قبل"

    if (
        h[12] == "padra"
        and h[9] == "submitted"
        and h[8] == current_user.username
        and has_iam_permission("requests.create")
        and data["approvals"]
        and all(a[3] == "waiting" for a in data["approvals"])
    ):
        can_resubmit_request = True

    return render_template(
        "request_detail.html",
        data=data,
        username=current_user.username,
        can_withdraw_referral=can_withdraw_referral,
        withdraw_target_title=withdraw_target_title,
        can_return_to_previous=can_return_to_previous,
        return_target_title=return_target_title,
        can_resubmit_request=can_resubmit_request,
        can_manage_item_assignments=has_iam_permission("requests.assign_specialist"),
        can_record_delivery=(h[12] == "padra" and current_user.username == "kharid_padra") or can_view_item_assignment,
    )


@app.route("/request/<int:request_id>/decision", methods=["POST"])
@login_required
def decide_request(request_id):
    decision = request.form.get("decision", "").strip()
    comment = request.form.get("comment", "").strip()
    selected_specialist = request.form.get("assigned_specialist", "").strip()
    supplier_names = request.form.getlist("supplier_name[]")
    supplier_selected = request.form.getlist("supplier_selected[]")
    proforma_numbers = request.form.getlist("proforma_number[]")
    delivery_dates = request.form.getlist("delivery_date[]")
    supplier_item_ids = request.form.getlist("supplier_item_id[]")
    supplier_quantities = request.form.getlist("supplier_quantity[]")
    supplier_unit_prices = request.form.getlist("supplier_unit_price[]")
    payment_methods = request.form.getlist("payment_method[]")

    if decision not in {"approved", "rejected"}:
        abort(400)

    conn = cur = None
    try:
        conn = data_db()
        conn.autocommit = False
        cur = conn.cursor()

        cur.execute(
            """SELECT TOP (1) a.id, a.step_no, a.approver_username, a.approver_title, r.workflow_type
               FROM Project.approvals a
               INNER JOIN Project.requests r ON r.id = a.request_id
               WHERE a.request_id=? AND a.approver_username=? AND a.decision='pending'
               ORDER BY a.step_no""",
            request_id,
            current_user.username,
        )
        approval = cur.fetchone()
        if not approval:
            conn.rollback()
            abort(403)

        approval_id, current_step_no, step_approver_user, step_approver_title, workflow_type = (
            approval[0],
            approval[1],
            approval[2],
            approval[3],
            approval[4],
        )
        if workflow_type == "padra" and current_step_no == 1:
            if not has_iam_permission("requests.approve.padra"):
                conn.rollback()
                abort(403)
        elif not has_iam_permission(f"requests.approve.step{current_step_no}"): 
            conn.rollback()
            abort(403)
        if current_step_no == 2 and not has_iam_permission("requests.assign_specialist"):
            conn.rollback()
            abort(403)
        if current_step_no == 3:
            conn.rollback()
            abort(403)

        specialist_info = None
        specialist_assignments = []
        if decision == "approved" and current_step_no == 2:
            submitted_item_ids = request.form.getlist("item_id[]")
            submitted_specialists = request.form.getlist("item_specialist[]")
            valid_items = {}
            cur.execute("SELECT id,line_no,item_description FROM Project.request_items WHERE request_id=?", request_id)
            for item_row in cur.fetchall():
                valid_items[str(item_row[0])] = item_row
            allowed_specialists = {sp["username"]: sp for sp in get_purchase_specialists()}
            if not valid_items or len(submitted_item_ids) != len(valid_items) or len(submitted_specialists) != len(valid_items):
                conn.rollback()
                flash("برای تمام اقلام درخواست باید کارشناس خرید تعیین شود.", "error")
                return redirect(url_for("view_request", request_id=request_id))
            seen_items = set()
            for item_id, specialist_username in zip(submitted_item_ids, submitted_specialists):
                if item_id not in valid_items or item_id in seen_items or specialist_username not in allowed_specialists:
                    conn.rollback()
                    flash("تخصیص یکی از اقلام معتبر نیست. صفحه را تازه‌سازی و دوباره تلاش کنید.", "error")
                    return redirect(url_for("view_request", request_id=request_id))
                seen_items.add(item_id)
                specialist_assignments.append((int(item_id), specialist_username, allowed_specialists[specialist_username]))
            if seen_items != set(valid_items):
                conn.rollback()
                flash("تخصیص همه اقلام الزامی است.", "error")
                return redirect(url_for("view_request", request_id=request_id))

        full_comment = comment
        supplier_rows = []
        if decision == "approved" and current_step_no == 3:
            cur.execute("SELECT id FROM Project.request_items WHERE request_id=?", request_id)
            valid_item_ids = {str(row[0]) for row in cur.fetchall()}
            try:
                row_count = max(
                    len(supplier_names), len(supplier_selected), len(proforma_numbers), len(delivery_dates),
                    len(supplier_item_ids), len(supplier_quantities),
                    len(supplier_unit_prices), len(payment_methods)
                )
                for idx in range(row_count):
                    values = [
                        supplier_names[idx] if idx < len(supplier_names) else "",
                        supplier_selected[idx] if idx < len(supplier_selected) else "0",
                        proforma_numbers[idx] if idx < len(proforma_numbers) else "",
                        delivery_dates[idx] if idx < len(delivery_dates) else "",
                        supplier_item_ids[idx] if idx < len(supplier_item_ids) else "",
                        supplier_quantities[idx] if idx < len(supplier_quantities) else "",
                        supplier_unit_prices[idx] if idx < len(supplier_unit_prices) else "",
                        payment_methods[idx] if idx < len(payment_methods) else "",
                    ]
                    if not any(str(v).strip() for pos, v in enumerate(values) if pos != 1):
                        continue
                    supplier, selected_raw, proforma, delivery, item_id, qty_raw, price_raw, payment = [str(v).strip() for v in values]
                    if not all([supplier, proforma, delivery, item_id, qty_raw, price_raw, payment]):
                        raise ValueError(f"اطلاعات تأمین‌کننده در ردیف {idx + 1} کامل نیست.")
                    if item_id not in valid_item_ids:
                        raise ValueError(f"قلم کالای انتخاب‌شده در ردیف {idx + 1} معتبر نیست.")
                    delivery = validate_jalali_date(delivery, f"تاریخ تحویل ردیف {idx + 1}")
                    qty = Decimal(normalize_digits(qty_raw).replace(",", ""))
                    price = Decimal(normalize_digits(price_raw).replace(",", ""))
                    if qty <= 0 or price <= 0:
                        raise ValueError(f"تعداد و فی واحد در ردیف {idx + 1} باید بیشتر از صفر باشد.")
                    if selected_raw not in {"0", "1"}:
                        raise ValueError(f"وضعیت انتخاب تأمین‌کننده در ردیف {idx + 1} معتبر نیست.")
                    supplier_rows.append((int(item_id), supplier, proforma, delivery, qty, price, payment, selected_raw == "1"))
            except (InvalidOperation, IndexError, ValueError) as exc:
                conn.rollback()
                flash(str(exc) if isinstance(exc, ValueError) else "تعداد یا فی واحد یکی از تأمین‌کنندگان معتبر نیست.", "error")
                return redirect(url_for("view_request", request_id=request_id))
            if not supplier_rows:
                conn.rollback()
                flash("برای تکمیل خرید، حداقل یک ردیف اطلاعات تأمین‌کننده ثبت نمایید.", "error")
                return redirect(url_for("view_request", request_id=request_id))
            cur.execute("DELETE FROM Project.request_supplier_offers WHERE request_id=?", request_id)
            for offer in supplier_rows:
                cur.execute(
                    """INSERT INTO Project.request_supplier_offers
                       (request_id,request_item_id,supplier_name,proforma_number,delivery_date_fa,
                        quantity,unit_price,payment_method,is_selected,created_by)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    request_id, *offer, current_user.username
                )
            supplier_summary = "؛ ".join(
                f"{row[1]} | پیش‌فاکتور {row[2]} | تعداد {row[4]} | فی {row[5]}"
                for row in supplier_rows
            )
            full_comment = (comment + " — " if comment else "") + "اطلاعات تأمین‌کنندگان: " + supplier_summary

        cur.execute(
            """UPDATE Project.approvals SET decision=?,comment=?,decided_at=GETDATE()
               WHERE id=? AND decision='pending'""",
            decision,
            full_comment or None,
            approval_id,
        )

        next_approval = None
        if decision == "rejected":
            status = "rejected"
        else:
            if workflow_type == "padra" and current_step_no == 1:
                cur.execute(
                    """IF EXISTS (SELECT 1 FROM Project.approvals WHERE request_id=? AND step_no=2)
                       UPDATE Project.approvals
                       SET approver_username=N'm.mahdiyon',
                           approver_title=N'مدیر خرید داخلی گروه صنعتی سرکان - محمد مهدیون',
                           decision='waiting',comment=NULL,decided_at=NULL
                       WHERE request_id=? AND step_no=2
                       ELSE
                       INSERT INTO Project.approvals
                       (request_id,step_no,approver_username,approver_title,decision)
                       VALUES (?,2,N'm.mahdiyon',N'مدیر خرید داخلی گروه صنعتی سرکان - محمد مهدیون','waiting')""",
                    request_id, request_id, request_id,
                )
                cur.execute(
                    """IF EXISTS (SELECT 1 FROM Project.approvals WHERE request_id=? AND step_no=3)
                       UPDATE Project.approvals
                       SET approver_username=N'UNASSIGNED_SPECIALIST',
                           approver_title=N'کارشناس خرید داخلی گروه صنعتی سرکان',
                           decision='waiting',comment=NULL,decided_at=NULL
                       WHERE request_id=? AND step_no=3
                       ELSE
                       INSERT INTO Project.approvals
                       (request_id,step_no,approver_username,approver_title,decision)
                       VALUES (?,3,N'UNASSIGNED_SPECIALIST',N'کارشناس خرید داخلی گروه صنعتی سرکان','waiting')""",
                    request_id, request_id, request_id,
                )
            if current_step_no == 2 and specialist_assignments:
                for item_id, specialist_username, specialist in specialist_assignments:
                    cur.execute(
                        """IF EXISTS (SELECT 1 FROM Project.request_item_assignments WHERE request_item_id=?)
                           UPDATE Project.request_item_assignments
                           SET specialist_username=?,assignment_status='assigned',
                               assigned_by=?,assigned_at=SYSUTCDATETIME(),
                               completed_by=NULL,completed_at=NULL,completion_comment=NULL
                           WHERE request_item_id=?
                           ELSE
                           INSERT Project.request_item_assignments
                               (request_id,request_item_id,specialist_username,assignment_status,assigned_by)
                           VALUES (?,?,?,'assigned',?)""",
                        item_id, specialist_username, current_user.username, item_id,
                        request_id, item_id, specialist_username, current_user.username,
                    )
                cur.execute(
                    """UPDATE Project.approvals
                       SET approver_username=N'UNASSIGNED_SPECIALIST',
                           approver_title=N'کارشناسان خرید داخلی گروه صنعتی سرکان',
                           decision='waiting',comment=NULL,decided_at=NULL
                       WHERE request_id=? AND step_no=3""",
                    request_id,
                )
                cur.execute(
                    """UPDATE Project.requests
                       SET assigned_specialist=NULL, updated_at=GETDATE()
                       WHERE id=? AND workflow_type IN ('serkan','padra')""",
                    request_id,
                )

            cur.execute(
                """SELECT TOP (1) id FROM Project.approvals
                   WHERE request_id=? AND decision='waiting' ORDER BY step_no""",
                request_id,
            )
            next_approval = cur.fetchone()
            if next_approval:
                cur.execute("UPDATE Project.approvals SET decision='pending' WHERE id=?", next_approval[0])
                status = "pending"
            else:
                status = "approved"

        cur.execute("UPDATE Project.requests SET status=?,updated_at=GETDATE() WHERE id=?", status, request_id)

        if decision == "rejected":
            action_label = f"رد درخواست در مرحله {current_step_no}"
            details = f"رد درخواست در مرحله {current_step_no} ({step_approver_title})"
            if full_comment:
                details += f" — علت: {full_comment}"
            cur.execute(
                """INSERT INTO Project.request_history(request_id,action_name,actor_username,details)
                   VALUES (?,?,?,?)""",
                request_id,
                action_label,
                current_user.username,
                details,
            )
        elif current_step_no == 1:
            details = f"تأیید مرحله ۱ توسط {step_approver_title}"
            if full_comment:
                details += f" — توضیح: {full_comment}"
            if workflow_type == "padra":
                cur.execute(
                    """INSERT INTO Project.request_history(request_id,action_name,actor_username,details)
                       VALUES (?,N'تأیید مدیرعامل پادرا',?,?)""",
                    request_id,
                    current_user.username,
                    details,
                )
                if next_approval:
                    cur.execute(
                        """INSERT INTO Project.request_history(request_id,action_name,actor_username,details)
                           VALUES (?,N'ارجاع به مدیر خرید داخلی سرکان',N'SYSTEM',?)""",
                        request_id,
                        "درخواست پس از تأیید مدیرعامل پادرا به کارتابل مدیر خرید داخلی سرکان ارجاع شد تا کارشناس خرید تخصیص یابد.",
                    )
            else:
                cur.execute(
                    """INSERT INTO Project.request_history(request_id,action_name,actor_username,details)
                       VALUES (?,N'تأیید مسئول گردش اولیه سرکان',?,?)""",
                    request_id,
                    current_user.username,
                    details,
                )
                if next_approval:
                    cur.execute(
                        """SELECT step_no,approver_username,approver_title
                           FROM Project.approvals WHERE id=?""",
                        next_approval[0],
                    )
                    next_step = cur.fetchone()
                    cur.execute(
                        """INSERT INTO Project.request_history(request_id,action_name,actor_username,details)
                           VALUES (?,N'محول‌سازی به مدیر خرید داخلی سرکان',N'SYSTEM',?)""",
                        request_id,
                        f"درخواست پس از تأیید مرحله اول گردش کار سرکان به مرحله {next_step[0]} ({next_step[2]} - {next_step[1]}) ارجاع شد تا کارشناس خرید تعیین گردد.",
                    )
        elif current_step_no == 2 and specialist_assignments:
            assigned_summary = "؛ ".join(
                f"قلم {valid_items[str(item_id)][1]}: {specialist['full_name']}"
                for item_id, _username, specialist in specialist_assignments
            )
            details = "تخصیص اقلام درخواست به کارشناسان خرید سرکان: " + assigned_summary
            if full_comment:
                details += f" — دستور خرید: {full_comment}"
            cur.execute(
                """INSERT INTO Project.request_history(request_id,action_name,actor_username,details)
                   VALUES (?,N'تخصیص اقلام به کارشناسان خرید',?,?)""",
                request_id, current_user.username, details,
            )
        else:
            details = f"انجام سفارش‌گذاری و تکمیل مرحله {current_step_no} توسط {step_approver_title}"
            if full_comment:
                details += f" — جزئیات تأمین: {full_comment}"
            cur.execute(
                """INSERT INTO Project.request_history(request_id,action_name,actor_username,details)
                   VALUES (?,N'انجام سفارش‌گذاری و تکمیل خرید',?,?)""",
                request_id,
                current_user.username,
                details,
            )
            cur.execute(
                """INSERT INTO Project.request_history(request_id,action_name,actor_username,details)
                   VALUES (?,N'تکمیل گردش تأیید و خرید',N'SYSTEM',N'فرآیند تأیید مدیرعامل پادرا، ارجاع مدیر خرید داخلی سرکان و سفارش‌گذاری کارشناس خرید با موفقیت تکمیل شد.')""",
                request_id,
            )

        conn.commit()
        if decision == "approved" and workflow_type == "padra" and current_step_no == 1:
            flash("تأیید مدیرعامل پادرا ثبت شد و درخواست به کارتابل مدیر خرید داخلی سرکان ارجاع گردید.", "success")
        elif decision == "approved" and current_step_no == 1:
            flash("تأیید مدیرعامل شرکت پادرا ثبت شد و درخواست به کارتابل مدیر خرید داخلی گروه صنعتی سرکان محول گردید.", "success")
        elif decision == "approved" and current_step_no == 2 and specialist_assignments:
            flash(f"درخواست تأیید شد و {len(specialist_assignments)} قلم کالا بین کارشناسان خرید تخصیص یافت.", "success")
        elif decision == "approved":
            flash("انجام سفارش‌گذاری و تکمیل نهایی خرید با موفقیت ثبت شد.", "success")
        else:
            flash("رد درخواست در سامانه ثبت شد.", "warning")
    except HTTPException:
        raise
    except Exception:
        if conn:
            conn.rollback()
        app.logger.exception("Approval action failed")
        flash("ثبت تصمیم انجام نشد.", "error")
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()
    return redirect(url_for("view_request", request_id=request_id))



@app.route("/request/<int:request_id>/items/<int:item_id>/complete", methods=["POST"])
@login_required
def complete_assigned_item(request_id, item_id):
    if not has_iam_permission("requests.view_assigned"):
        abort(403)
    decision = request.form.get("decision", "approved").strip()
    comment = request.form.get("comment", "").strip()
    if decision not in {"approved", "rejected"}:
        abort(400)
    if decision == "rejected" and not comment:
        flash("برای اعلام عدم امکان تأمین این قلم، درج علت الزامی است.", "error")
        return redirect(url_for("view_request", request_id=request_id))

    supplier_names = request.form.getlist("supplier_name[]")
    supplier_selected = request.form.getlist("supplier_selected[]")
    proforma_numbers = request.form.getlist("proforma_number[]")
    delivery_dates = request.form.getlist("delivery_date[]")
    supplier_quantities = request.form.getlist("supplier_quantity[]")
    supplier_unit_prices = request.form.getlist("supplier_unit_price[]")
    payment_methods = request.form.getlist("payment_method[]")

    conn = cur = None
    try:
        conn = data_db()
        conn.autocommit = False
        cur = conn.cursor()
        cur.execute(
            """SELECT ia.id,ia.assignment_status,i.item_description
               FROM Project.request_item_assignments ia
               INNER JOIN Project.request_items i ON i.id=ia.request_item_id
               INNER JOIN Project.requests r ON r.id=ia.request_id
               WHERE ia.request_id=? AND ia.request_item_id=?
                 AND ia.specialist_username=? AND r.status='pending'""",
            request_id, item_id, current_user.username,
        )
        assignment = cur.fetchone()
        if not assignment or assignment[1] != "assigned":
            conn.rollback()
            abort(403)

        if decision == "approved":
            offers = []
            try:
                row_count = max(
                    len(supplier_names), len(supplier_selected), len(proforma_numbers),
                    len(delivery_dates), len(supplier_quantities), len(supplier_unit_prices),
                    len(payment_methods),
                )
                for idx in range(row_count):
                    values = [
                        supplier_names[idx] if idx < len(supplier_names) else "",
                        supplier_selected[idx] if idx < len(supplier_selected) else "0",
                        proforma_numbers[idx] if idx < len(proforma_numbers) else "",
                        delivery_dates[idx] if idx < len(delivery_dates) else "",
                        supplier_quantities[idx] if idx < len(supplier_quantities) else "",
                        supplier_unit_prices[idx] if idx < len(supplier_unit_prices) else "",
                        payment_methods[idx] if idx < len(payment_methods) else "",
                    ]
                    if not any(str(v).strip() for pos, v in enumerate(values) if pos != 1):
                        continue
                    supplier, selected_raw, proforma, delivery, qty_raw, price_raw, payment = [str(v).strip() for v in values]
                    if not all([supplier, proforma, delivery, qty_raw, price_raw, payment]):
                        raise ValueError(f"اطلاعات تأمین‌کننده در ردیف {idx + 1} کامل نیست.")
                    delivery = validate_jalali_date(delivery, f"تاریخ تحویل ردیف {idx + 1}")
                    qty = Decimal(normalize_digits(qty_raw).replace(",", ""))
                    price = Decimal(normalize_digits(price_raw).replace(",", ""))
                    if qty <= 0 or price <= 0:
                        raise ValueError(f"تعداد و فی واحد ردیف {idx + 1} باید بیشتر از صفر باشد.")
                    if selected_raw not in {"0", "1"}:
                        raise ValueError(f"وضعیت انتخاب تأمین‌کننده در ردیف {idx + 1} معتبر نیست.")
                    offers.append((supplier, selected_raw == "1", proforma, delivery, qty, price, payment))
            except (InvalidOperation, ValueError) as exc:
                conn.rollback()
                flash(str(exc) if isinstance(exc, ValueError) else "تعداد یا فی واحد معتبر نیست.", "error")
                return redirect(url_for("view_request", request_id=request_id))

            if not offers:
                conn.rollback()
                flash("برای تکمیل این قلم، حداقل یک استعلام بها ثبت کنید.", "error")
                return redirect(url_for("view_request", request_id=request_id))
            if sum(1 for offer in offers if offer[1]) != 1:
                conn.rollback()
                flash("برای هر قلم دقیقاً یک تأمین‌کننده باید به‌عنوان گزینه منتخب مشخص شود.", "error")
                return redirect(url_for("view_request", request_id=request_id))

            for offer in offers:
                cur.execute(
                    """INSERT INTO Project.request_supplier_offers
                       (request_id,request_item_id,supplier_name,proforma_number,delivery_date_fa,
                        quantity,unit_price,payment_method,is_selected,created_by)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    request_id, item_id, offer[0], offer[2], offer[3],
                    offer[4], offer[5], offer[6], offer[1], current_user.username,
                )
            summary = "؛ ".join(
                f"{offer[0]} / پیش‌فاکتور {offer[2]} / تعداد {offer[4]} / فی {offer[5]}"
                for offer in offers
            )
            completion_note = (comment + " — " if comment else "") + summary
            assignment_status = "completed"
            history_action = "تکمیل خرید قلم کالا"
            history_details = f"قلم «{assignment[2]}» توسط کارشناس خرید تکمیل شد. {completion_note}"
        else:
            completion_note = comment
            assignment_status = "rejected"
            history_action = "عدم امکان تأمین قلم کالا"
            history_details = f"عدم امکان تأمین قلم «{assignment[2]}» اعلام شد. علت: {comment}"

        cur.execute(
            """UPDATE Project.request_item_assignments
               SET assignment_status=?,completed_by=?,completed_at=SYSUTCDATETIME(),
                   completion_comment=?
               WHERE id=? AND assignment_status='assigned'""",
            assignment_status, current_user.username, completion_note, assignment[0],
        )
        cur.execute(
            """INSERT INTO Project.request_history(request_id,action_name,actor_username,details)
               VALUES (?,?,?,?)""",
            request_id, history_action, current_user.username, history_details,
        )
        cur.execute(
            """SELECT COUNT(*),
                      SUM(CASE WHEN assignment_status='completed' THEN 1 ELSE 0 END),
                      SUM(CASE WHEN assignment_status='rejected' THEN 1 ELSE 0 END),
                      SUM(CASE WHEN assignment_status='assigned' THEN 1 ELSE 0 END)
               FROM Project.request_item_assignments WHERE request_id=?""",
            request_id,
        )
        totals = cur.fetchone()
        all_items_determined = bool(
            totals and totals[0]
            and int(totals[0]) == int(totals[1] or 0) + int(totals[2] or 0)
        )
        has_rejected_item = bool(totals and int(totals[2] or 0) > 0)
        if all_items_determined:
            final_status = "rejected" if has_rejected_item else "approved"
            final_decision = "rejected" if has_rejected_item else "approved"
            cur.execute(
                """UPDATE Project.approvals
                   SET decision=?,approver_username=N'ITEM_ASSIGNMENTS_COMPLETED',
                       approver_title=N'تعیین‌تکلیف همه اقلام توسط کارشناسان خرید',
                       comment=?,decided_at=GETDATE()
                   WHERE request_id=? AND step_no=3 AND decision IN ('pending','waiting')""",
                final_decision,
                "تمام اقلام تعیین‌تکلیف شدند؛ حداقل یک قلم عدم امکان تأمین دارد." if has_rejected_item else "تمام اقلام درخواست تکمیل شدند.",
                request_id,
            )
            cur.execute(
                "UPDATE Project.requests SET status=?,updated_at=GETDATE() WHERE id=?",
                final_status, request_id,
            )
            cur.execute(
                """INSERT INTO Project.request_history(request_id,action_name,actor_username,details)
                   VALUES (?,N'تعیین‌تکلیف نهایی درخواست خرید',N'SYSTEM',?)""",
                request_id,
                "تمام اقلام تعیین‌تکلیف شدند؛ وضعیت نهایی درخواست: " + ("رد شده به‌دلیل عدم امکان تأمین حداقل یک قلم." if has_rejected_item else "خرید تکمیل شد."),
            )
        conn.commit()
        if all_items_determined:
            flash("همه اقلام تعیین‌تکلیف شدند و وضعیت نهایی درخواست به‌روزرسانی شد.", "success")
        elif decision == "rejected":
            flash("عدم امکان تأمین این قلم ثبت شد؛ درخواست تا تعیین‌تکلیف سایر اقلام باز می‌ماند.", "warning")
        else:
            flash("خرید این قلم ثبت شد. سایر کارشناسان می‌توانند اقلام ارجاع‌شده به خود را تکمیل کنند.", "success")
    except HTTPException:
        if conn:
            conn.rollback()
        raise
    except Exception:
        if conn:
            conn.rollback()
        app.logger.exception("Assigned item completion failed")
        flash("ثبت وضعیت خرید این قلم انجام نشد.", "error")
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()
    return redirect(url_for("view_request", request_id=request_id))


@app.route("/request/<int:request_id>/delivery", methods=["POST"])
@login_required
def record_delivery(request_id):
    receipt_no = request.form.get("receipt_no", "").strip()
    delivery_date = request.form.get("delivery_date_fa", "").strip()
    offer_id_raw = request.form.get("offer_id", "").strip()
    quantity_raw = request.form.get("quantity", "").strip()
    if not receipt_no or not offer_id_raw or not delivery_date or not quantity_raw:
        flash("شماره رسید، تاریخ تحویل، تأمین‌کننده و تعداد الزامی است.", "error")
        return redirect(url_for("view_request", request_id=request_id))
    try:
        offer_id = int(offer_id_raw)
        delivery_date = validate_jalali_date(delivery_date, "تاریخ تحویل")
        quantity = Decimal(normalize_digits(quantity_raw).replace(",", ""))
        if quantity <= 0:
            raise ValueError("تعداد تحویلی باید بیشتر از صفر باشد.")
    except (ValueError, InvalidOperation):
        flash("اطلاعات تحویل معتبر نیست؛ تاریخ را شمسی و تعداد را بزرگ‌تر از صفر وارد کنید.", "error")
        return redirect(url_for("view_request", request_id=request_id))

    conn = cur = None
    try:
        conn = data_db()
        conn.autocommit = False
        cur = conn.cursor()
        cur.execute(
            """SELECT r.workflow_type,ia.specialist_username,r.status,o.supplier_name,
                      o.quantity,COALESCE(SUM(d.quantity),0)
               FROM Project.requests r
               INNER JOIN Project.request_supplier_offers o ON o.request_id=r.id AND o.id=?
               INNER JOIN Project.request_item_assignments ia ON ia.request_item_id=o.request_item_id
               LEFT JOIN Project.request_deliveries d ON d.offer_id=o.id
               WHERE r.id=? AND o.is_selected=1
               GROUP BY r.workflow_type,ia.specialist_username,r.status,o.supplier_name,o.quantity""",
            offer_id, request_id,
        )
        offer = cur.fetchone()
        if not offer:
            conn.rollback()
            abort(404)
        is_padra_expert = offer[0] == "padra" and current_user.username == "kharid_padra"
        is_serkan_expert = bool(offer[1]) and current_user.username == offer[1]
        if not (is_padra_expert or is_serkan_expert):
            conn.rollback()
            abort(403)
        cur.execute(
            """INSERT INTO Project.request_deliveries
               (request_id,offer_id,receipt_no,delivery_date_fa,quantity,recorded_by)
               VALUES (?,?,?,?,?,?)""",
            request_id, offer_id, receipt_no, delivery_date, quantity, current_user.username,
        )
        details = (
            f"ثبت رسید تحویل شماره {receipt_no} از تأمین‌کننده «{offer[3]}»؛ "
            f"تاریخ تحویل {delivery_date}؛ تعداد {quantity}"
        )
        cur.execute(
            """INSERT INTO Project.request_history(request_id,action_name,actor_username,details)
               VALUES (?,N'ثبت رسید تحویل کالا',?,?)""",
            request_id, current_user.username, details,
        )
        conn.commit()
        flash("رسید تحویل با موفقیت ثبت شد و در گزارش مدیریتی قابل مشاهده است.", "success")
    except HTTPException:
        if conn:
            conn.rollback()
        raise
    except Exception:
        if conn:
            conn.rollback()
        app.logger.exception("Delivery receipt registration failed")
        flash("ثبت رسید تحویل انجام نشد؛ ساختار جدول و اتصال دیتابیس را بررسی کنید.", "error")
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()
    return redirect(url_for("view_request", request_id=request_id))


@app.route("/request/<int:request_id>/return", methods=["POST"])
@login_required
def return_request(request_id):
    """پس گرفتن ارجاع توسط ثبت‌کننده یا مسئول مرحله قبلی، پیش از اقدام نفر بعد."""
    reason = request.form.get("return_reason", "").strip()
    if not reason:
        flash("ثبت علت پس گرفتن ارجاع الزامی است.", "error")
        return redirect(url_for("view_request", request_id=request_id))

    conn = cur = None
    try:
        conn = data_db()
        conn.autocommit = False
        cur = conn.cursor()
        cur.execute(
            """SELECT workflow_type,status,requester_username
               FROM Project.requests WITH (UPDLOCK, ROWLOCK)
               WHERE id=?""",
            request_id,
        )
        request_row = cur.fetchone()
        if not request_row or request_row[0] != "padra" or request_row[1] != "pending":
            conn.rollback()
            abort(403)

        cur.execute(
            """SELECT step_no,approver_username,approver_title,decision
               FROM Project.approvals WITH (UPDLOCK, ROWLOCK)
               WHERE request_id=? ORDER BY step_no""",
            request_id,
        )
        approvals = cur.fetchall()
        active = next((a for a in approvals if a[3] == "pending"), None)
        if not active:
            conn.rollback()
            abort(403)

        # ثبت‌کننده پادرا می‌تواند ارجاع اولیه به مدیرعامل را پس بگیرد.
        if active[0] == 1:
            permitted = (
                request_row[2] == current_user.username
                and has_iam_permission("requests.create")
                and active[1] == "i.heydari"
            )
            if not permitted:
                conn.rollback()
                abort(403)
            cur.execute(
                """UPDATE Project.approvals
                   SET decision='waiting',comment=NULL,decided_at=NULL
                   WHERE request_id=? AND decision IN ('pending','waiting')""",
                request_id,
            )
            cur.execute(
                """UPDATE Project.requests SET status='submitted',updated_at=GETDATE()
                   WHERE id=? AND status='pending'""",
                request_id,
            )
            details = f"ثبت‌کننده پادرا ارجاع درخواست به مدیرعامل را پس گرفت. درخواست به کارتابل ثبت‌کننده برگشت. علت: {reason}"
            action_name = "پس گرفتن ارجاع توسط ثبت‌کننده پادرا"
            success_message = "ارجاع با موفقیت پس گرفته شد و درخواست به کارتابل شما برگشت."
        else:
            if active[0] not in (2, 3):
                conn.rollback()
                abort(403)
            previous = next((a for a in approvals if a[0] == active[0] - 1), None)
            if not previous or previous[3] != "approved":
                conn.rollback()
                abort(403)

            if previous[0] == 1:
                permitted = (
                    previous[1] == current_user.username
                    and has_iam_permission("requests.approve.padra")
                )
            else:
                permitted = (
                    previous[1] == current_user.username
                    and has_iam_permission("requests.approve.step2")
                    and has_iam_permission("requests.assign_specialist")
                )
            downstream_not_acted = all(
                a[3] in ("pending", "waiting")
                for a in approvals
                if a[0] >= active[0]
            )
            if not permitted or not downstream_not_acted:
                conn.rollback()
                abort(403)

            previous_step = previous[0]
            cur.execute(
                """UPDATE Project.approvals
                   SET decision='pending',comment=NULL,decided_at=NULL
                   WHERE request_id=? AND step_no=? AND decision='approved'""",
                request_id, previous_step,
            )
            cur.execute(
                """UPDATE Project.approvals
                   SET decision='waiting',comment=NULL,decided_at=NULL
                   WHERE request_id=? AND step_no>=?""",
                request_id, active[0],
            )
            cur.execute(
                """UPDATE Project.approvals
                   SET approver_username=N'UNASSIGNED_SPECIALIST',
                       approver_title=N'کارشناس خرید داخلی گروه صنعتی سرکان'
                   WHERE request_id=? AND step_no=3""",
                request_id,
            )
            cur.execute(
                """UPDATE Project.requests
                   SET status='pending',assigned_specialist=NULL,updated_at=GETDATE()
                   WHERE id=?""",
                request_id,
            )
            details = (
                f"ارجاع از مرحله {active[0]} پس گرفته شد و درخواست به کارتابل مرحله "
                f"{previous_step} ({previous[2]}) برگشت. علت: {reason}"
            )
            action_name = "پس گرفتن ارجاع توسط مسئول مرحله قبل"
            success_message = f"ارجاع پس گرفته شد و درخواست به کارتابل «{previous[2]}» برگشت."

        cur.execute(
            """INSERT INTO Project.request_history(request_id,action_name,actor_username,details)
               VALUES (?,?,?,?)""",
            request_id, action_name, current_user.username, details,
        )
        conn.commit()
        flash(success_message, "success")
    except HTTPException:
        if conn:
            conn.rollback()
        raise
    except Exception:
        if conn:
            conn.rollback()
        app.logger.exception("Purchase request referral withdrawal failed")
        flash("پس گرفتن ارجاع انجام نشد؛ تغییری در اطلاعات ثبت نشد.", "error")
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()
    return redirect(url_for("view_request", request_id=request_id))


@app.route("/request/<int:request_id>/return-to-previous", methods=["POST"])
@login_required
def return_request_to_previous(request_id):
    """بازگرداندن درخواست توسط مسئول فعلی به کارتابل مرحله قبلی."""
    reason = request.form.get("return_reason", "").strip()
    if not reason:
        flash("ثبت علت بازگشت درخواست الزامی است.", "error")
        return redirect(url_for("view_request", request_id=request_id))

    conn = cur = None
    try:
        conn = data_db()
        conn.autocommit = False
        cur = conn.cursor()
        cur.execute(
            """SELECT workflow_type,status,requester_username
               FROM Project.requests WITH (UPDLOCK, ROWLOCK)
               WHERE id=?""",
            request_id,
        )
        request_row = cur.fetchone()
        if not request_row or request_row[0] != "padra" or request_row[1] != "pending":
            conn.rollback()
            abort(403)

        cur.execute(
            """SELECT step_no,approver_username,approver_title,decision
               FROM Project.approvals WITH (UPDLOCK, ROWLOCK)
               WHERE request_id=? ORDER BY step_no""",
            request_id,
        )
        approvals = cur.fetchall()
        active = next((a for a in approvals if a[3] == "pending"), None)
        if not active or active[1] != current_user.username:
            conn.rollback()
            abort(403)

        if active[0] == 1:
            permitted = has_iam_permission("requests.approve.padra")
            if not permitted:
                conn.rollback()
                abort(403)
            cur.execute(
                """UPDATE Project.approvals
                   SET decision='waiting',comment=NULL,decided_at=NULL
                   WHERE request_id=? AND decision IN ('pending','waiting')""",
                request_id,
            )
            cur.execute(
                """UPDATE Project.requests SET status='submitted',updated_at=GETDATE()
                   WHERE id=?""",
                request_id,
            )
            action_name = "بازگشت درخواست از مدیرعامل به ثبت‌کننده پادرا"
            details = f"مدیرعامل پادرا درخواست را به کارتابل ثبت‌کننده ({request_row[2]}) بازگرداند. علت: {reason}"
            success_message = "درخواست به کارتابل ثبت‌کننده پادرا بازگردانده شد."
        elif active[0] == 2:
            previous = next((a for a in approvals if a[0] == 1), None)
            permitted = (
                has_iam_permission("requests.approve.step2")
                and has_iam_permission("requests.assign_specialist")
                and previous
                and previous[3] == "approved"
            )
            if not permitted:
                conn.rollback()
                abort(403)
            cur.execute(
                """UPDATE Project.approvals
                   SET decision='pending',comment=NULL,decided_at=NULL
                   WHERE request_id=? AND step_no=1 AND decision='approved'""",
                request_id,
            )
            cur.execute(
                """UPDATE Project.approvals
                   SET decision='waiting',comment=NULL,decided_at=NULL
                   WHERE request_id=? AND step_no>=2""",
                request_id,
            )
            cur.execute(
                """UPDATE Project.approvals
                   SET approver_username=N'UNASSIGNED_SPECIALIST',
                       approver_title=N'کارشناس خرید داخلی گروه صنعتی سرکان'
                   WHERE request_id=? AND step_no=3""",
                request_id,
            )
            cur.execute(
                """UPDATE Project.requests SET status='pending',assigned_specialist=NULL,updated_at=GETDATE()
                   WHERE id=?""",
                request_id,
            )
            action_name = "بازگشت درخواست از مدیر خرید به مدیرعامل پادرا"
            details = f"مدیر خرید داخلی سرکان درخواست را به کارتابل مدیرعامل پادرا بازگرداند. علت: {reason}"
            success_message = "درخواست به کارتابل مدیرعامل پادرا بازگردانده شد."
        elif active[0] == 3:
            previous = next((a for a in approvals if a[0] == 2), None)
            permitted = (
                has_iam_permission("requests.approve.step3")
                and previous
                and previous[3] == "approved"
            )
            if not permitted:
                conn.rollback()
                abort(403)
            cur.execute(
                """UPDATE Project.approvals
                   SET decision='pending',comment=NULL,decided_at=NULL
                   WHERE request_id=? AND step_no=2 AND decision='approved'""",
                request_id,
            )
            cur.execute(
                """UPDATE Project.approvals
                   SET approver_username=N'UNASSIGNED_SPECIALIST',
                       approver_title=N'کارشناس خرید داخلی گروه صنعتی سرکان',
                       decision='waiting',comment=NULL,decided_at=NULL
                   WHERE request_id=? AND step_no=3""",
                request_id,
            )
            cur.execute(
                """UPDATE Project.requests SET status='pending',assigned_specialist=NULL,updated_at=GETDATE()
                   WHERE id=?""",
                request_id,
            )
            action_name = "بازگشت درخواست از کارشناس خرید به مدیر خرید سرکان"
            details = f"کارشناس خرید درخواست را به کارتابل مدیر خرید داخلی سرکان بازگرداند. علت: {reason}"
            success_message = "درخواست به کارتابل مدیر خرید داخلی سرکان بازگردانده شد."
        else:
            conn.rollback()
            abort(403)

        cur.execute(
            """INSERT INTO Project.request_history(request_id,action_name,actor_username,details)
               VALUES (?,?,?,?)""",
            request_id, action_name, current_user.username, details,
        )
        conn.commit()
        flash(success_message, "success")
    except HTTPException:
        if conn:
            conn.rollback()
        raise
    except Exception:
        if conn:
            conn.rollback()
        app.logger.exception("Purchase request previous-stage return failed")
        flash("بازگرداندن درخواست انجام نشد؛ تغییری در اطلاعات ثبت نشد.", "error")
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()
    return redirect(url_for("view_request", request_id=request_id))


@app.route("/request/<int:request_id>/resubmit", methods=["POST"])
@login_required
def resubmit_request(request_id):
    """ارسال مجدد درخواست توسط ثبت‌کننده پس از پس گرفتن ارجاع یا بازگشت از مدیرعامل."""
    if not has_iam_permission("requests.create"):
        abort(403)
    conn = cur = None
    try:
        conn = data_db()
        conn.autocommit = False
        cur = conn.cursor()
        cur.execute(
            """SELECT workflow_type,status,requester_username
               FROM Project.requests WITH (UPDLOCK, ROWLOCK)
               WHERE id=?""",
            request_id,
        )
        row = cur.fetchone()
        if not row or row[0] != "padra" or row[1] != "submitted" or row[2] != current_user.username:
            conn.rollback()
            abort(403)
        cur.execute(
            """SELECT id,decision,approver_username FROM Project.approvals WITH (UPDLOCK, ROWLOCK)
               WHERE request_id=? AND step_no=1""",
            request_id,
        )
        first = cur.fetchone()
        if not first or first[1] != "waiting" or first[2] != "i.heydari":
            conn.rollback()
            abort(403)
        cur.execute(
            """UPDATE Project.approvals
               SET decision='pending',comment=NULL,decided_at=NULL
               WHERE id=? AND decision='waiting'""",
            first[0],
        )
        cur.execute(
            """UPDATE Project.requests SET status='pending',updated_at=GETDATE()
               WHERE id=? AND status='submitted'""",
            request_id,
        )
        cur.execute(
            """INSERT INTO Project.request_history(request_id,action_name,actor_username,details)
               VALUES (?,N'ارسال مجدد درخواست به مدیرعامل پادرا',?,N'ثبت‌کننده درخواست را پس از بازبینی مجدداً به کارتابل مدیرعامل پادرا ارسال کرد.')""",
            request_id, current_user.username,
        )
        conn.commit()
        flash("درخواست مجدداً به کارتابل مدیرعامل پادرا ارسال شد.", "success")
    except HTTPException:
        if conn:
            conn.rollback()
        raise
    except Exception:
        if conn:
            conn.rollback()
        app.logger.exception("Purchase request resubmission failed")
        flash("ارسال مجدد درخواست انجام نشد.", "error")
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()
    return redirect(url_for("view_request", request_id=request_id))


@app.route("/request/<int:request_id>/cancel", methods=["POST"])
@login_required
def cancel_request(request_id):
    if not has_iam_permission("requests.create"):
        abort(403)
    reason = request.form.get("cancel_reason", "").strip()
    if not reason:
        flash("ثبت علت لغو درخواست الزامی است.", "error")
        return redirect(url_for("view_request", request_id=request_id))
    conn = cur = None
    try:
        conn = data_db()
        conn.autocommit = False
        cur = conn.cursor()
        cur.execute(
            """SELECT r.requester_username,r.workflow_type,r.status,a.id
               FROM Project.requests r
               INNER JOIN Project.approvals a ON a.request_id=r.id
               WHERE r.id=? AND a.step_no=1 AND a.decision='pending'
                 AND a.approver_username=N'i.heydari'""",
            request_id,
        )
        row = cur.fetchone()
        if not row or row[0] != current_user.username or row[1] != "padra" or row[2] != "pending":
            conn.rollback()
            abort(403)
        cur.execute(
            """UPDATE Project.approvals
               SET decision='rejected',comment=?,decided_at=GETDATE()
               WHERE request_id=? AND decision IN ('pending','waiting')""",
            reason or "لغو درخواست توسط کارشناس ثبت خرید پادرا پیش از بررسی مدیرعامل",
            request_id,
        )
        cur.execute(
            "UPDATE Project.requests SET status='cancelled',updated_at=GETDATE() WHERE id=?",
            request_id,
        )
        details = "درخواست توسط کارشناس ثبت خرید پادرا، پیش از اقدام مدیرعامل لغو شد."
        if reason:
            details += " علت لغو: " + reason
        cur.execute(
            """INSERT INTO Project.request_history(request_id,action_name,actor_username,details)
               VALUES (?,N'لغو درخواست توسط ثبت‌کننده پادرا',?,?)""",
            request_id,current_user.username,details,
        )
        conn.commit()
        flash("درخواست با موفقیت لغو شد.", "success")
    except HTTPException:
        if conn:
            conn.rollback()
        raise
    except Exception:
        if conn:
            conn.rollback()
        app.logger.exception("Padra request cancellation failed")
        flash("لغو درخواست انجام نشد.", "error")
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()
    return redirect(url_for("view_request", request_id=request_id))



@app.route("/request/<int:request_id>/items/<int:item_id>/reassign", methods=["POST"])
@login_required
def reassign_item_specialist(request_id, item_id):
    if not has_iam_permission("requests.reassign_specialist"):
        abort(403)
    new_specialist = request.form.get("assigned_specialist", "").strip()
    specialists = {sp["username"]: sp for sp in get_purchase_specialists()}
    if new_specialist not in specialists:
        flash("کارشناس خرید انتخاب‌شده معتبر نیست.", "error")
        return redirect(url_for("view_request", request_id=request_id))
    conn = cur = None
    try:
        conn = data_db()
        conn.autocommit = False
        cur = conn.cursor()
        cur.execute(
            """SELECT ia.id,ia.assignment_status,i.item_description
               FROM Project.request_item_assignments ia
               INNER JOIN Project.request_items i ON i.id=ia.request_item_id
               INNER JOIN Project.requests r ON r.id=ia.request_id
               WHERE ia.request_id=? AND ia.request_item_id=? AND r.status='pending'""",
            request_id, item_id,
        )
        assignment = cur.fetchone()
        if not assignment or assignment[1] not in {"assigned", "rejected"}:
            conn.rollback()
            abort(403)
        cur.execute(
            """UPDATE Project.request_item_assignments
               SET specialist_username=?,assignment_status='assigned',
                   assigned_by=?,assigned_at=SYSUTCDATETIME(),
                   completed_by=NULL,completed_at=NULL,completion_comment=NULL
               WHERE id=?""",
            new_specialist, current_user.username, assignment[0],
        )
        cur.execute(
            """INSERT INTO Project.request_history(request_id,action_name,actor_username,details)
               VALUES (?,N'تغییر کارشناس یک قلم کالا',?,?)""",
            request_id, current_user.username,
            f"مسئول خرید قلم «{assignment[2]}» به {specialists[new_specialist]['full_name']} ({new_specialist}) تغییر کرد.",
        )
        conn.commit()
        flash("کارشناس مسئول این قلم تغییر کرد.", "success")
    except HTTPException:
        if conn:
            conn.rollback()
        raise
    except Exception:
        if conn:
            conn.rollback()
        app.logger.exception("Item specialist reassignment failed")
        flash("تغییر کارشناس این قلم انجام نشد.", "error")
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()
    return redirect(url_for("view_request", request_id=request_id))


@app.route("/request/<int:request_id>/reassign", methods=["POST"])
@login_required
def reassign_specialist(request_id):
    """تغییر کارشناس مسئول بر اساس مجوز مرکزی IAM."""
    if not has_iam_permission("requests.reassign_specialist"):
        abort(403)
    new_specialist = request.form.get("assigned_specialist", "").strip()
    note = request.form.get("reassign_note", "").strip()
    if new_specialist not in {s["username"] for s in get_purchase_specialists()}:
        flash("کارشناس خرید انتخاب‌شده معتبر نیست.", "error")
        return redirect(url_for("view_request", request_id=request_id))

    spec_info = next(s for s in get_purchase_specialists() if s["username"] == new_specialist)
    spec_title = f"{spec_info['role_title']} - {spec_info['full_name']}"
    conn = cur = None
    try:
        conn = data_db()
        conn.autocommit = False
        cur = conn.cursor()
        cur.execute(
            """SELECT id FROM Project.approvals
               WHERE request_id=? AND step_no=3 AND decision='pending'""",
            request_id,
        )
        if not cur.fetchone():
            conn.rollback()
            abort(403)
        cur.execute(
            """UPDATE Project.request_item_assignments
               SET specialist_username=?,assigned_by=?,assigned_at=SYSUTCDATETIME()
               WHERE request_id=? AND assignment_status='assigned'""",
            spec_info["username"], current_user.username, request_id,
        )
        cur.execute(
            """UPDATE Project.requests
               SET assigned_specialist=NULL, updated_at=GETDATE()
               WHERE id=? AND workflow_type IN ('serkan','padra')""",
            request_id,
        )
        details = f"تغییر کارشناس مسئول خرید توسط مدیر خرید داخلی سرکان به: {spec_info['full_name']} ({spec_info['role_title']})"
        if note:
            details += f" — توضیح: {note}"
        cur.execute(
            """INSERT INTO Project.request_history(request_id,action_name,actor_username,details)
               VALUES (?,N'تغییر کارشناس خرید مسئول',?,?)""",
            request_id,
            current_user.username,
            details,
        )
        conn.commit()
        flash(f"مسئولیت پیگیری و خرید به «{spec_info['full_name']}» منتقل شد.", "success")
    except HTTPException:
        if conn:
            conn.rollback()
        raise
    except Exception:
        if conn:
            conn.rollback()
        app.logger.exception("Specialist reassignment failed")
        flash("تغییر کارشناس خرید انجام نشد.", "error")
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()
    return redirect(url_for("view_request", request_id=request_id))


@app.errorhandler(403)
def forbidden(_):
    return render_template("error.html", code=403, message="شما مجوز مشاهده یا انجام این عملیات را ندارید."), 403


@app.errorhandler(404)
def not_found(_):
    return render_template("error.html", code=404, message="صفحه یا درخواست موردنظر پیدا نشد."), 404


@app.route("/healthz")
def healthz():
    """Lightweight process health check; intentionally does not require a database."""
    return {"status": "ok", "service": "padra-purchase-request"}, 200


if __name__ == "__main__":
    from waitress import serve
    serve(app, host=os.getenv("HOST", "0.0.0.0"), port=int(os.getenv("PORT", "3942")))
