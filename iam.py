"""Reusable, fail-closed RBAC authorization helpers for the central IAM database.

The caller owns the database connection/cursor. No Flask dependency is required.
"""
from functools import wraps
try:
    from flask import abort, current_app
    from flask_login import current_user
except ImportError:
    abort = None
    current_app = None
    current_user = None


def has_permission(cursor, username, project_code, permission_code):
    """Return True only for an active, currently valid RBAC grant."""
    if not username or not project_code or not permission_code:
        return False
    cursor.execute(
        """
        SELECT TOP (1) 1
        FROM Project.IAM_UserRoles ur
        INNER JOIN Project.IAM_Roles r
            ON r.id = ur.role_id AND r.is_active = 1
        INNER JOIN Project.IAM_RolePermissions rp
            ON rp.role_id = r.id
        INNER JOIN Project.IAM_Permissions p
            ON p.id = rp.permission_id AND p.is_active = 1
        INNER JOIN Project.IAM_Modules m
            ON m.id = p.module_id AND m.is_active = 1
        INNER JOIN Project.IAM_Projects pr
            ON pr.id = m.project_id AND pr.is_active = 1
        WHERE ur.username = ?
          AND ur.is_active = 1
          AND ur.valid_from <= SYSDATETIME()
          AND (ur.valid_until IS NULL OR ur.valid_until > SYSDATETIME())
          AND (ur.project_id IS NULL OR ur.project_id = pr.id)
          AND (r.project_id IS NULL OR r.project_id = pr.id)
          AND pr.project_code = ?
          AND p.permission_code = ?
        """,
        username, project_code, permission_code,
    )
    return cursor.fetchone() is not None


def require_permission(connection_factory, project_code, permission_code):
    """Flask decorator. Denies access if IAM is unavailable (fail closed)."""
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if current_user is None or not current_user.is_authenticated:
                if abort:
                    abort(401)
                raise PermissionError("Unauthorized")
            conn = cur = None
            try:
                conn = connection_factory()
                cur = conn.cursor()
                allowed = has_permission(
                    cur, current_user.username, project_code, permission_code
                )
            except Exception:
                if current_app:
                    current_app.logger.exception(
                        "IAM authorization lookup failed: %s / %s",
                        project_code, permission_code,
                    )
                if abort:
                    abort(503)
                raise RuntimeError("IAM service unavailable")
            finally:
                if cur:
                    cur.close()
                if conn:
                    conn.close()
            if not allowed:
                if abort:
                    abort(403)
                raise PermissionError("Forbidden")
            return view(*args, **kwargs)
        return wrapped
    return decorator
