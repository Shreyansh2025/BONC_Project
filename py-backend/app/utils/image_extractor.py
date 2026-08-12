import hashlib
import io
import os
import time
from dataclasses import dataclass

import fitz  # PyMuPDF
from PIL import Image


@dataclass
class PageResult:
    pageNumber: int
    imageUrl: str
    buffer: bytes


@dataclass
class EmbeddedImage:
    pageNumber: int
    imageUrl: str
    buffer: bytes
    region: str  # e.g. "top-left" — where this photo actually sits on the page
    area: int  # pixel area, used to prefer the larger/clearer photo when a region ties


def _classify_region(cx_frac: float, cy_frac: float) -> str:
    """Classify a point (as a fraction of page width/height) into the same
    quadrant vocabulary the AI uses for imageRegion, so an embedded photo's
    actual on-page position can be matched against a product's guessed
    region instead of just handing out photos in extraction order."""
    col = "left" if cx_frac < 0.5 else "right"
    if cy_frac < 1 / 3:
        return f"top-{col}"
    if cy_frac > 2 / 3:
        return f"bottom-{col}"
    return f"center-{col}"


def extract_embedded_images(
    file_path: str,
    uploads_dir: str,
    base_url: str,
    selected_pages: list[int] | None = None,
    min_area: int = 2500,
    max_aspect_ratio: float = 4.0,
) -> dict[int, list[EmbeddedImage]]:
    """Extract the actual embedded photos from a PDF (not page screenshots),
    grouped by the page they appear on. This mirrors the old pdf-lib XObject
    extraction approach: real product photos, deduped by content hash.

    Two things are filtered out, deliberately not by raw width/height (which
    wrongly discards narrow product shots like a portrait phone photo):
      - soft-mask companion channels (the alpha layer of another image —
        not a photo on its own, just plumbing)
      - very elongated thin strips (logos/banners/rule lines), based on
        aspect ratio rather than absolute size, so a narrow-but-substantial
        product photo still passes

    Returns {page_number: [EmbeddedImage, ...]} — each item carries the region
    it actually sits in on the page (e.g. "top-left"), so a product can be
    matched to the photo that's really next to its text instead of just
    handing out photos in xref-extraction order (which caused wrong photos,
    e.g. a lifestyle shot of a person, being assigned to the wrong product).
    """
    results: dict[int, list[EmbeddedImage]] = {}
    seen_hashes: set[str] = set()
    counter = 0

    with fitz.open(file_path) as doc:
        for i in range(doc.page_count):
            page_number = i + 1
            if selected_pages and page_number not in selected_pages:
                continue

            page = doc[i]
            page_rect = page.rect
            raw_images = page.get_images(full=True)
            # img tuple: (xref, smask_xref, width, height, bpc, colorspace, ...)
            smask_xrefs = {img[1] for img in raw_images if img[1]}

            page_images: list[EmbeddedImage] = []

            for img in raw_images:
                xref = img[0]
                if xref in smask_xrefs:
                    continue  # this xref is only the alpha channel of another image

                try:
                    base_image = doc.extract_image(xref)
                except Exception:
                    continue

                image_bytes = base_image.get("image")
                ext = base_image.get("ext", "png")
                width = base_image.get("width", 0)
                height = base_image.get("height", 0)

                if not image_bytes or width <= 0 or height <= 0:
                    continue
                if width * height < min_area:
                    continue  # too small to be a real product photo
                if max(width, height) / min(width, height) > max_aspect_ratio:
                    continue  # thin strip — logo/banner/rule line, not a photo

                content_hash = hashlib.md5(image_bytes).hexdigest()
                if content_hash in seen_hashes:
                    continue
                seen_hashes.add(content_hash)

                # Where does this image actually sit on the page? Falls back
                # to "center-left" (an arbitrary but consistent default) if
                # PyMuPDF can't locate the placement rect for this xref.
                region = "center-left"
                try:
                    rects = page.get_image_rects(xref)
                    if rects:
                        r = rects[0]
                        cx = (r.x0 + r.x1) / 2 / max(page_rect.width, 1)
                        cy = (r.y0 + r.y1) / 2 / max(page_rect.height, 1)
                        region = _classify_region(cx, cy)
                except Exception:
                    pass

                counter += 1
                file_name = f"embedded_{int(time.time() * 1000)}_{counter}.{ext}"
                file_dest = os.path.join(uploads_dir, file_name)
                with open(file_dest, "wb") as f:
                    f.write(image_bytes)

                page_images.append(
                    EmbeddedImage(
                        pageNumber=page_number,
                        imageUrl=f"{base_url}/{file_name}",
                        buffer=image_bytes,
                        region=region,
                        area=width * height,
                    )
                )

            if page_images:
                results[page_number] = page_images

    return results


@dataclass
class ImagePoolItem:
    id: str
    url: str
    pageNumber: int
    source: str  # "embedded" | "page"


def build_image_pool(
    embedded_by_page: dict[int, list[EmbeddedImage]],
    ocr_pages: list[PageResult],
) -> list[ImagePoolItem]:
    """Flatten every isolated image found on the processed pages (embedded
    product photos plus full-page renders) into one global pool, in page
    order. This is what the frontend's Image Pool UI lets the user drag
    onto any product — independent of whatever auto-assignment already
    happened in the /process route."""
    pool: list[ImagePoolItem] = []
    for page_num in sorted(embedded_by_page.keys()):
        for idx, img in enumerate(embedded_by_page[page_num]):
            pool.append(
                ImagePoolItem(
                    id=f"embedded-{page_num}-{idx}",
                    url=img.imageUrl,
                    pageNumber=page_num,
                    source="embedded",
                )
            )
    for page in ocr_pages:
        pool.append(
            ImagePoolItem(
                id=f"page-{page.pageNumber}",
                url=page.imageUrl,
                pageNumber=page.pageNumber,
                source="page",
            )
        )
    return pool


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def get_pdf_page_count(file_path: str) -> int:
    with fitz.open(file_path) as doc:
        return doc.page_count


def _render_page(page: fitz.Page, scale: float) -> Image.Image:
    matrix = fitz.Matrix(scale, scale)
    pix = page.get_pixmap(matrix=matrix)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def generate_page_thumbnails(
    file_path: str,
    uploads_dir: str,
    base_url: str,
    selected_pages: list[int] | None = None,
) -> list[PageResult]:
    """High quality JPEG thumbnails, scale=2.5 (mirrors pdf-to-img + sharp jpeg q90)."""
    results: list[PageResult] = []
    with fitz.open(file_path) as doc:
        for i, page in enumerate(doc):
            page_number = i + 1
            if selected_pages and page_number not in selected_pages:
                continue

            img = _render_page(page, scale=2.5)
            file_name = f"page_{int(time.time() * 1000)}_{page_number}.jpg"
            file_dest = os.path.join(uploads_dir, file_name)

            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=90)
            data = buf.getvalue()

            with open(file_dest, "wb") as f:
                f.write(data)

            results.append(
                PageResult(
                    pageNumber=page_number,
                    imageUrl=f"{base_url}/{file_name}",
                    buffer=data,
                )
            )
    return results


def generate_ocr_pages(
    file_path: str,
    uploads_dir: str,
    base_url: str,
    selected_pages: list[int],
) -> list[PageResult]:
    """Highest quality lossless PNG pages for OCR accuracy, scale=3.0."""
    results: list[PageResult] = []
    with fitz.open(file_path) as doc:
        for i, page in enumerate(doc):
            page_number = i + 1
            if page_number not in selected_pages:
                continue

            img = _render_page(page, scale=3.0)
            file_name = f"ocr_{int(time.time() * 1000)}_{page_number}.png"
            file_dest = os.path.join(uploads_dir, file_name)

            buf = io.BytesIO()
            img.save(buf, format="PNG")
            data = buf.getvalue()

            with open(file_dest, "wb") as f:
                f.write(data)

            results.append(
                PageResult(
                    pageNumber=page_number,
                    imageUrl=f"{base_url}/{file_name}",
                    buffer=data,
                )
            )
    return results


# Region name -> (left, top, width, height) as fractions of the page,
# mirrors the quadrant map from the original imageExtractor.ts
def _region_bounds(region: str, w: int, h: int) -> tuple[int, int, int, int]:
    h2, h3, w2 = h // 2, h // 3, w // 2
    regions = {
        "top-left": (0, 0, w2, h2),
        "top-right": (w2, 0, w2, h2),
        "top-center": (0, 0, w, h3),
        "center-left": (0, h3, w2, h3),
        "center-right": (w2, h3, w2, h3),
        "center": (0, h3, w, h3),
        "bottom-left": (0, h2, w2, h2),
        "bottom-right": (w2, h2, w2, h2),
        "full": (0, 0, w, h),
    }
    return regions.get(region, regions["top-center"])


def crop_product_image(page_buffer: bytes, region: str, output_path: str) -> None:
    img = Image.open(io.BytesIO(page_buffer)).convert("RGB")
    w, h = img.size
    left, top, box_w, box_h = _region_bounds(region, w, h)
    cropped = img.crop((left, top, left + box_w, top + box_h))
    cropped.save(output_path, format="JPEG", quality=88)