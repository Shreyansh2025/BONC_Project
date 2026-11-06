/*
    Migration: 003_create_search_ai_settings.sql

    Purpose:
    Create dedicated Search AI configuration storage.

    Existing dbo tables are not modified.

    Safe to rerun:
    - Creates the schema only if missing.
    - Creates each table only if missing.
    - Inserts initial defaults only if no active settings exist.

    STAGING ONLY: This migration intentionally checks the database name.
*/
SET NOCOUNT ON;

SET XACT_ABORT ON;

-- Safety check: never accidentally run this staging migration in production.
IF DB_NAME() <> N'RN_BoncNetwork_Staging'
    BEGIN
        THROW 51000, 'Safety stop: this migration is intended for RN_BoncNetwork_Staging only.', 1;
    END

DECLARE @InitialSettingsJson AS NVARCHAR (MAX) = N'
{
    "semantic_candidate_limit": 50,
    "lexical_result_threshold": 20,
    "company": {
        "semantic_min_score": 35.0,
        "semantic_high_confidence_score": 60.0
    },
    "b2b_product": {
        "semantic_min_score": 40.0,
        "semantic_high_confidence_score": 65.0
    },
    "local_product": {
        "semantic_min_score": 10.0
    }
}';

BEGIN TRY
    BEGIN TRANSACTION;
    -- 1. Create a separate Search AI schema.
    IF SCHEMA_ID(N'searchai') IS NULL
        BEGIN
            EXECUTE (N'CREATE SCHEMA [searchai] AUTHORIZATION [dbo];');
        END
    -- 2. Store the current active settings (one configuration row).
    IF OBJECT_ID(N'searchai.SearchAISettings', N'U') IS NULL
        BEGIN
            CREATE TABLE [searchai].[SearchAISettings] (
                [SettingId]    TINYINT        NOT NULL,
                [Version]      INT            NOT NULL,
                [SettingsJson] NVARCHAR (MAX) NOT NULL,
                [UpdatedAtUtc] DATETIME2 (3)  CONSTRAINT [DF_SearchAISettings_UpdatedAtUtc] DEFAULT SYSUTCDATETIME() NOT NULL,
                [UpdatedBy]    NVARCHAR (256) NOT NULL,
                CONSTRAINT [PK_SearchAISettings] PRIMARY KEY ([SettingId]),
                CONSTRAINT [CK_SearchAISettings_Singleton] CHECK ([SettingId] = 1),
                CONSTRAINT [CK_SearchAISettings_Version] CHECK ([Version] >= 1),
                CONSTRAINT [CK_SearchAISettings_ValidJson]
                    CHECK (ISJSON([SettingsJson], OBJECT) = 1)
                );
        END
    -- 3. Keep a history snapshot for every configuration version.
    IF OBJECT_ID(N'searchai.SearchAISettingsHistory', N'U') IS NULL
        BEGIN
            CREATE TABLE [searchai].[SearchAISettingsHistory] (
                [HistoryId]    BIGINT          IDENTITY (1, 1) NOT NULL,
                [Version]      INT             NOT NULL,
                [SettingsJson] NVARCHAR (MAX)  NOT NULL,
                [ChangedAtUtc] DATETIME2 (3)   CONSTRAINT [DF_SearchAISettingsHistory_ChangedAtUtc] DEFAULT SYSUTCDATETIME() NOT NULL,
                [ChangedBy]    NVARCHAR (256)  NOT NULL,
                [ChangeReason] NVARCHAR (1000) NULL,
                CONSTRAINT [PK_SearchAISettingsHistory] PRIMARY KEY ([HistoryId]),
                CONSTRAINT [UQ_SearchAISettingsHistory_Version] UNIQUE ([Version]),
                CONSTRAINT [CK_SearchAISettingsHistory_Version] CHECK ([Version] >= 1),
                CONSTRAINT [CK_SearchAISettingsHistory_ValidJson]
                    CHECK (ISJSON([SettingsJson], OBJECT) = 1)
                );
        END
    -- 4. Insert version 1 using the existing search defaults.
    IF NOT EXISTS (SELECT 1
                   FROM   [searchai].[SearchAISettings]
                   WHERE  [SettingId] = 1)
        BEGIN
            INSERT  INTO [searchai].[SearchAISettings] (
                [SettingId],
                [Version],
                [SettingsJson],
                [UpdatedBy]
            )
            VALUES                                     (1, 1, @InitialSettingsJson, N'system:initial');
            INSERT  INTO [searchai].[SearchAISettingsHistory] (
                [Version],
                [SettingsJson],
                [ChangedBy],
                [ChangeReason]
            )
            VALUES                                            (1, @InitialSettingsJson, N'system:initial', N'Initial settings; defaults match SearchRulesSettings.');
        END
    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0
        ROLLBACK;
    THROW;
END CATCH

-- 5. Show the created objects and current configuration version.
SELECT   DB_NAME() AS DatabaseName,
         S.name AS SchemaName,
         T.name AS TableName
FROM     sys.tables AS T
         INNER JOIN
         sys.schemas AS S
         ON T.schema_id = S.schema_id
WHERE    S.name = N'searchai'
ORDER BY T.name;

SELECT [SettingId],
       [Version],
       [UpdatedAtUtc],
       [UpdatedBy],
       [SettingsJson]
FROM   [searchai].[SearchAISettings];