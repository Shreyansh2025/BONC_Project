/*
    Migration 005: capture the ranking snapshot attached to Search AI feedback.

    Staging only while the generic search platform is being validated.
    Run after migration 004 against RN_BoncNetwork_Staging only.
*/
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DB_NAME() <> N'RN_BoncNetwork_Staging'
BEGIN
    THROW 51005, 'Safety stop: migration 005 is intended for RN_BoncNetwork_Staging only.', 1;
END;

IF OBJECT_ID(N'searchai.SearchFeedback', N'U') IS NULL
BEGIN
    THROW 51006, 'searchai.SearchFeedback is missing. Run migration 004 first.', 1;
END;

BEGIN TRY
    BEGIN TRANSACTION;

    IF COL_LENGTH(N'searchai.SearchFeedback', N'ResultRank') IS NULL
        ALTER TABLE searchai.SearchFeedback ADD ResultRank INT NULL;

    IF COL_LENGTH(N'searchai.SearchFeedback', N'ResultScore') IS NULL
        ALTER TABLE searchai.SearchFeedback ADD ResultScore FLOAT(53) NULL;

    IF COL_LENGTH(N'searchai.SearchFeedback', N'ConfigVersion') IS NULL
        ALTER TABLE searchai.SearchFeedback ADD ConfigVersion INT NULL;

    IF COL_LENGTH(N'searchai.SearchFeedback', N'IndexVersion') IS NULL
        ALTER TABLE searchai.SearchFeedback ADD IndexVersion INT NULL;

    IF NOT EXISTS (
        SELECT 1 FROM sys.check_constraints
        WHERE name = N'CK_SearchFeedback_ResultRank'
          AND parent_object_id = OBJECT_ID(N'searchai.SearchFeedback')
    )
        ALTER TABLE searchai.SearchFeedback WITH CHECK
            ADD CONSTRAINT CK_SearchFeedback_ResultRank
            CHECK (ResultRank IS NULL OR ResultRank BETWEEN 1 AND 100);

    IF NOT EXISTS (
        SELECT 1 FROM sys.check_constraints
        WHERE name = N'CK_SearchFeedback_ResultScore'
          AND parent_object_id = OBJECT_ID(N'searchai.SearchFeedback')
    )
        ALTER TABLE searchai.SearchFeedback WITH CHECK
            ADD CONSTRAINT CK_SearchFeedback_ResultScore
            CHECK (ResultScore IS NULL OR (ResultScore >= 0 AND ResultScore <= 100));

    IF NOT EXISTS (
        SELECT 1 FROM sys.check_constraints
        WHERE name = N'CK_SearchFeedback_ConfigVersion'
          AND parent_object_id = OBJECT_ID(N'searchai.SearchFeedback')
    )
        ALTER TABLE searchai.SearchFeedback WITH CHECK
            ADD CONSTRAINT CK_SearchFeedback_ConfigVersion
            CHECK (ConfigVersion IS NULL OR ConfigVersion >= 1);

    IF NOT EXISTS (
        SELECT 1 FROM sys.check_constraints
        WHERE name = N'CK_SearchFeedback_IndexVersion'
          AND parent_object_id = OBJECT_ID(N'searchai.SearchFeedback')
    )
        ALTER TABLE searchai.SearchFeedback WITH CHECK
            ADD CONSTRAINT CK_SearchFeedback_IndexVersion
            CHECK (IndexVersion IS NULL OR IndexVersion >= 0);

    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK;
    THROW;
END CATCH;
