from datetime import datetime, timezone
from typing import Any

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

    def to_row(self) -> dict[str, Any]:
        """Builds the PascalCase / JSON-encoded row dict for INSERT INTO Products."""
        return {
            "ProductName": self.productName,
            "Category": self.category,
            "Model": self.model,
            "Description": self.description,
            "Price": self.price,
            "Features": json_col(self.features),
            "Specifications": json_col(self.specifications),
            "Images": json_col(self.images),
            "SourceFileName": self.sourceFileName,
            "CreatedDate": datetime.now(timezone.utc),
        }


class SaveProductsBody(BaseModel):
    products: list[ProductCreate]
    brochureId: str | None = None


class UpdateProductImages(BaseModel):
    images: list[str] = Field(default_factory=list, max_length=5)
