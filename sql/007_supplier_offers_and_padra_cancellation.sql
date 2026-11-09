USE bi;
GO

SET NOCOUNT ON;
SET XACT_ABORT ON;

DECLARE @request_id_type NVARCHAR(300);
DECLARE @request_item_id_type NVARCHAR(300);
DECLARE @sql NVARCHAR(MAX);

-- Match the foreign-key column types exactly to the existing parent columns.
SELECT @request_id_type =
    CASE
        WHEN t.is_user_defined = 1
            THEN QUOTENAME(SCHEMA_NAME(t.schema_id)) + N'.' + QUOTENAME(t.name)
        WHEN t.name IN (N'nvarchar', N'nchar')
            THEN t.name + N'(' + CASE WHEN c.max_length = -1 THEN N'MAX'
                                      ELSE CONVERT(NVARCHAR(10), c.max_length / 2) END + N')'
        WHEN t.name IN (N'varchar', N'char', N'varbinary', N'binary')
            THEN t.name + N'(' + CASE WHEN c.max_length = -1 THEN N'MAX'
                                      ELSE CONVERT(NVARCHAR(10), c.max_length) END + N')'
        WHEN t.name IN (N'decimal', N'numeric')
            THEN t.name + N'(' + CONVERT(NVARCHAR(10), c.precision) + N',' +
                 CONVERT(NVARCHAR(10), c.scale) + N')'
        WHEN t.name IN (N'datetime2', N'datetimeoffset', N'time')
            THEN t.name + N'(' + CONVERT(NVARCHAR(10), c.scale) + N')'
        ELSE t.name
    END
FROM sys.columns c
JOIN sys.types t ON t.user_type_id = c.user_type_id
WHERE c.object_id = OBJECT_ID(N'Project.requests')
  AND c.name = N'id';

SELECT @request_item_id_type =
    CASE
        WHEN t.is_user_defined = 1
            THEN QUOTENAME(SCHEMA_NAME(t.schema_id)) + N'.' + QUOTENAME(t.name)
        WHEN t.name IN (N'nvarchar', N'nchar')
            THEN t.name + N'(' + CASE WHEN c.max_length = -1 THEN N'MAX'
                                      ELSE CONVERT(NVARCHAR(10), c.max_length / 2) END + N')'
        WHEN t.name IN (N'varchar', N'char', N'varbinary', N'binary')
            THEN t.name + N'(' + CASE WHEN c.max_length = -1 THEN N'MAX'
                                      ELSE CONVERT(NVARCHAR(10), c.max_length) END + N')'
        WHEN t.name IN (N'decimal', N'numeric')
            THEN t.name + N'(' + CONVERT(NVARCHAR(10), c.precision) + N',' +
                 CONVERT(NVARCHAR(10), c.scale) + N')'
        WHEN t.name IN (N'datetime2', N'datetimeoffset', N'time')
            THEN t.name + N'(' + CONVERT(NVARCHAR(10), c.scale) + N')'
        ELSE t.name
    END
FROM sys.columns c
JOIN sys.types t ON t.user_type_id = c.user_type_id
WHERE c.object_id = OBJECT_ID(N'Project.request_items')
  AND c.name = N'id';

IF @request_id_type IS NULL
    THROW 51001, 'Could not find Project.requests.id. Check the table and schema.', 1;

IF @request_item_id_type IS NULL
    THROW 51002, 'Could not find Project.request_items.id. Check the table and schema.', 1;

BEGIN TRY
    BEGIN TRANSACTION;

    IF OBJECT_ID(N'Project.request_supplier_offers', N'U') IS NULL
    BEGIN
        SET @sql = N'
        CREATE TABLE Project.request_supplier_offers
        (
            id INT IDENTITY(1,1) NOT NULL
                CONSTRAINT PK_request_supplier_offers PRIMARY KEY,
            request_id ' + @request_id_type + N' NOT NULL,
            request_item_id ' + @request_item_id_type + N' NOT NULL,
            supplier_name NVARCHAR(250) NOT NULL,
            proforma_number NVARCHAR(150) NOT NULL,
            delivery_date_fa NVARCHAR(10) NOT NULL,
            quantity DECIMAL(18,3) NOT NULL,
            unit_price DECIMAL(18,2) NOT NULL,
            payment_method NVARCHAR(500) NOT NULL,
            created_by NVARCHAR(128) NOT NULL,
            created_at DATETIME2(0) NOT NULL
                CONSTRAINT DF_request_supplier_offers_created_at DEFAULT (SYSDATETIME()),
            CONSTRAINT FK_request_supplier_offers_request
                FOREIGN KEY (request_id) REFERENCES Project.requests(id),
            CONSTRAINT FK_request_supplier_offers_item
                FOREIGN KEY (request_item_id) REFERENCES Project.request_items(id),
            CONSTRAINT CK_request_supplier_offers_quantity CHECK (quantity > 0),
            CONSTRAINT CK_request_supplier_offers_unit_price CHECK (unit_price > 0)
        );';

        EXEC sys.sp_executesql @sql;
    END;

    IF NOT EXISTS (
        SELECT 1 FROM sys.indexes
        WHERE object_id = OBJECT_ID(N'Project.request_supplier_offers')
          AND name = N'IX_request_supplier_offers_request'
    )
    BEGIN
        CREATE INDEX IX_request_supplier_offers_request
            ON Project.request_supplier_offers(request_id, request_item_id);
    END;

    COMMIT TRANSACTION;
    PRINT 'Supplier offer storage is ready.';
    PRINT 'request_id type: ' + @request_id_type;
    PRINT 'request_item_id type: ' + @request_item_id_type;
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
