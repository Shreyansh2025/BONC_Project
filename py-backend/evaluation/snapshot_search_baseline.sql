/*
  Local Search AI configuration + index baseline snapshot.
  Run once against the same local SQL Server database used by Search AI.
  Creates read-only copies under searchai_baseline; it does not change live tables.
  Snapshot suffix: 20261119
*/
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF SCHEMA_ID(N'searchai_baseline') IS NULL
    EXEC(N'CREATE SCHEMA searchai_baseline AUTHORIZATION dbo;');

IF OBJECT_ID(N'searchai_baseline.SearchCategories_20261119', N'U') IS NOT NULL
 OR OBJECT_ID(N'searchai_baseline.SearchCategoryHistory_20261119', N'U') IS NOT NULL
 OR OBJECT_ID(N'searchai_baseline.SearchFeedback_20261119', N'U') IS NOT NULL
 OR OBJECT_ID(N'searchai_baseline.SearchDocuments_20261119', N'U') IS NOT NULL
 OR OBJECT_ID(N'searchai_baseline.SearchAISettings_20261119', N'U') IS NOT NULL
 OR OBJECT_ID(N'searchai_baseline.SearchAISettingsHistory_20261119', N'U') IS NOT NULL
BEGIN
    THROW 50001, 'A 20261119 Search AI baseline snapshot already exists. Do not overwrite it.', 1;
END;

BEGIN TRY
    BEGIN TRANSACTION;

    SELECT * INTO searchai_baseline.SearchCategories_20261119
    FROM searchai.SearchCategories;

    SELECT * INTO searchai_baseline.SearchCategoryHistory_20261119
    FROM searchai.SearchCategoryHistory;

    SELECT * INTO searchai_baseline.SearchFeedback_20261119
    FROM searchai.SearchFeedback;

    SELECT * INTO searchai_baseline.SearchDocuments_20261119
    FROM searchai.SearchDocuments;

    SELECT * INTO searchai_baseline.SearchAISettings_20261119
    FROM searchai.SearchAISettings;

    SELECT * INTO searchai_baseline.SearchAISettingsHistory_20261119
    FROM searchai.SearchAISettingsHistory;

    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;

SELECT 'SearchCategories' AS SnapshotTable, COUNT_BIG(*) AS RowCount
FROM searchai_baseline.SearchCategories_20261119
UNION ALL
SELECT 'SearchCategoryHistory', COUNT_BIG(*)
FROM searchai_baseline.SearchCategoryHistory_20261119
UNION ALL
SELECT 'SearchFeedback', COUNT_BIG(*)
FROM searchai_baseline.SearchFeedback_20261119
UNION ALL
SELECT 'SearchDocuments', COUNT_BIG(*)
FROM searchai_baseline.SearchDocuments_20261119
UNION ALL
SELECT 'SearchAISettings', COUNT_BIG(*)
FROM searchai_baseline.SearchAISettings_20261119
UNION ALL
SELECT 'SearchAISettingsHistory', COUNT_BIG(*)
FROM searchai_baseline.SearchAISettingsHistory_20261119;
