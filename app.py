import os, secrets
from pathlib import Path
from datetime import datetime
from decimal import Decimal, InvalidOperation
from functools import wraps

import jdatetime
import pyodbc
from dotenv import load_dotenv
from flask import Flask, flash, redirect, render_template, request, url_for, abort
from flask_login import LoginManager, UserMixin, current_user, login_required, login_user, logout_user
from flask_wtf.csrf import CSRFProtect
from werkzeug.exceptions import HTTPException

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(dotenv_path=BASE_DIR / '.env', override=False)
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY") or secrets.token_hex(32)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024
CSRFProtect(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "برای ادامه ابتدا وارد سامانه شوید."
login_manager.login_message_category = "warning"


def conn_string(auth=False):
    """Build a SQL Server connection string using the project's .env settings.

    Both authentication and purchase-request data are stored in bi.
    """
    prefix = "AUTH_DB_" if auth else "DATA_DB_"
    server = os.getenv(prefix + "SERVER", r"192.168.120.38\odsm")
    database = os.getenv(prefix + "NAME", "bi")
    driver = os.getenv(prefix + "DRIVER", "ODBC Driver 17 for SQL Server")
    user = os.getenv(prefix + "USER", "").strip()
    password = os.getenv(prefix + "PASSWORD", "")
    encrypt = os.getenv(prefix + "ENCRYPT", "no")
    trust_certificate = os.getenv(prefix + "TRUST_SERVER_CERTIFICATE", "yes")

    # Match productionplan_padra1: SQL Server Authentication only.
    # Never silently fall back to the current Windows account.
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
    return pyodbc.connect(conn_string(auth), timeout=8)


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


class User(UserMixin):
    def __init__(self, user_id, username):
        self.id, self.username = str(user_id), str(username)


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
        if cur: cur.close()
        if conn: conn.close()


def verify_user(username, password):
    conn = cur = None
    try:
        conn = auth_db()
        cur = conn.cursor()
        # Kept compatible with the reference app's current password_hash comparison.
        cur.execute("""SELECT TOP (1) id, username FROM [Project].[loginusers]
                       WHERE username = ? AND password_hash = ?""", username, password)
        row = cur.fetchone()
        return User(row[0], row[1]) if row else None
    finally:
        if cur: cur.close()
        if conn: conn.close()


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
        cur.execute("""SELECT id, request_no, request_date_fa, request_type, unit_name, supply_location,
                              fixed_asset, request_reason, requester_username, status
                       FROM Project.requests WHERE id=?""", request_id)
        header = cur.fetchone()
        if not header: return None
        cur.execute("""SELECT line_no,item_code,item_description,unit_name,quantity,needed_date_fa,usage_location
                       FROM Project.request_items WHERE request_id=? ORDER BY line_no""", request_id)
        items = cur.fetchall()
        cur.execute("""SELECT step_no,approver_username,approver_title,decision,comment,decided_at
                       FROM Project.approvals WHERE request_id=? ORDER BY step_no""", request_id)
        approvals = cur.fetchall()
        cur.execute("""SELECT action_name,actor_username,details,created_at FROM Project.request_history
                       WHERE request_id=? ORDER BY created_at,id""", request_id)
        return {"header": header, "items": items, "approvals": approvals, "history": cur.fetchall()}
    finally:
        conn.close()


@login_manager.unauthorized_handler
def unauthorized():
    return redirect(url_for("login", next=request.url))


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated: return redirect(url_for("index"))
    if request.method == "POST":
        username, password = request.form.get("username", "").strip(), request.form.get("password", "")
        try:
            user = verify_user(username, password)
            if user:
                login_user(user, remember=False)
                return redirect(request.args.get("next") or url_for("index"))
            flash("نام کاربری یا رمز عبور صحیح نیست.", "error")
        except Exception:
            app.logger.exception("Login failed")
            flash("ارتباط با پایگاه داده کاربران برقرار نشد؛ تنظیمات اتصال دیتابیس را بررسی کنید.", "error")
    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    conn = data_db()
    try:
        cur = conn.cursor()
        status_filter = request.args.get("status", "").strip()
        search_text = request.args.get("q", "").strip()[:80]
        allowed_statuses = {"pending", "submitted", "approved", "rejected"}
        if status_filter not in allowed_statuses:
            status_filter = ""
        where = """(r.requester_username=? OR EXISTS
                    (SELECT 1 FROM Project.approvals ax
                     WHERE ax.request_id=r.id AND ax.approver_username=?))"""
        params = [current_user.username, current_user.username]
        if status_filter:
            where += " AND r.status=?"
            params.append(status_filter)
        if search_text:
            where += """ AND (r.request_no LIKE ? OR r.unit_name LIKE ?
                         OR r.requester_username LIKE ? OR r.request_reason LIKE ?)"""
            like = f"%{search_text}%"
            params.extend([like, like, like, like])
        cur.execute(f"""SELECT TOP (100)
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
                       WHERE {where}
                       ORDER BY r.id DESC""", *params)
        rows = cur.fetchall()
        counts = {key: 0 for key in ("all", "pending", "submitted", "approved", "rejected")}
        for row in rows:
            counts["all"] += 1
            if row[5] in counts:
                counts[row[5]] += 1
        return render_template("index.html", rows=rows, username=current_user.username,
                               counts=counts, status_filter=status_filter, search_text=search_text)
    finally:
        conn.close()


@app.route("/request/new", methods=["GET", "POST"])
@login_required
def new_request():
    if request.method == "POST":
        request_type = request.form.get("request_type", "goods")
        if request_type not in {"goods", "service", "equipment", "other"}:
            flash("نوع درخواست نامعتبر است.", "error")
            return redirect(url_for("new_request"))
        unit_name = request.form.get("unit_name", "").strip()
        supply_location = request.form.get("supply_location", "").strip()
        fixed_asset = request.form.get("fixed_asset") == "yes"
        reason = request.form.get("request_reason", "").strip()
        fields = [request.form.getlist(k) for k in
                  ("item_code[]", "item_description[]", "item_unit[]", "quantity[]", "needed_date[]", "usage_location[]")]
        codes, descriptions, units, quantities, needed_dates, locations = fields
        if not unit_name or not supply_location:
            flash("واحد درخواست‌کننده و محل تأمین الزامی هستند.", "error")
            return redirect(url_for("new_request"))
        items = []
        try:
            for i, desc in enumerate(descriptions):
                desc = desc.strip()
                if not desc: continue
                qty = Decimal(normalize_digits(quantities[i]).replace(",", ""))
                if qty <= 0: raise ValueError(f"مقدار ردیف {i+1} باید بیشتر از صفر باشد.")
                needed = validate_jalali_date(needed_dates[i], f"تاریخ نیاز ردیف {i+1}")
                unit, destination = units[i].strip(), locations[i].strip()
                if not unit or not destination:
                    raise ValueError(f"واحد سنجش و محل مصرف ردیف {i+1} الزامی است.")
                items.append((len(items)+1, codes[i].strip() or None, desc, unit, qty, needed, destination))
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
            cur.execute("""INSERT INTO Project.requests
                (request_date,request_date_fa,request_type,unit_name,supply_location,fixed_asset,
                 request_reason,requester_username,status)
                OUTPUT INSERTED.id VALUES (?,?,?,?,?,?,?,?,'pending')""",
                now.date(), jalali, request_type, unit_name, supply_location, fixed_asset,
                reason or None, current_user.username)
            request_id = cur.fetchone()[0]
            request_no = f"PR-{jalali.replace('/', '')}-{request_id:06d}"
            cur.execute("UPDATE Project.requests SET request_no=? WHERE id=?", request_no, request_id)
            for item in items:
                cur.execute("""INSERT INTO Project.request_items
                    (request_id,line_no,item_code,item_description,unit_name,quantity,needed_date_fa,usage_location)
                    VALUES (?,?,?,?,?,?,?,?)""", request_id, *item)
            cur.execute("""INSERT INTO Project.approvals(request_id,step_no,approver_username,approver_title,decision)
                SELECT ?,step_no,approver_username,approver_title,'waiting'
                FROM Project.approval_routes WHERE request_type=? AND is_active=1""", request_id, request_type)
            cur.execute("""INSERT INTO Project.request_history(request_id,action_name,actor_username,details)
                VALUES (?,N'ثبت درخواست',?,N'درخواست خرید ایجاد شد.')""", request_id, current_user.username)
            cur.execute("""SELECT TOP (1) id,step_no,approver_username,approver_title
                           FROM Project.approvals WHERE request_id=? ORDER BY step_no""", request_id)
            first_approval = cur.fetchone()
            if first_approval:
                cur.execute("UPDATE Project.approvals SET decision='pending' WHERE id=?", first_approval[0])
                cur.execute("""INSERT INTO Project.request_history(request_id,action_name,actor_username,details)
                               VALUES (?,N'ارجاع برای تأیید',?,?)""", request_id, current_user.username,
                            f"درخواست برای مرحله {first_approval[1]} به {first_approval[3]} ({first_approval[2]}) ارجاع شد.")
            else:
                cur.execute("UPDATE Project.requests SET status='submitted' WHERE id=?", request_id)
            conn.commit()
            flash(f"درخواست {request_no} با موفقیت ثبت شد.", "success")
            if not first_approval:
                flash("درخواست ثبت شد اما مسیر تأیید آن هنوز تعریف نشده است؛ تا زمان تنظیم مسیر، در وضعیت ثبت‌شده باقی می‌ماند.", "warning")
            return redirect(url_for("view_request", request_id=request_id))
        except Exception:
            if conn: conn.rollback()
            app.logger.exception("Purchase request save failed")
            flash("ثبت درخواست انجام نشد؛ تراکنش برگشت داده شد. اتصال دیتابیس و ساختار جداول را بررسی کنید.", "error")
            return redirect(url_for("new_request"))
        finally:
            if cur: cur.close()
            if conn: conn.close()
    return render_template("request_form.html", username=current_user.username, today=jalali_today())


@app.route("/request/<int:request_id>")
@login_required
def view_request(request_id):
    data = get_request(request_id)
    if not data: abort(404)
    h = data["header"]
    if h[8] != current_user.username and not any(a[1] == current_user.username for a in data["approvals"]):
        abort(403)
    return render_template("request_detail.html", data=data, username=current_user.username)


@app.route("/request/<int:request_id>/decision", methods=["POST"])
@login_required
def decide_request(request_id):
    decision, comment = request.form.get("decision"), request.form.get("comment", "").strip()
    if decision not in {"approved", "rejected"}: abort(400)
    conn = cur = None
    try:
        conn = data_db()
        conn.autocommit = False
        cur = conn.cursor()
        cur.execute("""SELECT TOP (1) id FROM Project.approvals
                       WHERE request_id=? AND approver_username=? AND decision='pending'""",
                    request_id, current_user.username)
        approval = cur.fetchone()
        if not approval:
            conn.rollback()
            abort(403)
        cur.execute("""UPDATE Project.approvals SET decision=?,comment=?,decided_at=GETDATE()
                       WHERE id=? AND decision='pending'""", decision, comment or None, approval[0])
        if decision == "rejected":
            status = "rejected"
        else:
            cur.execute("""SELECT TOP (1) id FROM Project.approvals
                           WHERE request_id=? AND decision='waiting' ORDER BY step_no""", request_id)
            next_approval = cur.fetchone()
            if next_approval:
                cur.execute("UPDATE Project.approvals SET decision='pending' WHERE id=?", next_approval[0])
                status = "pending"
            else:
                status = "approved"
        cur.execute("UPDATE Project.requests SET status=?,updated_at=GETDATE() WHERE id=?", status, request_id)
        cur.execute("""SELECT step_no,approver_title FROM Project.approvals WHERE id=?""", approval[0])
        decided_step = cur.fetchone()
        decision_title = "تأیید مرحله" if decision == "approved" else "رد درخواست در مرحله"
        details = f"{decision_title} {decided_step[0]} · {decided_step[1]}"
        if comment:
            details += f" — توضیح: {comment}"
        cur.execute("""INSERT INTO Project.request_history(request_id,action_name,actor_username,details)
                       VALUES (?,?,?,?)""", request_id,
                    "تأیید" if decision == "approved" else "رد درخواست", current_user.username, details)
        if decision == "approved" and next_approval:
            cur.execute("""SELECT step_no,approver_username,approver_title
                           FROM Project.approvals WHERE id=?""", next_approval[0])
            next_step = cur.fetchone()
            cur.execute("""INSERT INTO Project.request_history(request_id,action_name,actor_username,details)
                           VALUES (?,N'ارجاع به مرحله بعد',N'SYSTEM',?)""", request_id,
                        f"درخواست به مرحله {next_step[0]} ارجاع شد: {next_step[2]} ({next_step[1]}).")
        elif decision == "approved":
            cur.execute("""INSERT INTO Project.request_history(request_id,action_name,actor_username,details)
                           VALUES (?,N'تکمیل گردش تأیید',N'SYSTEM',N'تمام مراحل تأیید با موفقیت تکمیل شد.')""", request_id)
        conn.commit()
        flash("نظر شما ثبت شد.", "success")
    except HTTPException:
        raise
    except Exception:
        if conn: conn.rollback()
        app.logger.exception("Approval action failed")
        flash("ثبت تصمیم انجام نشد.", "error")
    finally:
        if cur: cur.close()
        if conn: conn.close()
    return redirect(url_for("view_request", request_id=request_id))


@app.errorhandler(403)
def forbidden(_):
    return render_template("error.html", code=403, message="شما مجوز مشاهده یا انجام این عملیات را ندارید."), 403


@app.errorhandler(404)
def not_found(_):
    return render_template("error.html", code=404, message="صفحه یا درخواست موردنظر پیدا نشد."), 404


if __name__ == "__main__":
    from waitress import serve
    serve(app, host="0.0.0.0", port=int(os.getenv("PORT", "3942")))
