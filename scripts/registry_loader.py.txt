"""
registry_loader.py
Loads and validates the entire App Registry from YAML files.
This is the single entry point for all automation — every script
imports this instead of hardcoding repo lists.

Usage:
    from registry_loader import RegistryLoader

    loader = RegistryLoader("/path/to/registry")
    apps = loader.load_all()              # List[AppEntry]
    app = loader.get_app("payments-gw")  # AppEntry | None
    repos = loader.get_all_repos()       # List of (org, repo, app_id, is_primary)
"""

import os
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

REQUIRED_FIELDS = ["app_id", "display_name", "service_line", "tier",
                   "team_lead_email", "release_champion", "stack", "repos"]
VALID_STACKS     = {"java-spring", "dotnet", "node", "python", "mixed", "other"}
VALID_ROLES      = {"backend", "frontend", "infra", "shared-lib", "config"}
VALID_CI_TOOLS   = {"jenkins", "github-actions", "azure-devops", "gitlab-ci", "none"}
VALID_ENVS       = {"prod", "staging", "dev-only"}
VALID_TIERS      = {1, 2, 3}


@dataclass
class RepoEntry:
    name: str
    org: str
    role: str
    is_primary: bool


@dataclass
class AppEntry:
    # Core
    app_id:           str
    display_name:     str
    service_line:     str
    tier:             int
    team_lead_email:  str
    release_champion: str
    stack:            str
    repos:            list[RepoEntry] = field(default_factory=list)

    # Integrations
    jira_project_key:    str = ""
    servicenow_ci_id:    str = ""
    jenkins_job_prefix:  str = ""
    sonarqube_project:   str = ""

    # Pipeline
    ci_tool:                str  = "jenkins"
    cd_enabled:             bool = False
    zero_touch_deployment:  bool = False
    cr_auto_creation:       bool = False
    feature_flags_adopted:  bool = False
    pipeline_standard:      str  = "v1"

    # Compliance
    release_page_url:           str = ""
    compliance_evidence_page:   str = ""
    priv_access_reviewed_date:  str = ""
    data_classification:        str = "internal"

    # API catalog
    apis_published: bool = False
    catalog_url:    str  = ""
    api_count:      int  = 0

    # AI adoption
    copilot_enabled:    bool       = False
    ai_tools_declared:  list[str]  = field(default_factory=list)

    # Production status
    in_production: bool = True
    environment:   str  = "prod"

    @property
    def primary_repo(self) -> Optional[RepoEntry]:
        return next((r for r in self.repos if r.is_primary), self.repos[0] if self.repos else None)

    @property
    def is_priv_access_current(self) -> bool:
        """Returns True if privileged access was reviewed in the last 90 days."""
        if not self.priv_access_reviewed_date:
            return False
        try:
            reviewed = datetime.strptime(self.priv_access_reviewed_date, "%Y-%m-%d")
            reviewed = reviewed.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - reviewed) < timedelta(days=90)
        except ValueError:
            return False

    @property
    def has_release_page(self) -> bool:
        return bool(self.release_page_url.strip())

    @property
    def has_compliance_evidence(self) -> bool:
        return bool(self.compliance_evidence_page.strip())


class RegistryValidationError(Exception):
    pass


class RegistryLoader:
    """
    Loads all app YAML files from the registry directory.
    Validates schema on load and raises clear errors for missing fields.
    """

    def __init__(self, registry_dir: str = None):
        if registry_dir is None:
            # Default: look for registry/ relative to this script
            registry_dir = Path(__file__).parent.parent / "registry"
        self.registry_dir = Path(registry_dir)
        self._apps: dict[str, AppEntry] = {}
        self._loaded = False

    def load_all(self, strict: bool = False) -> list[AppEntry]:
        """
        Load and validate all app YAML files.

        Args:
            strict: If True, raise on any validation error.
                    If False, log errors and skip invalid entries.
        Returns:
            List of validated AppEntry objects.
        """
        apps_dir = self.registry_dir / "apps"
        if not apps_dir.exists():
            raise FileNotFoundError(f"Registry apps directory not found: {apps_dir}")

        yaml_files = sorted(apps_dir.glob("*.yaml"))
        # Skip template file
        yaml_files = [f for f in yaml_files if not f.name.startswith("_")]

        if not yaml_files:
            logger.warning(f"No app YAML files found in {apps_dir}")
            return []

        loaded = []
        errors = []

        for yaml_file in yaml_files:
            try:
                entries = self._load_file(yaml_file)
                for entry in entries:
                    if entry.app_id in self._apps:
                        raise RegistryValidationError(
                            f"Duplicate app_id '{entry.app_id}' in {yaml_file.name}"
                        )
                    self._apps[entry.app_id] = entry
                    loaded.append(entry)
            except Exception as e:
                msg = f"[{yaml_file.name}] {e}"
                errors.append(msg)
                logger.error(f"Registry error: {msg}")
                if strict:
                    raise RegistryValidationError(msg) from e

        self._loaded = True
        logger.info(f"Registry loaded: {len(loaded)} apps from {len(yaml_files)} files"
                    + (f" ({len(errors)} errors)" if errors else ""))

        if errors:
            logger.warning("Registry errors summary:\n" + "\n".join(f"  • {e}" for e in errors))

        return loaded

    def _load_file(self, path: Path) -> list[AppEntry]:
        """Parse and validate a single YAML file. Returns list of AppEntry."""
        with open(path) as f:
            data = yaml.safe_load(f)

        if data is None:
            raise RegistryValidationError("Empty file")

        # Support both single dict and list of dicts
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list):
            raise RegistryValidationError("YAML must be a mapping or list of mappings")

        return [self._parse_entry(item, path.name) for item in data]

    def _parse_entry(self, data: dict, filename: str) -> AppEntry:
        """Validate and parse one app entry from raw dict."""
        # Required fields
        missing = [f for f in REQUIRED_FIELDS if f not in data or data[f] is None]
        if missing:
            raise RegistryValidationError(f"Missing required fields: {missing}")

        app_id = str(data["app_id"]).strip()
        if not app_id or " " in app_id:
            raise RegistryValidationError(f"app_id must be non-empty with no spaces: '{app_id}'")

        tier = int(data.get("tier", 2))
        if tier not in VALID_TIERS:
            raise RegistryValidationError(f"tier must be 1, 2, or 3 (got {tier})")

        stack = str(data.get("stack", "other")).lower()

        # Parse repos
        repos_raw = data.get("repos", [])
        if not repos_raw:
            raise RegistryValidationError("At least one repo is required")

        repos = []
        primary_count = 0
        for i, r in enumerate(repos_raw):
            if not r.get("name") or not r.get("org"):
                raise RegistryValidationError(f"Repo {i}: 'name' and 'org' are required")
            role = r.get("role", "backend")
            if role not in VALID_ROLES:
                logger.warning(f"{app_id}: repo role '{role}' not in {VALID_ROLES}, using 'backend'")
                role = "backend"
            is_primary = bool(r.get("is_primary", False))
            if is_primary:
                primary_count += 1
            repos.append(RepoEntry(
                name=str(r["name"]),
                org=str(r["org"]),
                role=role,
                is_primary=is_primary,
            ))

        if primary_count == 0:
            logger.warning(f"{app_id}: No primary repo set — defaulting to first repo")
            repos[0] = RepoEntry(repos[0].name, repos[0].org, repos[0].role, True)
        elif primary_count > 1:
            raise RegistryValidationError(f"Only one repo can be is_primary=true (found {primary_count})")

        # Nested sections with defaults
        intg = data.get("integrations", {}) or {}
        pipe = data.get("pipeline_config", {}) or {}
        comp = data.get("compliance", {}) or {}
        apic = data.get("api_catalog", {}) or {}
        ai   = data.get("ai_adoption", {}) or {}
        prod = data.get("production_status", {}) or {}

        ci_tool = str(pipe.get("ci_tool", "jenkins")).lower()
        if ci_tool not in VALID_CI_TOOLS:
            logger.warning(f"{app_id}: ci_tool '{ci_tool}' unknown, using 'jenkins'")
            ci_tool = "jenkins"

        return AppEntry(
            app_id=app_id,
            display_name=str(data.get("display_name", app_id)),
            service_line=str(data.get("service_line", "")),
            tier=tier,
            team_lead_email=str(data.get("team_lead_email", "")),
            release_champion=str(data.get("release_champion", "")),
            stack=stack,
            repos=repos,
            # integrations
            jira_project_key=str(intg.get("jira_project_key", "")),
            servicenow_ci_id=str(intg.get("servicenow_ci_id", "")),
            jenkins_job_prefix=str(intg.get("jenkins_job_prefix", "")),
            sonarqube_project=str(intg.get("sonarqube_project", "")),
            # pipeline
            ci_tool=ci_tool,
            cd_enabled=bool(pipe.get("cd_enabled", False)),
            zero_touch_deployment=bool(pipe.get("zero_touch_deployment", False)),
            cr_auto_creation=bool(pipe.get("cr_auto_creation", False)),
            feature_flags_adopted=bool(pipe.get("feature_flags_adopted", False)),
            pipeline_standard=str(pipe.get("pipeline_standard", "v1")),
            # compliance
            release_page_url=str(comp.get("release_page_url", "")),
            compliance_evidence_page=str(comp.get("compliance_evidence_page", "")),
            priv_access_reviewed_date=str(comp.get("priv_access_reviewed_date", "")),
            data_classification=str(comp.get("data_classification", "internal")),
            # api
            apis_published=bool(apic.get("apis_published", False)),
            catalog_url=str(apic.get("catalog_url", "")),
            api_count=int(apic.get("api_count", 0)),
            # ai
            copilot_enabled=bool(ai.get("copilot_enabled", False)),
            ai_tools_declared=list(ai.get("ai_tools_declared", [])),
            # production
            in_production=bool(prod.get("in_production", True)),
            environment=str(prod.get("environment", "prod")),
        )

    def get_app(self, app_id: str) -> Optional[AppEntry]:
        if not self._loaded:
            self.load_all()
        return self._apps.get(app_id)

    def get_all_repos(self) -> list[tuple[str, str, str, bool]]:
        """
        Returns flat list of all repos across all apps.
        Each tuple: (org, repo_name, app_id, is_primary)
        Useful for running GitHub hygiene checks against all repos.
        """
        if not self._loaded:
            self.load_all()
        result = []
        for app in self._apps.values():
            for repo in app.repos:
                result.append((repo.org, repo.name, app.app_id, repo.is_primary))
        return result

    def get_apps_by_service_line(self, service_line: str) -> list[AppEntry]:
        if not self._loaded:
            self.load_all()
        return [a for a in self._apps.values() if a.service_line == service_line]

    def get_apps_by_tier(self, tier: int) -> list[AppEntry]:
        if not self._loaded:
            self.load_all()
        return [a for a in self._apps.values() if a.tier == tier]

    def get_stale_registry_entries(self, stale_days: int = 90) -> list[tuple[AppEntry, str]]:
        """
        Returns apps with stale compliance fields.
        Returns list of (AppEntry, reason) tuples.
        """
        if not self._loaded:
            self.load_all()
        stale = []
        for app in self._apps.values():
            if not app.is_priv_access_current:
                reason = ("never reviewed" if not app.priv_access_reviewed_date
                          else f"last reviewed {app.priv_access_reviewed_date} (>90 days ago)")
                stale.append((app, f"Privileged access {reason}"))
            if not app.has_release_page:
                stale.append((app, "No release page URL declared"))
            if app.tier == 1 and not app.has_compliance_evidence:
                stale.append((app, "Tier-1 app missing compliance evidence URL"))
        return stale

    def validate_all(self) -> list[str]:
        """
        Run full validation. Returns list of error/warning strings.
        Empty list = fully valid registry.
        """
        errors = []
        apps_dir = self.registry_dir / "apps"
        if not apps_dir.exists():
            return [f"Registry directory not found: {apps_dir}"]

        for yaml_file in sorted(apps_dir.glob("*.yaml")):
            if yaml_file.name.startswith("_"):
                continue
            try:
                self._load_file(yaml_file)
            except Exception as e:
                errors.append(f"{yaml_file.name}: {e}")

        # Check for duplicate app_ids across files
        seen_ids: dict[str, str] = {}
        for yaml_file in sorted(apps_dir.glob("*.yaml")):
            if yaml_file.name.startswith("_"):
                continue
            try:
                with open(yaml_file) as f:
                    data = yaml.safe_load(f)
                if isinstance(data, dict):
                    data = [data]
                for item in (data or []):
                    aid = str(item.get("app_id", ""))
                    if aid in seen_ids:
                        errors.append(f"Duplicate app_id '{aid}' in {yaml_file.name} and {seen_ids[aid]}")
                    else:
                        seen_ids[aid] = yaml_file.name
            except Exception:
                pass

        return errors


# ── CLI: validate registry ────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    registry_path = sys.argv[1] if len(sys.argv) > 1 else None
    loader = RegistryLoader(registry_path)

    print("\n── Registry Validation ──────────────────────────────")
    errors = loader.validate_all()
    if errors:
        print(f"\n❌ {len(errors)} validation error(s):")
        for e in errors:
            print(f"   • {e}")
        sys.exit(1)

    apps = loader.load_all()
    print(f"\n✅ Registry valid: {len(apps)} apps loaded")

    repos = loader.get_all_repos()
    print(f"   Total repos tracked: {len(repos)}")

    stale = loader.get_stale_registry_entries()
    if stale:
        print(f"\n⚠️  {len(stale)} stale compliance entries:")
        for app, reason in stale[:10]:
            print(f"   • [{app.app_id}] {reason}")
        if len(stale) > 10:
            print(f"   ... and {len(stale)-10} more")

    print()
    sys.exit(0)
