SET NOCOUNT ON;

IF OBJECT_ID(N'Project.approval_routes', N'U') IS NULL
    THROW 51100, 'Project.approval_routes is missing. Run create_purchase_request_schema.sql first.', 1;

-- IAM maps admin to the Padra CEO role and m.mahdiyon to the Serkan purchasing manager role.
UPDATE Project.approval_routes
SET approver_username = N'admin',
    approver_title = N'مدیرعامل شرکت پادرا'
WHERE step_no = 1
  AND approver_username IN (N'ceo_padra', N'admin');

UPDATE Project.approval_routes
SET approver_username = N'm.mahdiyon',
    approver_title = N'مدیر خرید داخلی گروه صنعتی سرکان'
WHERE step_no = 2
  AND approver_username IN (N'serkan_purchase_mgr', N'm.mahdiyon');

-- Move only still-open approval tasks; completed audit history remains unchanged.
UPDATE Project.approvals
SET approver_username = N'admin',
    approver_title = N'مدیرعامل شرکت پادرا'
WHERE step_no = 1
  AND decision IN ('pending', 'waiting')
  AND approver_username = N'ceo_padra';

UPDATE Project.approvals
SET approver_username = N'm.mahdiyon',
    approver_title = N'مدیر خرید داخلی گروه صنعتی سرکان'
WHERE step_no = 2
  AND decision IN ('pending', 'waiting')
  AND approver_username = N'serkan_purchase_mgr';

PRINT 'Purchase workflow approvers aligned with central IAM user assignments.';
