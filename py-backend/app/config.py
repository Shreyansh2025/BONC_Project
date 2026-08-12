import os

UPLOADS_DIR = os.getenv("UPLOADS_DIR") or os.path.join(os.getcwd(), "uploads")
