IF SCHEMA_ID(N'purchase') IS NULL EXEC(N'CREATE SCHEMA purchase');
GO
IF OBJECT_ID(N'purchase.requests', N'U') IS NULL
BEGIN
 CREATE TABLE purchase.requests(
  id BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT PK_purchase_requests PRIMARY KEY,
  request_no NVARCHAR(40) NULL,
  request_date DATE NOT NULL,
  request_date_fa NVARCHAR(10) NOT NULL,
  request_type VARCHAR(20) NOT NULL,
  unit_name NVARCHAR(150) NOT NULL,
  supply_location NVARCHAR(150) NOT NULL,
  fixed_asset BIT NOT NULL CONSTRAINT DF_purchase_fixed_asset DEFAULT(0),
  request_reason NVARCHAR(1000) NULL,
  requester_username NVARCHAR(100) NOT NULL,
  status VARCHAR(20) NOT NULL CONSTRAINT DF_purchase_status DEFAULT('pending'),
  created_at DATETIME2 NOT NULL CONSTRAINT DF_purchase_created DEFAULT(GETDATE()),
  updated_at DATETIME2 NOT NULL CONSTRAINT DF_purchase_updated DEFAULT(GETDATE())
 );
 CREATE UNIQUE INDEX UX_purchase_requests_request_no ON purchase.requests(request_no) WHERE request_no IS NOT NULL;
 CREATE INDEX IX_purchase_requests_requester ON purchase.requests(requester_username, created_at DESC);
 CREATE INDEX IX_purchase_requests_status ON purchase.requests(status, created_at DESC);
END;
GO
IF OBJECT_ID(N'purchase.request_items', N'U') IS NULL
BEGIN
 CREATE TABLE purchase.request_items(
  id BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT PK_purchase_request_items PRIMARY KEY,
  request_id BIGINT NOT NULL,
  line_no INT NOT NULL,
  item_code NVARCHAR(80) NULL,
  item_description NVARCHAR(500) NOT NULL,
  unit_name NVARCHAR(50) NOT NULL,
  quantity DECIMAL(18,3) NOT NULL,
  needed_date_fa NVARCHAR(10) NOT NULL,
  usage_location NVARCHAR(150) NOT NULL,
  CONSTRAINT FK_purchase_items_request FOREIGN KEY(request_id) REFERENCES purchase.requests(id) ON DELETE CASCADE,
  CONSTRAINT CK_purchase_item_quantity CHECK(quantity > 0)
 );
 CREATE INDEX IX_purchase_items_request ON purchase.request_items(request_id, line_no);
END;
GO
IF OBJECT_ID(N'purchase.approval_routes', N'U') IS NULL
BEGIN
 CREATE TABLE purchase.approval_routes(
  id INT IDENTITY(1,1) NOT NULL CONSTRAINT PK_purchase_approval_routes PRIMARY KEY,
  request_type VARCHAR(20) NOT NULL,
  step_no INT NOT NULL,
  approver_username NVARCHAR(100) NOT NULL,
  approver_title NVARCHAR(150) NOT NULL,
  is_active BIT NOT NULL CONSTRAINT DF_purchase_route_active DEFAULT(1),
  CONSTRAINT UQ_purchase_route UNIQUE(request_type, step_no)
 );
END;
GO
IF OBJECT_ID(N'purchase.approvals', N'U') IS NULL
BEGIN
 CREATE TABLE purchase.approvals(
  id BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT PK_purchase_approvals PRIMARY KEY,
  request_id BIGINT NOT NULL,
  step_no INT NOT NULL,
  approver_username NVARCHAR(100) NOT NULL,
  approver_title NVARCHAR(150) NOT NULL,
  decision VARCHAR(20) NOT NULL CONSTRAINT DF_purchase_approval_decision DEFAULT('waiting'),
  comment NVARCHAR(1000) NULL,
  decided_at DATETIME2 NULL,
  CONSTRAINT FK_purchase_approval_request FOREIGN KEY(request_id) REFERENCES purchase.requests(id) ON DELETE CASCADE,
  CONSTRAINT UQ_purchase_approval_step UNIQUE(request_id, step_no)
 );
 CREATE INDEX IX_purchase_approvals_inbox ON purchase.approvals(approver_username, decision, request_id);
END;
GO
IF OBJECT_ID(N'purchase.request_history', N'U') IS NULL
BEGIN
 CREATE TABLE purchase.request_history(
  id BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT PK_purchase_request_history PRIMARY KEY,
  request_id BIGINT NOT NULL,
  action_name NVARCHAR(80) NOT NULL,
  actor_username NVARCHAR(100) NOT NULL,
  details NVARCHAR(1000) NULL,
  created_at DATETIME2 NOT NULL CONSTRAINT DF_purchase_history_created DEFAULT(GETDATE()),
  CONSTRAINT FK_purchase_history_request FOREIGN KEY(request_id) REFERENCES purchase.requests(id) ON DELETE CASCADE
 );
 CREATE INDEX IX_purchase_history_request ON purchase.request_history(request_id, created_at);
END;
GO
