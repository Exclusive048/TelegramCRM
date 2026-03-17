from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _run_python_import_check(script: str) -> subprocess.CompletedProcess[str]:
    project_root = Path(__file__).resolve().parents[1]
    return subprocess.run(
        [sys.executable, "-X", "utf8", "-c", script],
        cwd=project_root,
        check=False,
        capture_output=True,
        text=True,
    )


def test_leads_route_import_and_schema_annotations_resolve() -> None:
    result = _run_python_import_check(
        """
import inspect
from slowapi.extension import Limiter

import app.api.rate_limit as rate_limit
import app.api.routes.leads as leads
from app.api.schemas.lead_schemas import LeadCommentRequest, LeadCreateRequest, LeadUpdateRequest

assert isinstance(rate_limit.limiter, Limiter)
assert inspect.signature(leads.create_lead).parameters["body"].annotation is LeadCreateRequest
assert inspect.signature(leads.update_lead).parameters["body"].annotation is LeadUpdateRequest
assert inspect.signature(leads.add_comment).parameters["body"].annotation is LeadCommentRequest
"""
    )

    assert result.returncode == 0, (
        "Expected app.api.routes.leads import/type resolution to succeed.\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
