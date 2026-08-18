-- Brochure app: SQL Server schema (replaces the 3 MongoDB collections)

CREATE TABLE Brochures (
    Id                  INT IDENTITY(1,1) PRIMARY KEY,
    FileName            NVARCHAR(500)   NOT NULL,
    Category            NVARCHAR(200)   NOT NULL,
    Title               NVARCHAR(1000)  NULL,
    Description         NVARCHAR(MAX)   NULL,
    Specifications       NVARCHAR(MAX)   NULL,  -- JSON array of strings
    AdditionalSections  NVARCHAR(MAX)   NULL,  -- JSON array of {heading, content}
    ExtractedText       NVARCHAR(MAX)   NULL,
    ExtractedImages     NVARCHAR(MAX)   NULL,  -- JSON array of image URLs
    UploadDate          DATETIME2       NOT NULL DEFAULT SYSUTCDATETIME()
);

CREATE TABLE Products (
    Id                  INT IDENTITY(1,1) PRIMARY KEY,
    ProductName         NVARCHAR(500)   NOT NULL,
    Category            NVARCHAR(200)   NOT NULL,
    Model               NVARCHAR(200)   NULL,
    Description         NVARCHAR(MAX)   NULL,
    Price               NVARCHAR(100)   NULL,
    Features            NVARCHAR(MAX)   NULL,  -- JSON array of strings
    Specifications      NVARCHAR(MAX)   NULL,  -- JSON object (key/value)
    Images              NVARCHAR(MAX)   NULL,  -- JSON array of image URLs (max 5)
    ImagePath           NVARCHAR(1000)  NULL,  -- single primary image, used by search results
    Slug                NVARCHAR(500)   NULL,  -- URL-friendly identifier
    SourceFileName      NVARCHAR(500)   NULL,
    CreatedDate         DATETIME2       NOT NULL DEFAULT SYSUTCDATETIME()
);

CREATE TABLE B2BCompanies (
    Id                  INT IDENTITY(1,1) PRIMARY KEY,
    BusinessId          NVARCHAR(36)    NOT NULL,  -- original GUID from the CSV
    BusinessName        NVARCHAR(500)   NULL,
    CategoryId          NVARCHAR(100)   NULL,
    BusinessDescription NVARCHAR(MAX)   NULL,
    BusinessSlug        NVARCHAR(500)   NULL,
    Tagline             NVARCHAR(1000)  NULL,
    AboutBrief          NVARCHAR(MAX)   NULL,
    Description         NVARCHAR(MAX)   NULL,
    Vision              NVARCHAR(MAX)   NULL,
    WhyChooseUs         NVARCHAR(MAX)   NULL,
    Address1            NVARCHAR(500)   NULL,
    City                NVARCHAR(200)   NULL,
    State               NVARCHAR(200)   NULL,
    Country             NVARCHAR(200)   NULL,
    Pincode             NVARCHAR(20)    NULL,
    Landmark            NVARCHAR(500)   NULL,
    ImagePath           NVARCHAR(1000)  NULL  -- company logo / cover image
);

CREATE TABLE B2BProducts (
    Id                          INT IDENTITY(1,1) PRIMARY KEY,
    UniqueId                    NVARCHAR(64)    NOT NULL,  -- e.g. PRD1778152482530
    IndustryName                NVARCHAR(300)   NULL,
    CategoryName                NVARCHAR(300)   NULL,
    SubCategoryName             NVARCHAR(300)   NULL,
    DispositionName              NVARCHAR(300)   NULL,
    ProductName                 NVARCHAR(500)   NULL,
    ItemType                    NVARCHAR(200)   NULL,
    ProductType                 NVARCHAR(200)   NULL,
    Description                 NVARCHAR(MAX)   NULL,
    ManufacturerName             NVARCHAR(300)   NULL,
    BrandName                   NVARCHAR(300)   NULL,
    KeyWords                    NVARCHAR(MAX)   NULL,
    BusinessName                NVARCHAR(500)   NULL,
    CustomizedPrice             NVARCHAR(50)    NULL,
    MinPrice                    DECIMAL(18,2)   NULL,
    MaxPrice                    DECIMAL(18,2)   NULL,
    ExportCapabilities           NVARCHAR(200)   NULL,
    CustomizationAvailability    NVARCHAR(200)   NULL,
    ShipsGlobally                NVARCHAR(200)   NULL,
    HazardousGoods                NVARCHAR(200)   NULL,
    GstPercentage                 NVARCHAR(50)    NULL,
    AverageDeliveryTime           NVARCHAR(200)   NULL,
    ProcessingTime                NVARCHAR(200)   NULL,
    CountryOfOrigin               NVARCHAR(200)   NULL,
    MinimumOrderQuantity          NVARCHAR(200)   NULL,
    Status                        NVARCHAR(50)    NULL,
    PublishDate                   NVARCHAR(50)    NULL,
    Slug                          NVARCHAR(500)   NULL   -- URL-friendly identifier
    ImagePath                     NVARCHAR(1000)  NULL,  -- primary product image
);

CREATE INDEX IX_Products_CreatedDate ON Products(CreatedDate DESC);
CREATE INDEX IX_Brochures_UploadDate ON Brochures(UploadDate DESC);
CREATE INDEX IX_B2BCompanies_BusinessName ON B2BCompanies(BusinessName);
CREATE INDEX IX_B2BCompanies_City ON B2BCompanies(City);
CREATE INDEX IX_B2BProducts_ProductName ON B2BProducts(ProductName);
CREATE INDEX IX_B2BProducts_BusinessName ON B2BProducts(BusinessName);
CREATE INDEX IX_B2BProducts_CategoryName ON B2BProducts(CategoryName);
CREATE INDEX IX_B2BProducts_Slug ON B2BProducts(Slug);