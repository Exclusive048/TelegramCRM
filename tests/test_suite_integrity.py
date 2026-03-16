from pathlib import Path
import re


TESTS_DIR = Path("tests")
MIN_TEST_FILES = 8
MIN_TEST_CASES = 20

REQUIRED_BASELINE_FILES = {
    "test_setup_tenant_selection.py",
    "test_setup_flow_ordering.py",
    "test_panel_tenant_isolation.py",
    "test_api_key_scope_deps.py",
    "test_leads_scope_matrix.py",
    "test_lead_quota_atomicity.py",
}


def _test_files() -> list[Path]:
    return sorted(TESTS_DIR.glob("test_*.py"))


def _count_test_functions(path: Path) -> int:
    source = path.read_text(encoding="utf-8")
    return len(re.findall(r"^\s*(?:async\s+def|def)\s+test_", source, flags=re.M))


def test_required_pre_release_baseline_files_exist() -> None:
    existing = {path.name for path in _test_files()}
    missing = REQUIRED_BASELINE_FILES - existing
    assert not missing, f"Missing baseline test groups: {sorted(missing)}"


def test_suite_is_not_empty_and_not_trivially_small() -> None:
    files = _test_files()
    assert len(files) >= MIN_TEST_FILES

    total_cases = sum(_count_test_functions(path) for path in files)
    assert total_cases >= MIN_TEST_CASES
