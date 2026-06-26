"""Fake S3Client for local/CI testing.

The dashboard uses S3 only for the Prisma scan-report viewer. The fake reports
"object not found" for every key, so the viewer shows its graceful empty state
instead of crashing. Drop real text under localdev/fixtures/s3/<key> to serve
canned reports if you want to exercise the viewer.
"""

from __future__ import annotations

import os

_S3_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "fixtures", "s3")


class S3Client:
    def __init__(self, *args, **kwargs):
        pass

    def get_object(self, bucket: str = "", key: str = "", **kwargs):
        path = os.path.join(_S3_DIR, key.lstrip("/"))
        if os.path.isfile(path):
            with open(path, "rb") as fh:
                return fh.read()
        raise FileNotFoundError(f"localdev fake S3: no object {bucket}/{key}")

    def get_object_text(self, bucket: str = "", key: str = "", **kwargs) -> str:
        return self.get_object(bucket, key).decode("utf-8", "replace")

    def list_objects(self, *args, **kwargs):
        return []
