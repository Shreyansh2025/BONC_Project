import asyncio
import json
import os
import re
from typing import Any, TypedDict

import httpx

from app.logger import logger

MAX_TOKENS_PER_CHUNK = 3000
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"

PAGE_PATTERN = re.compile(r"---\s*PAGE\s+(\d+)\s*---", re.IGNORECASE)

SYSTEM_PROMPT = """You are an expert parts catalog and tabular price list parser. 

CRITICAL RULES FOR TABULAR / LIST PAGES:
1. ROW-BY-ROW EXTRACTION: This page contains a table or list where each row is a separate product (e.g., matching a product name/title with its size, code, or description). 
2. YOU MUST CREATE A SEPARATE PRODUCT OBJECT FOR EVERY SINGLE ROW in the table. Never group multiple table rows into a single product's features.
3. MAP FIELDS ACCURATELY: 
   - "title": The product name from that row (e.g., "Brass Copler", "Brass Male Female Nipple").
   - "specifications": Put sizes, dimensions, or material codes found in that row here.
   - "price": Any price listed in that row.

4. OUTPUT FORMAT (valid JSON only):
{
  "products": [
    {
      "title": "Row Product Name",
      "model": "Model code if present, else null",
      "category": "Fittings / Hardware",
      "description": "Description from row",
      "price": "null",
      "specifications": { "Size": "3/4\" x 1/2\"" },
      "features": [],
      "pages": [4],
      "imageRegion": "left"
    }
  ]
}

IMPORTANT: Return ONLY valid JSON containing an array for every single row."""
class ExtractedProduct(TypedDict):
    name: str
    model: str | None
    category: str | None
    description: str | None
    price: str | None
    specifications: dict[str, str]
    features: list[str]
    pages: list[int]
    imageRegion: str | None

async def structure_text_with_ai(raw_text: str) -> dict[str, Any]:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY not configured")

    if not raw_text or len(raw_text.strip()) < 50:
        raise RuntimeError("Insufficient text for AI structuring")

    # Split strictly page by page to keep token payload tiny and safe from rate limits
    pages = raw_text.split("--- PAGE")
    logger.info(f"AI structuring total pages: {len(pages) - 1}")

    all_products: list[ExtractedProduct] = []

    async with httpx.AsyncClient(timeout=60) as client:
        for i, page_content in enumerate(pages):
            if not page_content.strip():
                continue
            
            page_text = f"--- PAGE {page_content}"
            
            try:
                chunk_products = await _process_chunk(client, page_text, api_key)
                if chunk_products:
                    all_products.extend(chunk_products)
            except Exception as err:
                logger.error(f"Page {i} AI processing failed: {err}")
                # If hit with a rate limit, pause a bit longer to let Groq cool down
                if "429" in str(err):
                    await asyncio.sleep(6)
            
            # Mandatory safety delay between every single page
            if i < len(pages) - 1:
                await asyncio.sleep(3)

    if not all_products:
        raise RuntimeError("AI structuring returned no products")

    deduped = _deduplicate_products(all_products)
    return _build_structured_response(deduped)

def _chunk_text(text: str, max_tokens: int) -> list[str]:
    max_chars = max_tokens * 4
    pages = PAGE_PATTERN.split(text)
    page_numbers = [int(m.group(1)) for m in PAGE_PATTERN.finditer(text)]

    chunks: list[str] = []
    current_chunk = ""
    current_length = 0

    for i, page_num in enumerate(page_numbers):
        page_content = pages[(i * 2) + 2] if (i * 2) + 2 < len(pages) else ""
        page_text = f"--- PAGE {page_num} ---\n{page_content}"
        page_length = len(page_text)

        if current_length + page_length > max_chars and len(current_chunk) > 0:
            chunks.append(current_chunk.strip())
            current_chunk = page_text
            current_length = page_length
        else:
            current_chunk += "\n" + page_text
            current_length += page_length

    if current_chunk.strip():
        chunks.append(current_chunk.strip())

    if not chunks:
        chunks = [text[i : i + max_chars] for i in range(0, len(text), max_chars)]

    return chunks if chunks else [text]

async def _process_chunk(
    client: httpx.AsyncClient, chunk_text: str, api_key: str
) -> list[ExtractedProduct]:
    response = await client.post(
        GROQ_API_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": GROQ_MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"Parse the following OCR text and extract ALL product information completely:\n\n{chunk_text}",
                },
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": 4096,
            "temperature": 0.1,
        },
    )

    if response.status_code >= 400:
        raise RuntimeError(f"Groq API error {response.status_code}: {response.text}")

    data = response.json()
    content = data.get("choices", [{}])[0].get("message", {}).get("content")
    if not content:
        raise RuntimeError("Empty AI response")

    parsed = json.loads(content)
    products = parsed.get("products")
    if not isinstance(products, list):
        return []

    result: list[ExtractedProduct] = []
    for p in products:
        if not isinstance(p, dict):
            continue
        
        # Robust title fallback
        product_name = str(p.get("title") or p.get("name") or "").strip()
        if not product_name or product_name.lower() in ['null', 'none']:
            product_name = "Unknown Product"

        price_val = str(p.get("price")).strip() if p.get("price") else None
        if price_val and price_val.lower() in ['null', 'none', 'n/a']:
            price_val = None

        result.append(
            {
                "name": product_name,
                "model": str(p["model"]).strip() if p.get("model") else None,
                "category": str(p["category"]).strip() if p.get("category") else None,
                "description": str(p["description"]).strip() if p.get("description") else None,
                "price": price_val,
                "specifications": _parse_specs(p.get("specifications")),
                "features": _parse_string_array(p.get("features")),
                "pages": _parse_number_array(p.get("pages")),
                "imageRegion": str(p["imageRegion"]) if p.get("imageRegion") else None,
            }
        )
    return result

def _parse_specs(val: Any) -> dict[str, str]:
    if not isinstance(val, dict):
        return {}
    return {str(k): str(v) for k, v in val.items()}

def _parse_string_array(val: Any) -> list[str]:
    if not isinstance(val, list):
        return []
    return [str(v) for v in val if str(v)]

def _parse_number_array(val: Any) -> list[int]:
    if not isinstance(val, list):
        return []
    result = []
    for v in val:
        try:
            result.append(int(v))
        except (ValueError, TypeError):
            continue
    return result

def _deduplicate_products(products: list[ExtractedProduct]) -> list[ExtractedProduct]:
    # SAFEGUARD: Only drop exact carbon copies, never drop unique items
    seen: set[str] = set()
    deduped: list[ExtractedProduct] = []
    for p in products:
        model_part = (p["model"] or "").lower().strip()
        name_part = (p["name"] or "").lower().strip()
        
        # If there's a model number, use it as the primary uniqueness key. Otherwise use name.
        key = model_part if model_part else name_part
        if not key:
            deduped.append(p)
            continue
            
        if key in seen:
            continue
        seen.add(key)
        deduped.append(p)
    return deduped

def _build_structured_response(products: list[ExtractedProduct]) -> dict[str, Any]:
    formatted_products = [
        {
            "name": p["name"],
            "model": p["model"],
            "category": p["category"],
            "description": p["description"],
            "price": p["price"],
            "specifications": p["specifications"],
            "features": p["features"],
            "pages": sorted(p["pages"]),
            "images": [],
            "imageRegion": p["imageRegion"],
        }
        for p in products
    ]

    title = ""
    description = ""

    if len(formatted_products) == 1:
        title = formatted_products[0]["name"]
        description = formatted_products[0]["description"] or ""
    elif len(formatted_products) > 1:
        categories = list(
            dict.fromkeys(p["category"] for p in formatted_products if p["category"])
        )
        title = f"Product Catalog ({len(formatted_products)} products)"
        description = f"Contains: {', '.join(categories)}"

    specifications: list[str] = []
    if formatted_products and formatted_products[0]["specifications"]:
        for k, v in formatted_products[0]["specifications"].items():
            specifications.append(f"{k}: {v}")

    return {
        "title": title,
        "description": description,
        "specifications": specifications,
        "additionalSections": [],
        "products": formatted_products,
    }