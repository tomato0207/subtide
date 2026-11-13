"""Download ECDICT (English-Chinese dictionary, MIT) and build backend/data/ecdict.db for the hover dictionary."""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from backend.services import dictionary_service as d  # noqa: E402


def main():
    if os.path.exists(d.DB_PATH):
        print(f"Dictionary already installed: {d.DB_PATH}")
        return
    if not os.path.exists(d.CSV_PATH):
        print("Downloading ECDICT (~66 MB) ...")
        d.download_csv()
    print("Building dictionary database ...")
    print(f"Done: {d.build_database()} entries")


if __name__ == '__main__':
    main()
