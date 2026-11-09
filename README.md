# سامانه درخواست خرید پادرا

نسخه مستقل سامانه درخواست خرید شرکت پادرا؛ طراحی‌شده با Flask، SQL Server، Waitress، Flask-Login و رابط فارسی RTL. پورت پیش‌فرض اجرا 3942 است.

## قابلیت‌های نسخه اول
- ورود با همان جدول کاربران سازمانی `[Project].[loginusers]` در دیتابیس `bi` و الگوی سازگار با پروژه مرجع.
- اتصال جداگانه دیتابیس احراز هویت و دیتابیس درخواست‌های خرید از طریق متغیرهای محیطی.
- ثبت درخواست خرید با اطلاعات سربرگ، نوع اقلام، محل تأمین، واحد درخواست‌کننده و دارایی ثابت.
- جدول پویای اقلام: افزودن/حذف ردیف، کد و شرح کالا، واحد، مقدار، تاریخ نیاز و محل مصرف.
- شماره درخواست و تاریخ شمسی خودکار.
- گردش تأیید مرحله‌ای قابل پیکربندی در جدول `Project.approval_routes`.
- ثبت تاریخچه عملیات و قابلیت چاپ A4.
- محافظت CSRF و استفاده از پارامترهای SQL.

## راه‌اندازی
1. Python 3.11+ و ODBC Driver 17 یا 18 نصب باشد.
2. فایل `sql/create_purchase_request_schema.sql` را در دیتابیس `bi` اجرا کنید.
3. فایل `sql/002_align_approval_users_with_iam.sql` را اجرا کنید تا ارجاع مرحله اول به `admin` و مرحله دوم به `m.mahdiyon` مطابق تخصیص نقش‌های IAM باشد.
4. پس از اجرای اسکریپت ایجاد ساختار تأمین‌کنندگان `sql/007_supplier_offers_and_padra_cancellation.sql`، فایل `sql/008_supplier_selection_and_delivery_tracking.sql` را اجرا کنید تا انتخاب تأمین‌کننده و ثبت چندمرحله‌ای رسیدهای تحویل فعال شود.
4. در همان دیتابیس، اسکریپت‌های IAM مخزن `serkan-iam` را به ترتیب `sql/001_create_iam_schema.sql`، `sql/002_seed_padra_purchase_permissions.sql` و `sql/003_padra_rbac_enforcement.sql` اجرا کنید.
5. یک محیط مجازی بسازید و وابستگی‌ها را نصب کنید:
   `python -m venv .venv`
   `.venv\\Scripts\\activate`
   `pip install -r requirements-windows.txt`
6. از `.env.example` یک فایل `.env` بسازید و تنظیمات دیتابیس و `FLASK_SECRET_KEY` را تکمیل کنید. برنامه فایل `.env` را در صورت نصب python-dotenv می‌خواند.
7. اجرای استاندارد ویندوز: فایل `run.bat` را اجرا کنید. اجرای دستی: `python -m waitress --listen=0.0.0.0:3942 --threads=8 app:app`
8. بررسی سلامت سرویس: `http://SERVER:3942/healthz` (باید JSON با status=ok برگرداند).
9. آدرس برنامه: `http://SERVER:3942`

## مجوزهای SQL
- دیتابیس احراز هویت و IAM (`AUTH_DB_NAME=bi`): دسترسی SELECT به `[Project].[loginusers]` و جداول `[Project].[IAM_Projects]`، `[Project].[IAM_Roles]`، `[Project].[IAM_UserRoles]`، `[Project].[IAM_Permissions]` و `[Project].[IAM_RolePermissions]`.
- دیتابیس درخواست خرید: دسترسی SELECT/INSERT/UPDATE به جداول schema `Project`.
- حساب برنامه نباید دسترسی DDL داشته باشد؛ اسکریپت ساخت جداول را مدیر دیتابیس اجرا کند.

## تنظیم گردش تأیید
پس از ساخت schema، برای هر نوع درخواست و هر مرحله یک ردیف در `Project.approval_routes` اضافه کنید. مقدار `request_type` یکی از `goods`، `service`، `equipment` یا `other` است. `approver_username` باید دقیقاً با نام کاربری جدول سازمانی مطابقت داشته باشد. ترتیب مراحل با `step_no` مشخص می‌شود.

نمونه:
```sql
INSERT INTO Project.approval_routes(request_type, step_no, approver_username, approver_title, is_active)
VALUES ('goods', 1, N'username1', N'مدیر واحد', 1),
       ('goods', 2, N'username2', N'مدیر بازرگانی', 1);
```

## نکات نسخه اول
- ساختار دقیق کدینگ کالا، واحدهای سازمانی، فهرست تأمین‌کنندگان و امضای دیجیتال هنوز از دیتابیس ERP دریافت نشده است؛ بنابراین ورودی‌های مرتبط فعلاً قابل ورود دستی هستند.
- امضای تصویری داخل فرم مرجع در نسخه اول به‌صورت جایگاه تأیید و مشخصات تأییدکننده نمایش داده می‌شود؛ امضای دیجیتال واقعی نیازمند سیاست و منبع امضای سازمانی است.
- برای بهره‌برداری واقعی، حساب‌های SQL را با حداقل دسترسی بسازید و رمزهای دیتابیس را فقط در Environment Variables نگه دارید.


## کنترل دسترسی متمرکز IAM

- احراز هویت از جدول سازمانی `Project.loginusers` و خواندن نقش‌ها و مجوزها از جداول `Project.IAM_*` در دیتابیس `bi` انجام می‌شود.
- برنامه از طریق SQL مستقیم به IAM متصل می‌شود؛ هیچ API یا توکن بین برنامه‌ها وجود ندارد.
- دسترسی‌های `requests.create`، `requests.view_own`، `requests.view_assigned`، `requests.view_all`، `requests.approve.step1` تا `requests.approve.step3`، `requests.assign_specialist`، `requests.reassign_specialist` و `requests.export` در سمت سرور کنترل می‌شوند.
- دامنه مشاهده رکوردها نیز با شرط SQL بر اساس مجوزهای `view_own`، `view_assigned` و `view_all` محدود می‌شود.
- در صورت عدم دسترسی به IAM یا خطا در خواندن مجوزها، برنامه دسترسی را رد می‌کند. تغییر نقش و مجوز در IAM بدون تغییر کد این برنامه اعمال می‌شود.
