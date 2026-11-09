USE bi;
GO

SET NOCOUNT ON;
SET XACT_ABORT ON;

IF OBJECT_ID(N'Project.request_supplier_offers', N'U') IS NULL
    THROW 51201, 'Project.request_supplier_offers is missing. Run the supplier-offer migration first.', 1;

IF COL_LENGTH(N'Project.request_supplier_offers', N'is_selected') IS NULL
BEGIN
    ALTER TABLE Project.request_supplier_offers
        ADD is_selected BIT NOT NULL
            CONSTRAINT DF_request_supplier_offers_is_selected DEFAULT (0) WITH VALUES;
END;
GO

DECLARE @request_id_type NVARCHAR(300);
DECLARE @sql NVARCHAR(MAX);

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

IF @request_id_type IS NULL
    THROW 51202, 'Could not find Project.requests.id.', 1;

IF OBJECT_ID(N'Project.request_deliveries', N'U') IS NULL
BEGIN
    SET @sql = N'
    CREATE TABLE Project.request_deliveries
    (
        id BIGINT IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_request_deliveries PRIMARY KEY,
        request_id ' + @request_id_type + N' NOT NULL,
        offer_id INT NOT NULL,
        receipt_no NVARCHAR(100) NOT NULL,
        delivery_date_fa NVARCHAR(10) NOT NULL,
        quantity DECIMAL(18,3) NOT NULL,
        recorded_by NVARCHAR(128) NOT NULL,
        recorded_at DATETIME2(0) NOT NULL
            CONSTRAINT DF_request_deliveries_recorded_at DEFAULT (SYSDATETIME()),
        CONSTRAINT FK_request_deliveries_request
            FOREIGN KEY (request_id) REFERENCES Project.requests(id),
        CONSTRAINT FK_request_deliveries_offer
            FOREIGN KEY (offer_id) REFERENCES Project.request_supplier_offers(id),
        CONSTRAINT CK_request_deliveries_quantity CHECK (quantity > 0)
    );';
    EXEC sys.sp_executesql @sql;
END;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'Project.request_deliveries')
      AND name = N'IX_request_deliveries_offer'
)
BEGIN
    CREATE INDEX IX_request_deliveries_offer
        ON Project.request_deliveries(offer_id, delivery_date_fa);
END;
GO

PRINT 'Supplier selection and staged delivery receipt tracking are ready.';
