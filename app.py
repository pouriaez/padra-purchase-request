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
from flask import Flask, Response, flash, redirect, render_template, request, url_for, abort, session, g
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
    {"username": "a.zafari", "full_name": "آرزو آذرلی"},
]


def get_purchase_specialists():
    conn = cur = None
    try:
        conn = auth_db()
        cur = conn.cursor()
        cur.execute(
            """SELECT DISTINCT u.username
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
                "full_name": display_names.get(item[0], item[0]),
                "role_title": "کارشناس خرید داخلی",
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
            """
        )

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
            return pyodbc.connect(conn_string(auth), timeout=8)
        except Exception as exc:
            prefix = "AUTH_DB_" if auth else "DATA_DB_"
            app.logger.exception("%s SQL Server connection failed", prefix)
            raise RuntimeError(prefix + " SQL Server connection failed. Check server, database, ODBC driver, network access and SQL login credentials.") from exc
    return SQLiteCompatConnection(LOCAL_DB_PATH)


def auth_db():
    return db_connect(auth=True)


def data_db():
    return db_connect(auth=False)


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
            "role_title": "کارشناس خرید داخلی",
            "org": "گروه صنعتی سرکان",
            "badge_color": "teal",
        }
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
           WHERE r.workflow_type = 'serkan'
             AND r.status = 'pending' AND a.decision = 'pending'
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
                 SUM(CASE WHEN a.decision = 'pending' THEN 1 ELSE 0 END),
                 SUM(CASE WHEN a.decision = 'approved' THEN 1 ELSE 0 END)
               FROM Project.approvals a
               INNER JOIN Project.requests r ON r.id = a.request_id
               WHERE r.workflow_type = 'serkan'
                 AND a.approver_username = ?""",
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
            """(r.workflow_type = 'serkan' AND EXISTS (
                SELECT 1 FROM Project.approvals ax
                WHERE ax.request_id = r.id AND ax.step_no IN (2, 3)
            ))"""
        )
    if "requests.view_own" in permissions:
        clauses.append("(r.workflow_type = 'padra' AND r.requester_username = ?)")
        params.append(current_user.username)
    if "requests.view_assigned" in permissions:
        clauses.append(
            """(r.workflow_type = 'serkan' AND (
                r.assigned_specialist = ? OR EXISTS (
                    SELECT 1 FROM Project.approvals ax
                    WHERE ax.request_id = r.id
                      AND ax.step_no = 3
                      AND ax.approver_username = ?
                )
            ))"""
        )
        params.extend([current_user.username, current_user.username])
    if not clauses:
        abort(403)
    return "(" + " OR ".join(clauses) + ")", params


_FALLBACK_ACTIVE_USERNAME = "serkan_purchase_mgr"


@app.before_request
def _auto_authenticate_preview_if_needed():
    if request.endpoint in ("static", "login", "logout", "switch_user"):
        return
    if not current_user.is_authenticated and not _has_sqlserver_config(auth=True):
        if not session.get("explicit_logout"):
            u = verify_user_by_username(_FALLBACK_ACTIVE_USERNAME)
            if u:
                login_user(u, remember=False)


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


def get_request(request_id):
    conn = data_db()
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT id, request_no, request_date_fa, request_type, unit_name, supply_location,
                      fixed_asset, request_reason, requester_username, status, created_at, updated_at,
                      workflow_type
               FROM Project.requests WHERE id=?""",
            request_id,
        )
        header = cur.fetchone()
        if not header:
            return None
        cur.execute(
            """SELECT line_no,item_code,item_description,unit_name,quantity,needed_date_fa,usage_location
               FROM Project.request_items WHERE request_id=? ORDER BY line_no""",
            request_id,
        )
        items = cur.fetchall()
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
        bi_stats = compute_bi_analytics(cur) if has_iam_permission("requests.assign_specialist") else None
        return {
            "header": header,
            "items": items,
            "approvals": approvals,
            "history": history,
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
        allowed_statuses = {"pending", "submitted", "approved", "rejected", "my_turn"}
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
        counts = {key: 0 for key in ("all", "pending", "submitted", "approved", "rejected", "my_turn")}
        for status_name, total in cur.fetchall():
            counts["all"] += total
            if status_name in counts:
                counts[status_name] = total

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
            counts["my_turn"] = cur.fetchone()[0] or 0
        else:
            counts["my_turn"] = 0
        bi_stats = compute_bi_analytics(cur) if "requests.assign_specialist" in permissions else None

        where = visibility
        params = list(vis_params)
        if status_filter == "my_turn":
            if not approval_steps:
                abort(403)
            step_placeholders = ",".join("?" for _ in approval_steps)
            where += f""" AND r.status='pending' AND EXISTS (
                         SELECT 1 FROM Project.approvals am
                         WHERE am.request_id=r.id AND am.decision='pending'
                           AND am.approver_username=? AND am.step_no IN ({step_placeholders}))"""
            params.append(current_user.username)
            params.extend(sorted(approval_steps))
        elif status_filter:
            where += " AND r.status=?"
            params.append(status_filter)

        if type_filter:
            where += " AND r.request_type=?"
            params.append(type_filter)

        if search_text:
            where += """ AND (r.request_no LIKE ? OR r.unit_name LIKE ?
                         OR r.requester_username LIKE ? OR r.request_reason LIKE ?)"""
            like = f"%{search_text}%"
            params.extend([like, like, like, like])

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
                    r.workflow_type
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
               WHERE {where}
               ORDER BY r.id DESC""",
            *params,
        )
        rows = cur.fetchall()
        return render_template(
            "index.html",
            rows=rows,
            username=current_user.username,
            counts=counts,
            bi_stats=bi_stats,
            status_filter=status_filter,
            type_filter=type_filter,
            search_text=search_text,
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
            upd_str = r[8].strftime("%Y/%m/%d %H:%M") if isinstance(r[8], datetime) else str(r[8] or "")
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
        if not unit_name or not supply_location:
            flash("واحد درخواست‌کننده و محل تأمین الزامی هستند.", "error")
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
                if not unit or not destination:
                    raise ValueError(f"واحد سنجش و محل مصرف ردیف {i+1} الزامی است.")
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
                """INSERT INTO Project.requests
                   (request_date,request_date_fa,request_type,unit_name,supply_location,fixed_asset,
                    request_reason,requester_username,workflow_type,status)
                   OUTPUT INSERTED.id VALUES (?,?,?,?,?,?,?,?,?,'pending')""",
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
            request_id = cur.fetchone()[0]
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
                       VALUES (?,1,N'i.heydari',N'مدیرعامل شرکت پادرا','waiting')""",
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
                       VALUES (?,N'ارجاع به مدیرعامل شرکت پادرا',?,?)""",
                    request_id,
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
            and h[12] == "serkan"
            and any(a[0] in (2, 3) for a in data["approvals"])
        )
        or (
            "requests.view_assigned" in permissions
            and h[12] == "serkan"
            and any(a[0] == 3 and a[1] == current_user.username for a in data["approvals"])
        )
    )
    if not can_view:
        abort(403)
    return render_template("request_detail.html", data=data, username=current_user.username)


@app.route("/request/<int:request_id>/decision", methods=["POST"])
@login_required
def decide_request(request_id):
    decision = request.form.get("decision", "").strip()
    comment = request.form.get("comment", "").strip()
    selected_specialist = request.form.get("assigned_specialist", "").strip()
    po_reference = request.form.get("po_reference", "").strip()

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
        if workflow_type == "padra":
            if current_step_no != 1 or not has_iam_permission("requests.approve.padra"):
                conn.rollback()
                abort(403)
        elif not has_iam_permission(f"requests.approve.step{current_step_no}"):
            conn.rollback()
            abort(403)
        if current_step_no == 2 and not has_iam_permission("requests.assign_specialist"):
            conn.rollback()
            abort(403)

        specialist_info = None
        if decision == "approved" and current_step_no == 2:
            if not selected_specialist or selected_specialist not in {s["username"] for s in get_purchase_specialists()}:
                conn.rollback()
                flash("لطفاً کارشناس خرید موردنظر خود را جهت محول‌سازی سفارش انتخاب نمایید.", "error")
                return redirect(url_for("view_request", request_id=request_id))
            specialist_info = next(s for s in get_purchase_specialists() if s["username"] == selected_specialist)

        full_comment = comment
        if po_reference and current_step_no == 3:
            full_comment = f"شماره سفارش/پیش‌فاکتور: {po_reference}" + (f" — {comment}" if comment else "")

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
            if current_step_no == 2 and specialist_info:
                spec_title = f"{specialist_info['role_title']} - {specialist_info['full_name']}"
                cur.execute(
                    """UPDATE Project.approvals
                       SET approver_username=?, approver_title=?
                       WHERE request_id=? AND step_no=3""",
                    specialist_info["username"],
                    spec_title,
                    request_id,
                )
                cur.execute(
                    """UPDATE Project.requests
                       SET assigned_specialist=?, updated_at=GETDATE()
                       WHERE id=? AND workflow_type='serkan'""",
                    specialist_info["username"],
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
                       VALUES (?,N'تأیید نهایی مدیرعامل پادرا',?,?)""",
                    request_id,
                    current_user.username,
                    details,
                )
            else:
                cur.execute(
                    """INSERT INTO Project.request_history(request_id,action_name,actor_username,details)
                       VALUES (?,N'تأیید مدیرعامل شرکت پادرا',?,?)""",
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
                        f"درخواست پس از تأیید مدیرعامل پادرا به مرحله {next_step[0]} ({next_step[2]} - {next_step[1]}) محول شد تا کارشناس خرید تعیین گردد.",
                    )
        elif current_step_no == 2 and specialist_info:
            details = (
                f"تأیید توسط مدیر خرید داخلی گروه صنعتی سرکان و محول‌سازی به "
                f"{specialist_info['full_name']} ({specialist_info['role_title']})"
            )
            if full_comment:
                details += f" — دستور خرید: {full_comment}"
            cur.execute(
                """INSERT INTO Project.request_history(request_id,action_name,actor_username,details)
                   VALUES (?,N'تخصیص و محول‌سازی به کارشناس خرید',?,?)""",
                request_id,
                current_user.username,
                details,
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
        if decision == "approved" and workflow_type == "padra":
            flash("تأیید نهایی مدیرعامل پادرا ثبت شد؛ گردش کار این درخواست در همین مرحله به پایان رسید.", "success")
        elif decision == "approved" and current_step_no == 1:
            flash("تأیید مدیرعامل شرکت پادرا ثبت شد و درخواست به کارتابل مدیر خرید داخلی گروه صنعتی سرکان محول گردید.", "success")
        elif decision == "approved" and current_step_no == 2 and specialist_info:
            flash(f"درخواست با موفقیت تأیید و به کارتابل «{specialist_info['full_name']} ({specialist_info['role_title']})» محول شد.", "success")
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
            """UPDATE Project.approvals
               SET approver_username=?, approver_title=?
               WHERE request_id=? AND step_no=3 AND decision='pending'""",
            spec_info["username"],
            spec_title,
            request_id,
        )
        cur.execute(
            """UPDATE Project.requests
               SET assigned_specialist=?, updated_at=GETDATE()
               WHERE id=? AND workflow_type='serkan'""",
            spec_info["username"],
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
