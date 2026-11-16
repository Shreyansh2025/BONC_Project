/*
    Migration 004: configuration-driven Search AI platform.

    Creates isolated searchai tables. Existing dbo tables and the legacy
    company/product search implementation are not modified.
    Run against the Search AI database after migration 003.
*/
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF SCHEMA_ID(N'searchai') IS NULL
BEGIN
    EXECUTE (N'CREATE SCHEMA [searchai] AUTHORIZATION [dbo];');
END;

BEGIN TRY
    BEGIN TRANSACTION;

    IF OBJECT_ID(N'searchai.SearchCategories', N'U') IS NULL
    BEGIN
        CREATE TABLE searchai.SearchCategories (
            CategoryId       NVARCHAR(64)  NOT NULL,
            DisplayName      NVARCHAR(120) NOT NULL,
            ConfigJson       NVARCHAR(MAX) NOT NULL,
            Status           NVARCHAR(20)  NOT NULL CONSTRAINT DF_SearchCategories_Status DEFAULT N'disabled',
            ConfigVersion    INT           NOT NULL CONSTRAINT DF_SearchCategories_ConfigVersion DEFAULT 1,
            IndexVersion     INT           NOT NULL CONSTRAINT DF_SearchCategories_IndexVersion DEFAULT 0,
            CreatedAtUtc     DATETIME2(3)  NOT NULL CONSTRAINT DF_SearchCategories_CreatedAtUtc DEFAULT SYSUTCDATETIME(),
            UpdatedAtUtc     DATETIME2(3)  NOT NULL CONSTRAINT DF_SearchCategories_UpdatedAtUtc DEFAULT SYSUTCDATETIME(),
            UpdatedBy        NVARCHAR(256) NOT NULL,
            CONSTRAINT PK_SearchCategories PRIMARY KEY (CategoryId),
            CONSTRAINT CK_SearchCategories_Status CHECK (Status IN (N'active', N'disabled')),
            CONSTRAINT CK_SearchCategories_ConfigVersion CHECK (ConfigVersion >= 1),
            CONSTRAINT CK_SearchCategories_IndexVersion CHECK (IndexVersion >= 0),
            CONSTRAINT CK_SearchCategories_ConfigJson CHECK (ISJSON(ConfigJson) = 1)
        );
    END;

    IF OBJECT_ID(N'searchai.SearchDocuments', N'U') IS NULL
    BEGIN
        CREATE TABLE searchai.SearchDocuments (
            CategoryId       NVARCHAR(64)  NOT NULL,
            DocumentId       NVARCHAR(200) NOT NULL,
            Title             NVARCHAR(1000) NOT NULL CONSTRAINT DF_SearchDocuments_Title DEFAULT N'',
            FieldsJson        NVARCHAR(MAX) NOT NULL,
            MetadataJson      NVARCHAR(MAX) NOT NULL CONSTRAINT DF_SearchDocuments_Metadata DEFAULT N'{}',
            CreatedAtUtc      DATETIME2(3) NOT NULL CONSTRAINT DF_SearchDocuments_CreatedAtUtc DEFAULT SYSUTCDATETIME(),
            UpdatedAtUtc      DATETIME2(3) NOT NULL CONSTRAINT DF_SearchDocuments_UpdatedAtUtc DEFAULT SYSUTCDATETIME(),
            CONSTRAINT PK_SearchDocuments PRIMARY KEY (CategoryId, DocumentId),
            CONSTRAINT FK_SearchDocuments_Category FOREIGN KEY (CategoryId)
                REFERENCES searchai.SearchCategories(CategoryId) ON DELETE CASCADE,
            CONSTRAINT CK_SearchDocuments_FieldsJson CHECK (ISJSON(FieldsJson) = 1),
            CONSTRAINT CK_SearchDocuments_MetadataJson CHECK (ISJSON(MetadataJson) = 1)
        );
        CREATE INDEX IX_SearchDocuments_Category ON searchai.SearchDocuments(CategoryId);
    END;

    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK;
    THROW;
END CATCH;
