USE bi;
GO

SET NOCOUNT ON;
SET XACT_ABORT ON;

IF OBJECT_ID(N'Project.requests', N'U') IS NULL
    THROW 51301, 'Project.requests is missing. Install the purchase-request schema first.', 1;
IF OBJECT_ID(N'Project.request_items', N'U') IS NULL
    THROW 51302, 'Project.request_items is missing. Install the purchase-request schema first.', 1;
IF OBJECT_ID(N'Project.approvals', N'U') IS NULL
    THROW 51303, 'Project.approvals is missing. Install the purchase-request schema first.', 1;
IF OBJECT_ID(N'Project.request_supplier_offers', N'U') IS NULL
    THROW 51304, 'Project.request_supplier_offers is missing. Run supplier-offer migrations 007 and 008 first.', 1;
IF OBJECT_ID(N'Project.request_deliveries', N'U') IS NULL
    THROW 51305, 'Project.request_deliveries is missing. Run migration 008 first.', 1;
GO

IF OBJECT_ID(N'Project.request_audit_log', N'U') IS NULL
BEGIN
    CREATE TABLE Project.request_audit_log
    (
        id BIGINT IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_request_audit_log PRIMARY KEY,
        request_id BIGINT NOT NULL,
        actor_username NVARCHAR(256) NOT NULL,
        action_type VARCHAR(10) NOT NULL,
        entity_name NVARCHAR(128) NOT NULL,
        record_id NVARCHAR(128) NOT NULL,
        stage_no INT NULL,
        old_values NVARCHAR(MAX) NULL,
        new_values NVARCHAR(MAX) NULL,
        created_at DATETIME2(3) NOT NULL
            CONSTRAINT DF_request_audit_log_created_at DEFAULT (SYSUTCDATETIME())
    );
END;
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'Project.request_audit_log') AND name=N'IX_request_audit_log_request_time')
    CREATE INDEX IX_request_audit_log_request_time
        ON Project.request_audit_log(request_id, created_at DESC, id DESC)
        INCLUDE (actor_username, action_type, entity_name, record_id, stage_no);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'Project.request_audit_log') AND name=N'IX_request_audit_log_actor_time')
    CREATE INDEX IX_request_audit_log_actor_time
        ON Project.request_audit_log(actor_username, created_at DESC)
        INCLUDE (request_id, action_type, entity_name);
GO

CREATE OR ALTER TRIGGER Project.trg_audit_requests
ON Project.requests
AFTER INSERT, UPDATE, DELETE
AS
BEGIN
    SET NOCOUNT ON;
    INSERT Project.request_audit_log
        (request_id, actor_username, action_type, entity_name, record_id, stage_no, old_values, new_values)
    SELECT
        COALESCE(i.id,d.id),
        COALESCE(NULLIF(CONVERT(NVARCHAR(256),SESSION_CONTEXT(N'actor_username')),N''),ORIGINAL_LOGIN()),
        CASE WHEN d.id IS NULL THEN 'INSERT' WHEN i.id IS NULL THEN 'DELETE' ELSE 'UPDATE' END,
        N'requests',
        CONVERT(NVARCHAR(128),COALESCE(i.id,d.id)),
        COALESCE((SELECT TOP (1) a.step_no FROM Project.approvals a WHERE a.request_id=COALESCE(i.id,d.id) AND a.decision='pending' ORDER BY a.step_no),(SELECT MAX(a.step_no) FROM Project.approvals a WHERE a.request_id=COALESCE(i.id,d.id))),
        CASE WHEN d.id IS NOT NULL THEN (SELECT d.* FOR JSON PATH, WITHOUT_ARRAY_WRAPPER) END,
        CASE WHEN i.id IS NOT NULL THEN (SELECT i.* FOR JSON PATH, WITHOUT_ARRAY_WRAPPER) END
    FROM inserted i
    FULL OUTER JOIN deleted d ON d.id=i.id
    WHERE d.id IS NULL OR i.id IS NULL
       OR ISNULL((SELECT i.* FOR JSON PATH, WITHOUT_ARRAY_WRAPPER),N'')
          <> ISNULL((SELECT d.* FOR JSON PATH, WITHOUT_ARRAY_WRAPPER),N'');
END;
GO

CREATE OR ALTER TRIGGER Project.trg_audit_request_items
ON Project.request_items
AFTER INSERT, UPDATE, DELETE
AS
BEGIN
    SET NOCOUNT ON;
    INSERT Project.request_audit_log
        (request_id, actor_username, action_type, entity_name, record_id, stage_no, old_values, new_values)
    SELECT
        COALESCE(i.request_id,d.request_id),
        COALESCE(NULLIF(CONVERT(NVARCHAR(256),SESSION_CONTEXT(N'actor_username')),N''),ORIGINAL_LOGIN()),
        CASE WHEN d.id IS NULL THEN 'INSERT' WHEN i.id IS NULL THEN 'DELETE' ELSE 'UPDATE' END,
        N'request_items',
        CONVERT(NVARCHAR(128),COALESCE(i.id,d.id)),
        COALESCE((SELECT TOP (1) a.step_no FROM Project.approvals a WHERE a.request_id=COALESCE(i.request_id,d.request_id) AND a.decision='pending' ORDER BY a.step_no),(SELECT MAX(a.step_no) FROM Project.approvals a WHERE a.request_id=COALESCE(i.request_id,d.request_id))),
        CASE WHEN d.id IS NOT NULL THEN (SELECT d.* FOR JSON PATH, WITHOUT_ARRAY_WRAPPER) END,
        CASE WHEN i.id IS NOT NULL THEN (SELECT i.* FOR JSON PATH, WITHOUT_ARRAY_WRAPPER) END
    FROM inserted i
    FULL OUTER JOIN deleted d ON d.id=i.id
    WHERE d.id IS NULL OR i.id IS NULL
       OR ISNULL((SELECT i.* FOR JSON PATH, WITHOUT_ARRAY_WRAPPER),N'')
          <> ISNULL((SELECT d.* FOR JSON PATH, WITHOUT_ARRAY_WRAPPER),N'');
END;
GO

CREATE OR ALTER TRIGGER Project.trg_audit_approvals
ON Project.approvals
AFTER INSERT, UPDATE, DELETE
AS
BEGIN
    SET NOCOUNT ON;
    INSERT Project.request_audit_log
        (request_id, actor_username, action_type, entity_name, record_id, stage_no, old_values, new_values)
    SELECT
        COALESCE(i.request_id,d.request_id),
        COALESCE(NULLIF(CONVERT(NVARCHAR(256),SESSION_CONTEXT(N'actor_username')),N''),ORIGINAL_LOGIN()),
        CASE WHEN d.id IS NULL THEN 'INSERT' WHEN i.id IS NULL THEN 'DELETE' ELSE 'UPDATE' END,
        N'approvals',
        CONVERT(NVARCHAR(128),COALESCE(i.id,d.id)),
        COALESCE(i.step_no,d.step_no),
        CASE WHEN d.id IS NOT NULL THEN (SELECT d.* FOR JSON PATH, WITHOUT_ARRAY_WRAPPER) END,
        CASE WHEN i.id IS NOT NULL THEN (SELECT i.* FOR JSON PATH, WITHOUT_ARRAY_WRAPPER) END
    FROM inserted i
    FULL OUTER JOIN deleted d ON d.id=i.id
    WHERE d.id IS NULL OR i.id IS NULL
       OR ISNULL((SELECT i.* FOR JSON PATH, WITHOUT_ARRAY_WRAPPER),N'')
          <> ISNULL((SELECT d.* FOR JSON PATH, WITHOUT_ARRAY_WRAPPER),N'');
END;
GO

CREATE OR ALTER TRIGGER Project.trg_audit_request_supplier_offers
ON Project.request_supplier_offers
AFTER INSERT, UPDATE, DELETE
AS
BEGIN
    SET NOCOUNT ON;
    INSERT Project.request_audit_log
        (request_id, actor_username, action_type, entity_name, record_id, stage_no, old_values, new_values)
    SELECT
        COALESCE(i.request_id,d.request_id),
        COALESCE(NULLIF(CONVERT(NVARCHAR(256),SESSION_CONTEXT(N'actor_username')),N''),ORIGINAL_LOGIN()),
        CASE WHEN d.id IS NULL THEN 'INSERT' WHEN i.id IS NULL THEN 'DELETE' ELSE 'UPDATE' END,
        N'request_supplier_offers',
        CONVERT(NVARCHAR(128),COALESCE(i.id,d.id)),
        COALESCE((SELECT TOP (1) a.step_no FROM Project.approvals a WHERE a.request_id=COALESCE(i.request_id,d.request_id) AND a.decision='pending' ORDER BY a.step_no),(SELECT MAX(a.step_no) FROM Project.approvals a WHERE a.request_id=COALESCE(i.request_id,d.request_id))),
        CASE WHEN d.id IS NOT NULL THEN (SELECT d.* FOR JSON PATH, WITHOUT_ARRAY_WRAPPER) END,
        CASE WHEN i.id IS NOT NULL THEN (SELECT i.* FOR JSON PATH, WITHOUT_ARRAY_WRAPPER) END
    FROM inserted i
    FULL OUTER JOIN deleted d ON d.id=i.id
    WHERE d.id IS NULL OR i.id IS NULL
       OR ISNULL((SELECT i.* FOR JSON PATH, WITHOUT_ARRAY_WRAPPER),N'')
          <> ISNULL((SELECT d.* FOR JSON PATH, WITHOUT_ARRAY_WRAPPER),N'');
END;
GO

CREATE OR ALTER TRIGGER Project.trg_audit_request_deliveries
ON Project.request_deliveries
AFTER INSERT, UPDATE, DELETE
AS
BEGIN
    SET NOCOUNT ON;
    INSERT Project.request_audit_log
        (request_id, actor_username, action_type, entity_name, record_id, stage_no, old_values, new_values)
    SELECT
        COALESCE(i.request_id,d.request_id),
        COALESCE(NULLIF(CONVERT(NVARCHAR(256),SESSION_CONTEXT(N'actor_username')),N''),ORIGINAL_LOGIN()),
        CASE WHEN d.id IS NULL THEN 'INSERT' WHEN i.id IS NULL THEN 'DELETE' ELSE 'UPDATE' END,
        N'request_deliveries',
        CONVERT(NVARCHAR(128),COALESCE(i.id,d.id)),
        COALESCE((SELECT TOP (1) a.step_no FROM Project.approvals a WHERE a.request_id=COALESCE(i.request_id,d.request_id) AND a.decision='pending' ORDER BY a.step_no),(SELECT MAX(a.step_no) FROM Project.approvals a WHERE a.request_id=COALESCE(i.request_id,d.request_id))),
        CASE WHEN d.id IS NOT NULL THEN (SELECT d.* FOR JSON PATH, WITHOUT_ARRAY_WRAPPER) END,
        CASE WHEN i.id IS NOT NULL THEN (SELECT i.* FOR JSON PATH, WITHOUT_ARRAY_WRAPPER) END
    FROM inserted i
    FULL OUTER JOIN deleted d ON d.id=i.id
    WHERE d.id IS NULL OR i.id IS NULL
       OR ISNULL((SELECT i.* FOR JSON PATH, WITHOUT_ARRAY_WRAPPER),N'')
          <> ISNULL((SELECT d.* FOR JSON PATH, WITHOUT_ARRAY_WRAPPER),N'');
END;
GO

PRINT 'Request audit logging is installed for requests, items, approvals, supplier offers and delivery receipts.';
PRINT 'Each trigger stores one before/after JSON snapshot per changed row in the same transaction.';
