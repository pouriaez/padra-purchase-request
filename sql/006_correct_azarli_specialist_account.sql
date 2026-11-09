USE bi;
GO

SET NOCOUNT ON;
SET XACT_ABORT ON;

BEGIN TRY
    BEGIN TRANSACTION;

    -- Correct the mistaken specialist assignment on Padra request 6.
    UPDATE Project.approvals
    SET approver_username = N'a.azarli',
        approver_title = N'کارشناس خرید داخلی - آرزو آذرلی'
    WHERE request_id = 6
      AND step_no = 3
      AND approver_username = N'a.zafari'
      AND decision IN ('pending', 'waiting');

    -- Ensure the correct account has the Serkan purchasing specialist role.
    IF EXISTS (SELECT 1 FROM Project.loginusers WHERE username = N'a.azarli')
       AND EXISTS (SELECT 1 FROM Project.IAM_Roles WHERE role_key = 'serkan_purchase_specialist')
       AND NOT EXISTS (
           SELECT 1
           FROM Project.IAM_UserRoles ur
           INNER JOIN Project.loginusers u ON u.id = ur.user_id
           INNER JOIN Project.IAM_Roles r ON r.id = ur.role_id
           WHERE u.username = N'a.azarli'
             AND r.role_key = 'serkan_purchase_specialist'
       )
    BEGIN
        INSERT Project.IAM_UserRoles(user_id, role_id, assigned_by)
        SELECT u.id, r.id, NULL
        FROM Project.loginusers u
        CROSS JOIN Project.IAM_Roles r
        WHERE u.username = N'a.azarli'
          AND r.role_key = 'serkan_purchase_specialist';
    END;

    -- Do not offer the incorrect account as a purchasing specialist.
    DELETE ur
    FROM Project.IAM_UserRoles ur
    INNER JOIN Project.loginusers u ON u.id = ur.user_id
    INNER JOIN Project.IAM_Roles r ON r.id = ur.role_id
    WHERE u.username = N'a.zafari'
      AND r.project_id = (
          SELECT id FROM Project.IAM_Projects
          WHERE project_key = 'padra-purchase-request'
      )
      AND r.role_key = 'serkan_purchase_specialist';

    INSERT Project.request_history(request_id, action_name, actor_username, details)
    SELECT
        6,
        N'اصلاح تخصیص کارشناس خرید',
        N'SYSTEM',
        N'حساب کاربری کارشناس از a.zafari به a.azarli (آرزو آذرلی) اصلاح شد.'
    WHERE EXISTS (SELECT 1 FROM Project.requests WHERE id = 6)
      AND NOT EXISTS (
          SELECT 1 FROM Project.request_history
          WHERE request_id = 6
            AND action_name = N'اصلاح تخصیص کارشناس خرید'
            AND details = N'حساب کاربری کارشناس از a.zafari به a.azarli (آرزو آذرلی) اصلاح شد.'
      );

    COMMIT TRANSACTION;
    PRINT 'Specialist account corrected to a.azarli.';
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
