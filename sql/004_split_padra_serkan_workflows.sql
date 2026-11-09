SET NOCOUNT ON;
SET XACT_ABORT ON;

BEGIN TRY
    BEGIN TRANSACTION;

    IF COL_LENGTH(N'Project.requests', N'workflow_type') IS NULL
    BEGIN
        ALTER TABLE Project.requests
        ADD workflow_type VARCHAR(20) NOT NULL
            CONSTRAINT DF_purchase_requests_workflow_type DEFAULT ('serkan') WITH VALUES;
    END;

    IF NOT EXISTS (
        SELECT 1
        FROM sys.check_constraints
        WHERE parent_object_id = OBJECT_ID(N'Project.requests')
          AND name = N'CK_purchase_requests_workflow_type'
    )
    BEGIN
        ALTER TABLE Project.requests
        ADD CONSTRAINT CK_purchase_requests_workflow_type
            CHECK (workflow_type IN ('padra', 'serkan'));
    END;

    UPDATE Project.requests
    SET workflow_type = 'padra'
    WHERE requester_username = N'karid_padra'
      AND workflow_type <> 'padra';

    -- Pending requests previously created by the Padra purchasing specialist
    -- are moved to the single-step approval queue of the Padra CEO.
    DELETE a
    FROM Project.approvals a
    INNER JOIN Project.requests r ON r.id = a.request_id
    WHERE r.requester_username = N'karid_padra'
      AND r.workflow_type = 'padra'
      AND r.status = 'pending'
      AND a.step_no > 1;

    UPDATE a
    SET approver_username = N'i.heydari',
        approver_title = N'مدیرعامل شرکت پادرا',
        decision = 'pending',
        comment = NULL,
        decided_at = NULL
    FROM Project.approvals a
    INNER JOIN Project.requests r ON r.id = a.request_id
    WHERE r.requester_username = N'karid_padra'
      AND r.workflow_type = 'padra'
      AND r.status = 'pending'
      AND a.step_no = 1;

    INSERT Project.approvals
        (request_id, step_no, approver_username, approver_title, decision)
    SELECT r.id, 1, N'i.heydari', N'مدیرعامل شرکت پادرا', 'pending'
    FROM Project.requests r
    WHERE r.requester_username = N'karid_padra'
      AND r.workflow_type = 'padra'
      AND r.status = 'pending'
      AND NOT EXISTS (
          SELECT 1
          FROM Project.approvals a
          WHERE a.request_id = r.id AND a.step_no = 1
      );

    IF NOT EXISTS (
        SELECT 1
        FROM sys.indexes
        WHERE object_id = OBJECT_ID(N'Project.requests')
          AND name = N'IX_purchase_requests_workflow_status'
    )
    BEGIN
        CREATE INDEX IX_purchase_requests_workflow_status
            ON Project.requests(workflow_type, status, created_at DESC);
    END;

    COMMIT TRANSACTION;
    PRINT 'Padra and Serkan request workflows have been separated.';
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
