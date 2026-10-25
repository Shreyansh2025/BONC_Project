import asyncio
import io
import os
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor

import pytesseract
from PIL import Image

from app.shared.logger import logger

WORKER_COUNT = int(os.getenv("OCR_WORKER_COUNT", "4"))


_explicit_cmd = os.getenv("TESSERACT_CMD")
if _explicit_cmd:
    pytesseract.pytesseract.tesseract_cmd = _explicit_cmd
elif sys.platform.startswith("win") and not shutil.which("tesseract"):
    default_win_path = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    if os.path.exists(default_win_path):
        pytesseract.pytesseract.tesseract_cmd = default_win_path

_executor: ThreadPoolExecutor | None = None


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=WORKER_COUNT)
        logger.info(f"OCR pool ready (workers={WORKER_COUNT})")
    return _executor


def _recognize_sync(buffer: bytes) -> str:
    image = Image.open(io.BytesIO(buffer))
    return pytesseract.image_to_string(image, lang="eng")


async def recognize_batch(
    pages: list[tuple[int, bytes]]
) -> list[dict[str, int | str]]:
    """OCR pages concurrently, preserving successful results if one page fails.

    A single damaged/unsupported page should not discard OCR text already
    extracted from other pages. If every page fails, raise a safe, stable error
    so the route does not save an empty brochure as if processing succeeded.
    """
    if not pages:
        raise RuntimeError("No pages available for OCR")

    loop = asyncio.get_running_loop()
    executor = _get_executor()

    async def run_one(page_number: int, buffer: bytes) -> dict[str, int | str]:
        text = await loop.run_in_executor(executor, _recognize_sync, buffer)
        return {"pageNumber": page_number, "text": text}

    outcomes = await asyncio.gather(
        *(run_one(page_number, buffer) for page_number, buffer in pages),
        return_exceptions=True,
    )
    successful: list[dict[str, int | str]] = []
    for (page_number, _), outcome in zip(pages, outcomes):
        if isinstance(outcome, asyncio.CancelledError):
            raise outcome
        if isinstance(outcome, Exception):
            # Log the page and error class, not potentially sensitive OCR input
            # or large/native-library exception details.
            logger.warning(
                "OCR failed for page %s (%s)", page_number, type(outcome).__name__
            )
            continue
        if isinstance(outcome, BaseException):
            raise outcome
        successful.append(outcome)

    if not successful:
        raise RuntimeError(f"OCR failed for all {len(pages)} page(s)")

    successful.sort(key=lambda result: int(result["pageNumber"]))
    if len(successful) < len(pages):
        logger.warning(
            "OCR completed with partial results: %s/%s pages succeeded",
            len(successful), len(pages),
        )
    return successful


def warm_up() -> None:
    _get_executor()


def terminate_pool() -> None:
    global _executor
    if _executor is not None:
        _executor.shutdown(wait=False)
        _executor = None
