"""Shipped eval-proxy fixtures must resolve from the installed package path."""
from pathlib import Path

import memor


def test_packaged_proxy_benchmark_fixtures_exist():
    fixtures = Path(memor.__file__).resolve().parent / "eval" / "proxy_benchmark_fixtures"
    assert fixtures.is_dir(), f"missing packaged fixtures at {fixtures}"
    json_files = list(fixtures.glob("*.json")
                      )
    assert len(json_files) >= 5
