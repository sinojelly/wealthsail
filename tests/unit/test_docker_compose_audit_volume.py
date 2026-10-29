"""Test Docker compose volume persistence contract for research audit data."""

from pathlib import Path
import yaml


def test_docker_compose_defines_and_mounts_research_audit_volume():
    root = Path(__file__).resolve().parents[2]
    compose_file = root / "docker-compose.yml"
    assert compose_file.exists(), "docker-compose.yml must exist"

    data = yaml.safe_load(compose_file.read_text(encoding="utf-8"))
    worker = data.get("services", {}).get("worker", {})
    volumes = worker.get("volumes", [])
    assert "research_audit_data:/app/data/research-audit" in volumes, (
        "worker service must mount research_audit_data to /app/data/research-audit"
    )
    assert "research_audit_data" in data.get("volumes", {}), (
        "research_audit_data must be defined in top-level volumes"
    )


def test_nas_storage_overlay_defines_research_audit_volume():
    root = Path(__file__).resolve().parents[2]
    storage_file = root / "external-access" / "compose.wealthsail.storage.yaml"
    assert storage_file.exists(), "compose.wealthsail.storage.yaml must exist"

    data = yaml.safe_load(storage_file.read_text(encoding="utf-8"))
    vol = data.get("volumes", {}).get("research_audit_data", {})
    assert vol.get("driver") == "local"
    opts = vol.get("driver_opts", {})
    assert opts.get("type") == "none"
    assert opts.get("o") == "bind"
    assert "research-audit" in str(opts.get("device", ""))
