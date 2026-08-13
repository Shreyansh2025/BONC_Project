-- Adds:
--   - ImagePath to B2BCompanies, B2BProducts, and Products (so search
--     results can show a picture)
--   - Slug to B2BProducts and Products (clean, URL-friendly product links)
--
-- Safe to run on a fresh database too — the checks make it a no-op if the
-- columns already exist. Run this once against your live database (instead
-- of re-running schema.sql, which would try to CREATE TABLE again).

-- ── ImagePath ────────────────────────────────────────────────────────────

IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE object_id = OBJECT_ID('B2BCompanies') AND name = 'ImagePath'
)
BEGIN
    ALTER TABLE B2BCompanies ADD ImagePath NVARCHAR(1000) NULL;
END

IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE object_id = OBJECT_ID('B2BProducts') AND name = 'ImagePath'
)
BEGIN
    ALTER TABLE B2BProducts ADD ImagePath NVARCHAR(1000) NULL;
END

IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE object_id = OBJECT_ID('Products') AND name = 'ImagePath'
)
BEGIN
    ALTER TABLE Products ADD ImagePath NVARCHAR(1000) NULL;
END

-- ── Slug ─────────────────────────────────────────────────────────────────

IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE object_id = OBJECT_ID('B2BProducts') AND name = 'Slug'
)
BEGIN
    ALTER TABLE B2BProducts ADD Slug NVARCHAR(500) NULL;
END

IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE object_id = OBJECT_ID('Products') AND name = 'Slug'
)
BEGIN
    ALTER TABLE Products ADD Slug NVARCHAR(500) NULL;
END

-- Helpful for slug-based lookups later (e.g. GET /products/{slug})
IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_B2BProducts_Slug' AND object_id = OBJECT_ID('B2BProducts')
)
BEGIN
    CREATE INDEX IX_B2BProducts_Slug ON B2BProducts(Slug);
END