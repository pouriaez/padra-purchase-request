USE bi;
GO

SET NOCOUNT ON;
SET XACT_ABORT ON;

IF OBJECT_ID(N'Project.requests', N'U') IS NULL
    THROW 51401, 'Project.requests is missing.', 1;
IF OBJECT_ID(N'Project.request_items', N'U') IS NULL
    THROW 51402, 'Project.request_items is missing.', 1;
IF OBJECT_ID(N'Project.approvals', N'U') IS NULL
    THROW 51403, 'Project.approvals is missing.', 1;
GO

IF OBJECT_ID(N'Project.request_item_assignments', N'U') IS NULL
BEGIN
    CREATE TABLE Project.request_item_assignments
    (
        id BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT PK_request_item_assignments PRIMARY KEY,
        request_id BIGINT NOT NULL,
        request_item_id BIGINT NOT NULL,
        specialist_username NVARCHAR(256) NOT NULL,
        assignment_status VARCHAR(20) NOT NULL CONSTRAINT DF_request_item_assignments_status DEFAULT ('assigned'),
        assigned_by NVARCHAR(256) NOT NULL,
        assigned_at DATETIME2(3) NOT NULL CONSTRAINT DF_request_item_assignments_assigned_at DEFAULT (SYSUTCDATETIME()),
        completed_by NVARCHAR(256) NULL,
        completed_at DATETIME2(3) NULL,
        completion_comment NVARCHAR(2000) NULL,
        CONSTRAINT FK_request_item_assignments_requests FOREIGN KEY (request_id)
            REFERENCES Project.requests(id) ON DELETE CASCADE,
        CONSTRAINT FK_request_item_assignments_items FOREIGN KEY (request_item_id)
            REFERENCES Project.request_items(id) ON DELETE CASCADE,
        CONSTRAINT UQ_request_item_assignments_item UNIQUE (request_item_id),
        CONSTRAINT CK_request_item_assignments_status CHECK (assignment_status IN ('assigned','completed','rejected'))
    );
END;
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'Project.request_item_assignments') AND name=N'IX_request_item_assignments_specialist_status')
    CREATE INDEX IX_request_item_assignments_specialist_status
        ON Project.request_item_assignments(specialist_username, assignment_status, request_id)
        INCLUDE (request_item_id, assigned_at);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE object_id=OBJECT_ID(N'Project.request_item_assignments') AND name=N'IX_request_item_assignments_request')
    CREATE INDEX IX_request_item_assignments_request
        ON Project.request_item_assignments(request_id, assignment_status)
        INCLUDE (request_item_id, specialist_username);
GO

/* Keep existing in-progress and completed specialist work visible after migration. */
INSERT Project.request_item_assignments
    (request_id, request_item_id, specialist_username, assignment_status,
     assigned_by, assigned_at, completed_by, completed_at, completion_comment)
SELECT
    r.id,
    i.id,
    a.approver_username,
    CASE WHEN a.decision='approved' THEN 'completed' ELSE 'assigned' END,
    N'SYSTEM-MIGRATION',
    COALESCE(a.decided_at, r.updated_at, r.created_at, SYSUTCDATETIME()),
    CASE WHEN a.decision='approved' THEN a.approver_username END,
    CASE WHEN a.decision='approved' THEN a.decided_at END,
    CASE WHEN a.decision='approved' THEN N'انتقال سوابق تکمیل‌شده از گردش کار قبلی' END
FROM Project.requests r
INNER JOIN Project.approvals a
    ON a.request_id=r.id
   AND a.step_no=3
   AND a.approver_username<>N'UNASSIGNED_SPECIALIST'
   AND a.decision IN ('pending','approved')
INNER JOIN Project.request_items i ON i.request_id=r.id
WHERE NOT EXISTS (
    SELECT 1 FROM Project.request_item_assignments x WHERE x.request_item_id=i.id
);
GO

CREATE OR ALTER TRIGGER Project.trg_audit_request_item_assignments
ON Project.request_item_assignments
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
        N'request_item_assignments',
        CONVERT(NVARCHAR(128),COALESCE(i.id,d.id)),
        3,
        CASE WHEN d.id IS NOT NULL THEN (SELECT d.* FOR JSON PATH, WITHOUT_ARRAY_WRAPPER) END,
        CASE WHEN i.id IS NOT NULL THEN (SELECT i.* FOR JSON PATH, WITHOUT_ARRAY_WRAPPER) END
    FROM inserted i
    FULL OUTER JOIN deleted d ON d.id=i.id
    WHERE d.id IS NULL OR i.id IS NULL
       OR ISNULL((SELECT i.* FOR JSON PATH, WITHOUT_ARRAY_WRAPPER),N'')
          <> ISNULL((SELECT d.* FOR JSON PATH, WITHOUT_ARRAY_WRAPPER),N'');
END;
GO

PRINT 'Per-item specialist assignment is installed. Existing step-3 assignments were migrated without deleting or rewriting request data.';
