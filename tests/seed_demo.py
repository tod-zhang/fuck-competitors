"""Populate the database with demo data so the UI can be exercised / screenshotted.

Writes to the configured FC_DB_URL (default ./data/app.db). Resets all tables first.
Run:  .venv/bin/python tests/seed_demo.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.demo import reseed  # noqa: E402

if __name__ == "__main__":
    reseed()
    print("seeded demo data")
