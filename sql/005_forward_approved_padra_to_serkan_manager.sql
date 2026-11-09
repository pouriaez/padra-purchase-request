USE bi;
GO

SET NOCOUNT ON;
SET XACT_ABORT ON;

BEGIN TRY
    BEGIN TRANSACTION;

    /*
       Requests from Padra that were approved by the Padra CEO before this
       workflow correction must continue to Serkan purchasing.
       Stage 1 remains approved; stages 2 and 3 are created below.
    */

    INSERT INTO Project.approvals
        (request_id, step_no, approver_username, approver_title, decision)
    SELECT
        r.id,
        2,
        N'm.mahdiyon',
        N'مدیر خرید داخلی گروه صنعتی سرکان',
        'pending'
    FROM Project.requests r
    INNER JOIN Project.approvals ceo
        ON ceo.request_id = r.id
       AND ceo.step_no = 1
       AND ceo.approver_username = N'i.heydari'
       AND ceo.decision = 'approved'
    WHERE r.workflow_type = 'padra'
      AND r.status = 'approved'
      AND NOT EXISTS (
          SELECT 1
          FROM Project.approvals a
          WHERE a.request_id = r.id
            AND a.step_no = 2
      );

    INSERT INTO Project.approvals
        (request_id, step_no, approver_username, approver_title, decision)
    SELECT
        r.id,
        3,
        N'UNASSIGNED_SPECIALIST',
        N'کارشناس خرید گروه صنعتی سرکان',
        'waiting'
    FROM Project.requests r
    INNER JOIN Project.approvals manager_step
        ON manager_step.request_id = r.id
       AND manager_step.step_no = 2
    WHERE r.workflow_type = 'padra'
      AND manager_step.approver_username = N'm.mahdiyon'
      AND manager_step.decision IN ('pending', 'waiting')
      AND NOT EXISTS (
          SELECT 1
          FROM Project.approvals a
          WHERE a.request_id = r.id
            AND a.step_no = 3
      );

    UPDATE r
    SET status = 'pending',
        updated_at = GETDATE()
    FROM Project.requests r
    WHERE r.workflow_type = 'padra'
      AND r.status = 'approved'
      AND EXISTS (
          SELECT 1
          FROM Project.approvals a
          WHERE a.request_id = r.id
            AND a.step_no = 2
            AND a.approver_username = N'm.mahdiyon'
            AND a.decision = 'pending'
      );

    INSERT INTO Project.request_history
        (request_id, action_name, actor_username, details)
    SELECT
        r.id,
        N'ارجاع درخواست تأییدشده پادرا به مدیر خرید داخلی سرکان',
        N'SYSTEM',
        N'به‌دلیل اصلاح گردش کار، درخواست تأییدشده مدیرعامل پادرا برای تخصیص کارشناس خرید به مدیر خرید داخلی سرکان ارجاع شد.'
    FROM Project.requests r
    WHERE r.workflow_type = 'padra'
      AND EXISTS (
          SELECT 1
          FROM Project.approvals a
          WHERE a.request_id = r.id
            AND a.step_no = 2
            AND a.approver_username = N'm.mahdiyon'
            AND a.decision = 'pending'
      )
      AND NOT EXISTS (
          SELECT 1
          FROM Project.request_history h
          WHERE h.request_id = r.id
            AND h.action_name = N'ارجاع درخواست تأییدشده پادرا به مدیر خرید داخلی سرکان'
      );

    COMMIT TRANSACTION;
    PRINT 'Approved Padra requests were forwarded to the Serkan purchasing manager.';
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
