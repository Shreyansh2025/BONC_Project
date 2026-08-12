-- Adds Model and Description to an existing Products table without losing
-- data. Run this once against your live database (instead of re-running
-- schema.sql, which would try to CREATE TABLE again).
-- Safe to run on a fresh database too — the checks make it a no-op if the
-- columns already exist.

IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE object_id = OBJECT_ID('Products') AND name = 'Model'
)
BEGIN
    ALTER TABLE Products ADD Model NVARCHAR(200) NULL;
END

IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE object_id = OBJECT_ID('Products') AND name = 'Description'
)
BEGIN
    ALTER TABLE Products ADD Description NVARCHAR(MAX) NULL;
END
