"""One-off backfill of analysis_images.image_width/image_height.

Downloads each image and measures it with cv2.imdecode(IMREAD_COLOR), the same
call the identifier uses (it applies EXIF orientation), so the size matches the
pixel space of bbox_xyxy. Needs opencv-python, httpx and psycopg2 (the
identifier's venv has cv2).

    python scripts/backfill_image_dimensions.py 121 162          # dry run
    python scripts/backfill_image_dimensions.py 121 162 --apply  # write

Only rows with NULL dimensions are touched. Reads DATABASE_URL from env or .env.
"""
import argparse
import os
import sys

import cv2
import httpx
import numpy as np
import psycopg2
from dotenv import load_dotenv


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("record_ids", nargs="+", type=int)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    load_dotenv()
    url = os.environ["DATABASE_URL"].replace("postgresql+psycopg2://", "postgresql://", 1)
    conn = psycopg2.connect(url)
    failures = 0
    with conn, conn.cursor() as cur, httpx.Client(timeout=60, follow_redirects=True) as client:
        cur.execute(
            "SELECT id, record_id, image_index, image_url FROM analysis_images "
            "WHERE record_id = ANY(%s) AND (image_width IS NULL OR image_height IS NULL) "
            "ORDER BY record_id, image_index",
            (args.record_ids,),
        )
        for row_id, record_id, index, image_url in cur.fetchall():
            try:
                response = client.get(image_url)
                response.raise_for_status()
                image = cv2.imdecode(np.frombuffer(response.content, np.uint8), cv2.IMREAD_COLOR)
                if image is None:
                    raise ValueError("could not decode image")
                height, width = image.shape[:2]
            except Exception as exc:
                failures += 1
                print(f"record {record_id} image {index}: FAILED ({exc})")
                continue
            print(f"record {record_id} image {index}: {width}x{height}")
            if args.apply:
                cur.execute(
                    "UPDATE analysis_images SET image_width=%s, image_height=%s WHERE id=%s",
                    (width, height, row_id),
                )
    if not args.apply:
        print("dry run: nothing written (use --apply)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
