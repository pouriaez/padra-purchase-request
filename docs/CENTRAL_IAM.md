# Central IAM (RBAC)

This repository contains the first additive foundation for a shared Identity and Access Management layer in the existing `BI` database.

## Architecture decisions

- Existing accounts remain in `BI.Project.loginusers`; this migration does not alter that table or current login behavior.
- Projects, modules, permissions, roles, role-permission grants, user-role assignments, and IAM audit events live in `BI.Project.IAM_*`.
- Roles may be global (`project_id IS NULL)) or scoped to one project.
- User-role assignments may be global or project-scoped and may have a validity window.
- Authorization is checked server-side. Missing grants and IAM database errors must never grant access.
- This phase implements RBAC only. It deliberately does not add direct per-user permissions or attribute-based policies.

## Deployment

1. Back up database `BI`.
2. Run `sql/create_iam_schema.sql` in database `BI`. It is additive and safe to re-run.
3. Verify the new tables and the seeded `iam_superadmin` role.
4. Choose the trusted central administrator account and execute the explicit assignment statement at the bottom of the SQL script after replacing the placeholder. Do not assign it to an ordinary account.
5. Pull the repository and keep the current application behavior until the IAM admin UI and project-by-project enforcement are deployed and tested.

## Python helper

`iam.py` provides `has_permission(cursor, username, project_code, permission_code)` and a Flask `require_permission(connection_factory, project_code, permission_code)` decorator. The caller supplies the existing DB connection factory. It fails closed if the IAM lookup fails.

Example permission naming convention: `padra-purchase-request.requests.create`.

## Important password compatibility note

The current shared login table is consumed by multiple existing applications. This migration intentionally does not change `password_hash` values or login verification. A password-hash migration must be coordinated across every connected application; changing only one application would break logins in the others.
