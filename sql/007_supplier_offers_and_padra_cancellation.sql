USE bi;
GO

SET NOCOUNT ON;
SET XACT_ABORT ON;

BEGIN TRY
    BEGIN TRANSACTION;

    IF OBJECT_ID(N'Project.request_supplier_offers', N'U') IS NULL
    BEGIN
        CREATE TABLE Project.request_supplier_offers
        (
            id INT IDENTITY(1,1) NOT NULL
                CONSTRAINT PK_request_supplier_offers PRIMARY KEY,
            request_id INT NOT NULL,
            request_item_id INT NOT NULL,
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
        );
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
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
