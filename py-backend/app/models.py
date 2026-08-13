import re
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.utils.serialize import json_col

CATEGORIES = [
    "Medical",
    "Electronics",
    "Tractor",
    "Shoes",
    "Solar",
    "AI",
    "MRI",
    "Rice",
    "Agriculture",
    "Plant Equipment",
    "Automobile",
    "Machinery",
]


class AdditionalSection(BaseModel):
    heading: str | None = None
    content: str | None = None


class BrochureCreate(BaseModel):
    fileName: str
    category: str
    title: str = ""
    description: str = ""
    specifications: list[str] = Field(default_factory=list)
    additionalSections: list[AdditionalSection] = Field(default_factory=list)
    extractedText: str = ""
    extractedImages: list[str] = Field(default_factory=list)

    def to_row(self) -> dict[str, Any]:
        """Builds the PascalCase / JSON-encoded row dict for INSERT INTO Brochures."""
        return {
            "FileName": self.fileName,
            "Category": self.category,
            "Title": self.title,
            "Description": self.description,
            "Specifications": json_col(self.specifications),
            "AdditionalSections": json_col([s.model_dump() for s in self.additionalSections]),
            "ExtractedText": self.extractedText,
            "ExtractedImages": json_col(self.extractedImages),
            "UploadDate": datetime.now(timezone.utc),
        }
    


class ProductCreate(BaseModel):
    productName: str
    category: str
    model: str | None = None
    description: str = ""
    price: str | None = None
    features: list[str] = Field(default_factory=list)
    specifications: dict[str, Any] = Field(default_factory=dict)
    images: list[str] = Field(default_factory=list, max_length=5)
    sourceFileName: str = ""
    slug: str | None = None
    imagePath: str | None = None

    def to_row(self) -> dict[str, Any]:
        """Builds the row dict for INSERT INTO Products with automatic fallbacks."""
        
        # 1. Fallback for Model: If model is missing, use a slugified version of the product name
        resolved_model = self.model
        if not resolved_model or not resolved_model.strip():
            resolved_model = slugify(self.productName, fallback="model")

        # 2. Fallback for ImagePath: Grab the first image from the images list if imagePath is empty
        resolved_image_path = self.imagePath
        if not resolved_image_path and self.images:
            resolved_image_path = self.images[0]

        resolved_slug = self.slug
        if not resolved_slug:
            resolved_slug = slugify(self.productName)

        row = {
            "ProductName": self.productName,
            "Category": self.category,
            "Model": resolved_model,              # Automatically populated!
            "Description": self.description,
            "Price": self.price,
            "Features": json_col(self.features),
            "Specifications": json_col(self.specifications),
            "Images": json_col(self.images),
            "SourceFileName": self.sourceFileName,
            "CreatedDate": datetime.now(timezone.utc),
            "Slug": resolved_slug,
            "ImagePath": resolved_image_path,     # Automatically populated!
        }
        return row

    
class SaveProductsBody(BaseModel):
    products: list[ProductCreate]
    brochureId: str | None = None


class UpdateProductImages(BaseModel):
    images: list[str] = Field(default_factory=list, max_length=5)


# ─── Slug helper ─────────────────────────────────────────────────────────
# Products imported from the B2B catalog may not have a Slug column value
# (older rows, or rows imported before the column existed). Rather than
# failing to link to them, the search route falls back to generating one
# on the fly from the product name — e.g. "Heavy Duty Tractor #2" ->
# "heavy-duty-tractor-2". This is intentionally simple regex-based slugging
# (no external deps), and is NOT persisted back to the database — it's a
# read-time fallback only.
_SLUG_STRIP_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str | None, fallback: str = "") -> str:
    """Turns arbitrary text into a URL-friendly slug. Falls back to
    `fallback` (usually a row id) if the text has no usable characters,
    e.g. slugify(None, fallback="42") -> "product-42"."""
    raw = (text or "").strip().lower()
    slug = _SLUG_STRIP_RE.sub("-", raw).strip("-")
    if slug:
        return slug
    return f"product-{fallback}" if fallback else "product"


# ─── Unified search response shapes ────────────────────────────────────────
# Both /search/companies and /search/products already return dicts shaped
# by serialize_row() (see app/utils/serialize.py) plus a few fields the
# search-scoring code bolts on (matchType, matchPercentage, matchedKeyword).
# These two models describe that combined shape for the unified /search
# endpoint, tagged with `type` so the frontend knows which card layout to
# use, and with `image`/`slug` always populated (never null) so the
# frontend doesn't need per-field null-checks.

class BusinessSearchResult(BaseModel):
    type: Literal["business"] = "business"
    id: str | int | None = Field(default=None, alias="_id")
    businessName: str | None = None
    categoryId: str | None = None
    businessSlug: str | None = None
    tagline: str | None = None
    description: str | None = None
    address1: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    pincode: str | None = None
    image: str | None = None
    matchType: str | None = None
    matchPercentage: float | None = None
    matchedKeyword: str | None = None

    model_config = {"populate_by_name": True}


class ProductSearchResult(BaseModel):
    type: Literal["product"] = "product"
    id: str | int | None = Field(default=None, alias="_id")
    productName: str | None = None
    slug: str = ""
    categoryName: str | None = None
    brandName: str | None = None
    businessName: str | None = None
    description: str | None = None
    minPrice: Any = None
    maxPrice: Any = None
    image: str | None = None
    matchType: str | None = None
    matchPercentage: float | None = None
    matchedKeyword: str | None = None

    model_config = {"populate_by_name": True}


class PaginationMeta(BaseModel):
    current_page: int
    limit: int
    total_results: int
    total_pages: int
    has_next: bool
    has_previous: bool


class UnifiedSearchResponse(BaseModel):
    results: list[dict[str, Any]]
    pagination: PaginationMeta