/*
 Central IAM schema for BI database.
 Safe additive migration: does not alter loginusers or existing project tables.
 Run in database BI.
*/
SET XACT_ABORT ON;
BEGIN TRANSACTION;

IF SCHEMA_ID(N'Project') IS NULL EXEC(N'CREATE SCHEMA Project');

IF OBJECT_ID(N'Project.IAM_Projects', N'U') IS NULL
BEGIN
    CREATE TABLE Project.IAM_Projects(
        id INT IDENTITY(1,1) NOT NULL CONSTRAINT PK_IAM_Projects PRIMARY KEY,
        project_code NVARCHAR(80) NOT NULL,
        project_name NVARCHAR(150) NOT NULL,
        description NVARCHAR(500) NULL,
        base_url NVARCHAR(500) NULL,
        is_active BIT NOT NULL CONSTRAINT DF_IAM_Projects_active DEFAULT(1),
        created_at DATETIME2 NOT NULL CONSTRAINT DF_IAM_Projects_created DEFAULT(SYSDATETIME()),
        CONSTRAINT UQ_IAM_Projects_code UNIQUE(project_code)
    );
END;

IF OBJECT_ID(N'Project.IAM_Modules', N'U') IS NULL
BEGIN
    CREATE TABLE Project.IAM_Modules(
        id INT IDENTITY(1,1) NOT NULL CONSTRAINT PK_IAM_Modules PRIMARY KEY,
        project_id INT NOT NULL,
        module_code NVARCHAR(80) NOT NULL,
        module_name NVARCHAR(150) NOT NULL,
        description NVARCHAR(500) NULL,
        is_active BIT NOT NULL CONSTRAINT DF_IAM_Modules_active DEFAULT(1),
        created_at DATETIME2 NOT NULL CONSTRAINT DF_IAM_Modules_created DEFAULT(SYSDATETIME()),
        CONSTRAINT FK_IAM_Modules_Project FOREIGN KEY(project_id) REFERENCES Project.IAM_Projects(id),
        CONSTRAINT UQ_IAM_Modules_project_code UNIQUE(project_id,module_code)
    );
END;

IF OBJECT_ID(N'Project.IAM_Permissions', N'U') IS NULL
BEGIN
    CREATE TABLE Project.IAM_Permissions(
        id INT IDENTITY(1,1) NOT NULL CONSTRAINT PK_IAM_Permissions PRIMARY KEY,
        module_id INT NOT NULL,
        permission_code NVARCHAR(160) NOT NULL,
        permission_name NVARCHAR(150) NOT NULL,
        action_code NVARCHAR(60) NOT NULL,
        description NVARCHAR(500) NULL,
        is_active BIT NOT NULL CONSTRAINT DF_IAM_Permissions_active DEFAULT(1),
        created_at DATETIME2 NOT NULL CONSTRAINT DF_IAM_Permissions_created DEFAULT(SYSDATETIME()),
        CONSTRAINT FK_IAM_Permissions_Module FOREIGN KEY(module_id) REFERENCES Project.IAM_Modules(id),
        CONSTRAINT UQ_IAM_Permissions_code UNIQUE(permission_code),
        CONSTRAINT UQ_IAM_Permissions_module_action UNIQUE(module_id,action_code)
    );
END;

IF OBJECT_ID(N'Project.IAM_Roles', N'U') IS NULL
BEGIN
    CREATE TABLE Project.IAM_Roles(
        id INT IDENTITY(1,1) NOT NULL CONSTRAINT PK_IAM_Roles PRIMARY KEY,
        project_id INT NULL,
        role_code NVARCHAR(80) NOT NULL,
        role_name NVARCHAR(150) NOT NULL,
        description NVARCHAR(500) NULL,
        is_system BIT NOT NULL CONSTRAINT DF_IAM_Roles_system DEFAULT(0),
        is_active BIT NOT NULL CONSTRAINT DF_IAM_Roles_active DEFAULT(1),
        created_at DATETIME2 NOT NULL CONSTRAINT DF_IAM_Roles_created DEFAULT(SYSDATETIME()),
        CONSTRAINT FK_IAM_Roles_Project FOREIGN KEY(project_id) REFERENCES Project.IAM_Projects(id)
    );
    CREATE UNIQUE INDEX UX_IAM_Roles_global_code ON Project.IAM_Roles(role_code) WHERE project_id IS NULL;
    CREATE UNIQUE INDEX UX_IAM_Roles_project_code ON Project.IAM_Roles(project_id,role_code) WHERE project_id IS NOT NULL;
END;

IF OBJECT_ID(N'Project.IAM_RolePermissions', N'U') IS NULL
BEGIN
    CREATE TABLE Project.IAM_RolePermissions(
        role_id INT NOT NULL,
        permission_id INT NOT NULL,
        granted_at DATETIME2 NOT NULL CONSTRAINT DF_IAM_RolePermissions_granted DEFAULT(SYSDATETIME()),
        CONSTRAINT PK_IAM_RolePermissions PRIMARY KEY(role_id,permission_id),
        CONSTRAINT FK_IAM_RolePermissions_Role FOREIGN KEY(role_id) REFERENCES Project.IAM_Roles(id) ON DELETE CASCADE,
        CONSTRAINT FK_IAM_RolePermissions_Permission FOREIGN KEY(permission_id) REFERENCES Project.IAM_Permissions(id) ON DELETE CASCADE
    );
END;

IF OBJECT_ID(N'Project.IAM_UserRoles', N'U') IS NULL
BEGIN
    CREATE TABLE Project.IAM_UserRoles(
        id BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT PK_IAM_UserRoles PRIMARY KEY,
        username NVARCHAR(100) NOT NULL,
        role_id INT NOT NULL,
        project_id INT NULL,
        valid_from DATETIME2 NOT NULL CONSTRAINT DF_IAM_UserRoles_from DEFAULT(SYSDATETIME()),
        valid_until DATETIME2 NULL,
        is_active BIT NOT NULL CONSTRAINT DF_IAM_UserRoles_active DEFAULT(1),
        assigned_by NVARCHAR(100) NOT NULL,
        assigned_at DATETIME2 NOT NULL CONSTRAINT DF_IAM_UserRoles_assigned DEFAULT(SYSDATETIME()),
        CONSTRAINT FK_IAM_UserRoles_Role FOREIGN KEY(role_id) REFERENCES Project.IAM_Roles(id),
        CONSTRAINT FK_IAM_UserRoles_Project FOREIGN KEY(project_id) REFERENCES Project.IAM_Projects(id),
        CONSTRAINT CK_IAM_UserRoles_dates CHECK(valid_until IS NULL OR valid_until > valid_from)
    );
    CREATE INDEX IX_IAM_UserRoles_username ON Project.IAM_UserRoles(username,is_active,project_id);
    CREATE INDEX IX_IAM_UserRoles_role ON Project.IAM_UserRoles(role_id,is_active);
END;

IF OBJECT_ID(N'Project.IAM_AuditLog', N'U') IS NULL
BEGIN
    CREATE TABLE Project.IAM_AuditLog(
        id BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT PK_IAM_AuditLog PRIMARY KEY,
        actor_username NVARCHAR(100) NOT NULL,
        target_username NVARCHAR(100) NULL,
        project_id INT NULL,
        entity_type NVARCHAR(60) NOT NULL,
        entity_id NVARCHAR(100) NULL,
        action_code NVARCHAR(60) NOT NULL,
        details NVARCHAR(2000) NULL,
        ip_address NVARCHAR(64) NULL,
        user_agent NVARCHAR(500) NULL,
        created_at DATETIME2 NOT NULL CONSTRAINT DF_IAM_AuditLog_created DEFAULT(SYSDATETIME()),
        CONSTRAINT FK_IAM_AuditLog_Project FOREIGN KEY(project_id) REFERENCES Project.IAM_Projects(id)
    );
    CREATE INDEX IX_IAM_AuditLog_created ON Project.IAM_AuditLog(created_at DESC);
    CREATE INDEX IX_IAM_AuditLog_target ON Project.IAM_AuditLog(target_username,created_at DESC);
END;

/* Bootstrap the central IAM catalog and a protected system role.
   No user is automatically granted this role. Assign it only to a trusted administrator. */
DECLARE @project_id INT, @module_id INT, @permission_id INT, @role_id INT;

SELECT @project_id=id FROM Project.IAM_Projects WHERE project_code=N'iam';
IF @project_id IS NULL
BEGIN
    INSERT Project.IAM_Projects(project_code,project_name,description)
    VALUES(N'iam',N'مدیریت متمرکز دسترسی',N'پنل و هسته مرکزی مدیریت هویت و دسترسی');
    SET @project_id=SCOPE_IDENTITY();
END;

SELECT @module_id=id FROM Project.IAM_Modules WHERE project_id=@project_id AND module_code=N'administration';
IF @module_id IS NULL
BEGIN
    INSERT Project.IAM_Modules(project_id,module_code,module_name,description)
    VALUES(@project_id,N'administration',N'مدیریت مرکزی',N'مدیریت کاربران، پروژه‌ها، نقش‌ها و مجوزها');
    SET @module_id=SCOPE_IDENTITY();
END;

SELECT @permission_id=id FROM Project.IAM_Permissions WHERE permission_code=N'iam.admin.access';
IF @permission_id IS NULL
BEGIN
    INSERT Project.IAM_Permissions(module_id,permission_code,permission_name,action_code,description)
    VALUES(@module_id,N'iam.admin.access',N'ورود به مدیریت مرکزی',N'access',N'اجازه ورود به پنل مدیریت مرکزی IAM');
    SET @permission_id=SCOPE_IDENTITY();
END;

SELECT @role_id=id FROM Project.IAM_Roles WHERE project_id IS NULL AND role_code=N'iam_superadmin';
IF @role_id IS NULL
BEGIN
    INSERT Project.IAM_Roles(project_id,role_code,role_name,description,is_system)
    VALUES(NULL,N'iam_superadmin',N'مدیر ارشد IAM',N'مدیریت کامل کاتالوگ و دسترسی‌های تمام پروژه‌ها',1);
    SET @role_id=SCOPE_IDENTITY();
END;

/* Seed the first set of central administration permissions. */
DECLARE @admin_permissions TABLE(
    permission_code NVARCHAR(160),
    permission_name NVARCHAR(150),
    action_code NVARCHAR(60),
    description NVARCHAR(500)
);
INSERT @admin_permissions VALUES
(N'iam.admin.access',N'ورود به مدیریت مرکزی',N'access',N'اجازه ورود به پنل مدیریت مرکزی IAM'),
(N'iam.users.manage',N'مدیریت کاربران',N'manage_users',N'مشاهده و مدیریت وضعیت و نقش‌های کاربران'),
(N'iam.projects.manage',N'مدیریت پروژه‌ها',N'manage_projects',N'تعریف و مدیریت پروژه‌ها و ماژول‌ها'),
(N'iam.roles.manage',N'مدیریت نقش‌ها',N'manage_roles',N'ایجاد نقش و تخصیص مجوز به نقش'),
(N'iam.permissions.manage',N'مدیریت مجوزها',N'manage_permissions',N'تعریف و مدیریت مجوزهای پروژه‌ها'),
(N'iam.audit.view',N'مشاهده گزارش رویدادها',N'view_audit',N'مشاهده سوابق تغییرات و فعالیت‌های IAM');

INSERT Project.IAM_Permissions(module_id,permission_code,permission_name,action_code,description)
SELECT @module_id,s.permission_code,s.permission_name,s.action_code,s.description
FROM @admin_permissions s
WHERE NOT EXISTS(
    SELECT 1 FROM Project.IAM_Permissions p WHERE p.permission_code=s.permission_code
);

SELECT @role_id=id FROM Project.IAM_Roles WHERE project_id IS NULL AND role_code=N'iam_superadmin';
INSERT Project.IAM_RolePermissions(role_id,permission_id)
SELECT @role_id,p.id
FROM Project.IAM_Permissions p
WHERE p.permission_code LIKE N'iam.%'
  AND NOT EXISTS(
      SELECT 1 FROM Project.IAM_RolePermissions rp
      WHERE rp.role_id=@role_id AND rp.permission_id=p.id
  );

COMMIT TRANSACTION;
GO

/* After confirming the trusted administrator username, assign the role explicitly.
   Replace the placeholder; do not grant this role to ordinary users.
   INSERT Project.IAM_UserRoles(username,role_id,project_id,assigned_by)
   SELECT N'<ADMIN_USERNAME>',id,NULL,N'<ADMIN_USERNAME>'
   FROM Project.IAM_Roles
   WHERE project_id IS NULL AND role_code=N'iam_superadmin'
     AND NOT EXISTS (
       SELECT 1 FROM Project.IAM_UserRoles ur
       WHERE ur.username=N'<ADMIN_USERNAME>' AND ur.role_id=Project.IAM_Roles.id
         AND ur.project_id IS NULL AND ur.is_active=1
     );
*/
