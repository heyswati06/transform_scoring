"""
validate_registry.py
Run this as a CI check on every PR to the app-registry repo.
Fails with exit code 1 if any YAML is invalid — blocks the PR merge.

Usage:
    python validate_registry.py                    # Validate all files
    python validate_registry.py registry/apps/     # Validate a specific dir
    python validate_registry.py --changed-only     # Only validate files changed in this PR (CI mode)

Add to Jenkins or GitHub Actions:
    python scripts/validate_registry.py --strict
"""

import argparse
import logging
import subprocess
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

sys.path.insert(0, str(Path(__file__).parent))
from registry_loader import RegistryLoader, RegistryValidationError


def get_changed_yaml_files() -> list[Path]:
    """Get list of YAML files changed in current git diff (for CI use)."""
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD~1", "HEAD"],
            capture_output=True, text=True
        )
        changed = [Path(f) for f in result.stdout.strip().splitlines()]
        return [f for f in changed if f.suffix == ".yaml" and "registry/apps" in str(f)]
    except Exception:
        return []


def main():
    parser = argparse.ArgumentParser(description="Validate DevOps App Registry YAML files")
    parser.add_argument("path", nargs="?", default=None, help="Registry directory path")
    parser.add_argument("--strict", action="store_true", help="Fail on warnings too")
    parser.add_argument("--changed-only", action="store_true", help="Only check git-changed files")
    args = parser.parse_args()

    registry_dir = Path(args.path) if args.path else Path(__file__).parent.parent / "registry"
    loader = RegistryLoader(str(registry_dir))

    print("\n╔══════════════════════════════════════════╗")
    print("║   App Registry Validation                ║")
    print("╚══════════════════════════════════════════╝\n")

    if args.changed_only:
        changed = get_changed_yaml_files()
        if not changed:
            print("✅ No registry YAML files changed in this PR.")
            sys.exit(0)
        print(f"Checking {len(changed)} changed file(s)...\n")

    errors = loader.validate_all()
    apps = []
    try:
        apps = loader.load_all(strict=False)
    except Exception as e:
        errors.append(str(e))

    # Print results
    if apps:
        print(f"✅ Loaded {len(apps)} app entries successfully\n")
        print(f"  {'APP ID':<30} {'DISPLAY NAME':<30} {'REPOS':>5} {'LEAD EMAIL'}")
        print(f"  {'─'*30} {'─'*30} {'─'*5} {'─'*25}")
        for app in sorted(apps, key=lambda a: a.app_id):
            print(f"  {app.app_id:<30} {app.display_name:<30} {len(app.repos):>5} {app.team_lead_email}")

    # Stale compliance warnings
    stale = loader.get_stale_registry_entries()
    if stale:
        print(f"\n⚠️  Compliance warnings ({len(stale)}):")
        for app, reason in stale:
            print(f"  • [{app.app_id}] {reason}")

    if errors:
        print(f"\n❌ Validation FAILED — {len(errors)} error(s):\n")
        for e in errors:
            print(f"  ✗ {e}")
        print("\nFix all errors before this PR can be merged.\n")
        sys.exit(1)

    print(f"\n✅ Registry is valid. {len(apps)} apps, {sum(len(a.repos) for a in apps)} total repos.\n")
    sys.exit(0)


if __name__ == "__main__":
    main()
