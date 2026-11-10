"""Automated tests checking Dockerfile structure, entrypoint execution, and packaging."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent


def test_dockerfile_static_structure() -> None:
    """Verifies Dockerfile meets all packaging, security, and footprint requirements."""
    dockerfile_path = REPO_ROOT / "Dockerfile"
    assert dockerfile_path.is_file(), "Dockerfile is missing"

    content = dockerfile_path.read_text(encoding="utf-8")

    # Base image
    assert "alpine:3.21" in content, "Dockerfile must use Alpine 3.21 base"

    # Minimal packages
    for pkg in ["python3", "ca-certificates", "curl", "tzdata"]:
        assert pkg in content, f"Missing required package {pkg} in Dockerfile"

    # Security: non-root appuser
    assert "adduser -D -u 1000" in content or "useradd" in content
    assert "USER appuser" in content, "Dockerfile must drop root privileges and run as appuser"

    # Embedded binary and entrypoint
    assert "bin/xray-checker" in content
    assert "/usr/bin/xray-checker" in content
    assert "ENTRYPOINT" in content
    assert "entrypoint.sh" in content


def test_entrypoint_script_structure() -> None:
    """Verifies entrypoint.sh properly handles exec and CLI forwarding."""
    entrypoint_path = REPO_ROOT / "entrypoint.sh"
    assert entrypoint_path.is_file(), "entrypoint.sh is missing"

    content = entrypoint_path.read_text(encoding="utf-8")
    assert "exec python3 -m checker" in content, "entrypoint.sh must exec python3 -m checker"


def test_dockerignore_rules() -> None:
    """.dockerignore must exclude git, tests, and caches from container context."""
    dockerignore_path = REPO_ROOT / ".dockerignore"
    assert dockerignore_path.is_file(), ".dockerignore is missing"

    content = dockerignore_path.read_text(encoding="utf-8")
    for pattern in [".git", "tests/", "__pycache__"]:
        assert pattern in content, f".dockerignore must ignore {pattern}"


def test_embedded_binary_exists() -> None:
    """Verifies that the compiled Go ELF binary is properly embedded in bin/."""
    bin_path = REPO_ROOT / "bin" / "xray-checker"
    assert bin_path.is_file(), "bin/xray-checker is missing"
    assert bin_path.stat().st_size > 30 * 1024 * 1024, "bin/xray-checker file size is smaller than expected"


def is_docker_available() -> bool:
    """Check if Docker CLI and daemon are reachable."""
    if not shutil.which("docker"):
        return False
    try:
        res = subprocess.run(
            ["docker", "info"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
        return res.returncode == 0
    except Exception:
        return False


@pytest.mark.skipif(not is_docker_available(), reason="Docker daemon is not reachable")
class TestDockerLiveBuild:
    IMAGE_TAG = "dinovoyager-checker:test"

    @classmethod
    def setup_class(cls):
        cmd = ["docker", "build", "-t", cls.IMAGE_TAG, str(REPO_ROOT)]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        assert res.returncode == 0, f"Docker build failed: {res.stderr}"

    def test_container_image_size(self):
        cmd = ["docker", "image", "inspect", self.IMAGE_TAG, "--format", "{{.Size}}"]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        size_bytes = int(res.stdout.strip())
        max_bytes = 105 * 1024 * 1024  # 105 MB tolerance (<100MB target)
        assert size_bytes < max_bytes, f"Image size {size_bytes / (1024*1024):.1f}MB exceeds limit"

    def test_help_flag_exit_code_0(self):
        cmd = ["docker", "run", "--rm", self.IMAGE_TAG, "--help"]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        assert res.returncode == 0
        assert "--subscription-url" in res.stdout
