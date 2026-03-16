"""
convert_intake_to_yaml.py
Converts app owner intake responses (YAML or Markdown) into
the final validated registry/apps/{app-id}.yaml format.

Sources it reads from (checks both automatically):
  1. registry/intake/*.yaml   — intake YAMLs filled by champion during calls
  2. registry/intake/*.md     — Markdown intake forms (async fallback)
  3. SharePoint folder        — if configured (reads downloaded CSV/files)

Outputs to:
  registry/apps/{app-id}.yaml  — final registry files, ready for PR

Usage:
  python scripts/convert_intake_to_yaml.py                    # convert all intake files
  python scripts/convert_intake_to_yaml.py payments-api       # convert one app
  python scripts/convert_intake_to_yaml.py --dry-run          # preview without writing

Run this after every batch of interviews. Person 3 runs it.
"""

import argparse
import logging
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

# ── Paths ──────────────────────────────────────────────────────────────────────
ROOT        = Path(__file__).parent.parent
INTAKE_DIR  = ROOT / "registry" / "intake"
APPS_DIR    = ROOT / "registry" / "apps"
SHAREPOINT_DIR = ROOT / "registry" / "intake" / "sharepoint"  # drop SP exports here


# ── Markdown parser ────────────────────────────────────────────────────────────

class MarkdownIntakeParser:
    """
    Parses the _intake_template.md format.
    Handles checkboxes, table rows, and plain text answers.
    """

    def __init__(self, text: str):
        self.text = text
        self.lines = text.splitlines()

    def _get_answer(self, section_header: str, default=None) -> Optional[str]:
        """Find the answer line after a bold question."""
        in_section = False
        for line in self.lines:
            if section_header.lower() in line.lower():
                in_section = True
                continue
            if in_section:
                stripped = line.strip()
                if stripped.startswith("_") and stripped.endswith("_"):
                    return stripped.strip("_").strip()
                if stripped and not stripped.startswith("#") and not stripped.startswith(">"):
                    return stripped
        return default

    def _get_checkbox(self, label: str) -> Optional[bool]:
        """Find a checked checkbox by label text. Returns True/False/None."""
        for line in self.lines:
            if label.lower() in line.lower():
                if "[x]" in line.lower():
                    return True
                if "[ ]" in line.lower():
                    return False
        return None

    def _get_table_repos(self) -> list[dict]:
        """Parse the repos table from Markdown."""
        repos = []
        in_table = False
        for line in self.lines:
            if "Git Org (container)" in line:
                in_table = True
                continue
            if in_table:
                if "|---|" in line or "---" in line:
                    continue
                if line.strip().startswith("|") and "answer" not in line.lower():
                    parts = [p.strip() for p in line.strip().strip("|").split("|")]
                    if len(parts) >= 3 and parts[0] and parts[1]:
                        if parts[0].startswith("_"):
                            continue
                        is_primary = "yes" in parts[3].lower() if len(parts) > 3 else False
                        repos.append({
                            "git_org": parts[0],
                            "repo_name": parts[1],
                            "role": parts[2] if parts[2] else "backend",
                            "is_primary": is_primary,
                        })
                elif line.strip() and not line.strip().startswith("|"):
                    in_table = False
        return repos

    def parse(self) -> dict:
        """Parse full Markdown intake to a dict matching intake YAML structure."""
        data = {}

        # Identity
        data["app_id"] = self._get_answer("App name (short slug", "") or ""
        data["display_name"] = self._get_answer("Display name (for leaderboard", "") or ""
        data["team_lead_email"] = self._get_answer("Your email (team lead", "") or ""
        data["release_champion"] = self._get_answer("Release champion email", "") or ""

        # Classification
        for line in self.lines:
            if "[x]" in line.lower():
                l = line.lower()
                if "modern" in l: data["app_type"] = "modern"
                elif "traditional" in l: data["app_type"] = "traditional"
                elif "legacy" in l: data["app_type"] = "legacy"
                elif "vendor" in l or "iseries" in l: data["app_type"] = "vendor"
                if "tier 1" in l: data["tier"] = 1
                elif "tier 2" in l: data["tier"] = 2
                elif "tier 3" in l: data["tier"] = 3
                if "yes" in l and "in production" not in data:
                    data["in_production"] = True
                if "no" in l and "pre-production" in l:
                    data["in_production"] = False

        data["stack"] = self._get_answer("Primary tech stack", "other") or "other"
        data.setdefault("app_type", "traditional")
        data.setdefault("tier", 2)
        data.setdefault("in_production", True)

        # Repos
        data["repos"] = self._get_table_repos()

        # Pipeline flags
        def yesno(label):
            val = self._get_checkbox(label)
            return bool(val) if val is not None else False

        data["ci_automated"]              = yesno("CI automated")
        data["cd_automated"]              = yesno("CD automated")
        data["standard_pipeline_adopted"] = yesno("org standard pipeline")
        data["git_hygiene_adopted"]       = yesno("Git hygiene formally")
        data["cr_auto_creation"]          = yesno("Change requests auto-created")
        data["zero_touch_deployment"]     = yesno("Zero-touch deployment")
        data["automated_rollback"]        = yesno("Automated rollback")
        data["feature_flags_adopted"]     = yesno("Feature flags adopted")

        gate_str = self._get_answer("many manual human approval gates", "0") or "0"
        try:
            data["approval_gate_count"] = int(re.search(r'\d+', gate_str).group())
        except (AttributeError, ValueError):
            data["approval_gate_count"] = 0

        # Access/security
        data["priv_access_for_deploy"] = not yesno("No — any authorised")
        data["priv_access_reviewed_date"] = self._get_answer("privileged access last formally reviewed", "") or ""
        data["sast_enabled"] = yesno("security scanning")

        for line in self.lines:
            if "[x]" in line.lower():
                l = line.lower()
                if "public" in l and "classification" not in data:
                    data["data_classification"] = "public"
                elif "restricted" in l: data["data_classification"] = "restricted"
                elif "confidential" in l: data["data_classification"] = "confidential"
                elif "internal" in l: data.setdefault("data_classification", "internal")
        data.setdefault("data_classification", "internal")

        # Tooling
        data["datasight_app_name"]   = self._get_answer("appear in DataSight", "") or ""
        data["servicenow_ci_id"]     = self._get_answer("ServiceNow CI ID", "") or ""
        data["jenkins_job_prefix"]   = self._get_answer("Jenkins job prefix", "") or ""
        data["jira_project_key"]     = self._get_answer("Jira project key", "") or ""

        # Compliance
        data["release_page_url"]          = self._get_answer("Release notes page URL", "") or ""
        data["compliance_evidence_page"]  = self._get_answer("Compliance evidence page URL", "") or ""

        # AI
        data["copilot_enabled"] = (
            yesno("Yes — officially") or yesno("Yes — informally")
        )
        ai_tools_raw = self._get_answer("Which AI tools", "") or ""
        data["ai_tools_declared"] = [t.strip() for t in ai_tools_raw.split(",") if t.strip() and t.strip().lower() not in ("none","n/a","no","answer here")]
        data["ai_test_generation"] = yesno("AI for test generation")
        data["apis_published"] = yesno("APIs published in the org API catalog")
        data["catalog_url"]  = self._get_answer("API catalog", "") or ""
        data["api_count"]    = 0

        # Interview notes
        data["biggest_release_blocker"]     = self._get_answer("Biggest blocker", "") or ""
        data["last_release_description"]    = self._get_answer("Last release description", "") or ""
        data["agreed_rf_target_per_month"]  = 0
        rf_raw = self._get_answer("Agreed RF target per month", "0") or "0"
        try:
            data["agreed_rf_target_per_month"] = int(re.search(r'\d+', rf_raw).group())
        except (AttributeError, ValueError):
            pass

        actions = []
        for i, line in enumerate(self.lines):
            if "improvement actions" in line.lower() or "three improvement" in line.lower():
                for j in range(i+1, min(i+10, len(self.lines))):
                    al = self.lines[j].strip()
                    if re.match(r'^\d+\.\s+', al):
                        action = re.sub(r'^\d+\.\s+', '', al).strip("_").strip()
                        if action and action != "answer here":
                            actions.append(action)
                break
        data["improvement_actions"] = actions

        return data


# ── YAML intake parser (just load and clean) ──────────────────────────────────

def parse_yaml_intake(path: Path) -> dict:
    with open(path) as f:
        raw = yaml.safe_load(f)
    if isinstance(raw, list):
        raw = raw[0]
    return raw or {}


# ── Final YAML builder ────────────────────────────────────────────────────────

def build_final_yaml(data: dict) -> dict:
    """
    Converts intake data (from YAML or Markdown) into the final
    registry YAML structure. Applies defaults and normalises values.
    """
    app_id = str(data.get("app_id", "")).strip().lower().replace(" ", "-")
    if not app_id:
        raise ValueError("app_id is required and cannot be blank")

    # Normalise release champion
    champion = data.get("release_champion", "")
    if str(champion).strip().lower() == "same":
        champion = data.get("team_lead_email", "")

    # Normalise repos
    repos_raw = data.get("repos", [])
    repos = []
    primary_set = False
    for r in repos_raw:
        if not r.get("git_org") or not r.get("repo_name"):
            continue
        is_primary = bool(r.get("is_primary", False))
        if is_primary:
            primary_set = True
        repos.append({
            "git_org":    str(r["git_org"]).strip(),
            "repo_name":  str(r["repo_name"]).strip(),
            "role":       str(r.get("role", "backend")).strip(),
            "is_primary": is_primary,
        })
    # Ensure at least one primary
    if repos and not primary_set:
        repos[0]["is_primary"] = True

    # App type and RF exclusion
    app_type = str(data.get("app_type", "traditional")).lower()
    rf_excluded = app_type == "vendor"

    final = {
        "app_id":          app_id,
        "display_name":    str(data.get("display_name", app_id)),
        "app_type":        app_type,
        "rf_excluded":     rf_excluded,  # explicit flag on the registry entry
        "stack":           str(data.get("stack", "other")).lower(),
        "tier":            int(data.get("tier", 2)),
        "in_production":   bool(data.get("in_production", True)),
        "team_lead_email": str(data.get("team_lead_email", "")),
        "release_champion":str(champion),
        "agreed_rf_target_per_month": int(data.get("agreed_rf_target_per_month", 0)),

        "repos": repos,

        "pipeline_flags": {
            "ci_automated":              bool(data.get("ci_automated", False)),
            "cd_automated":              bool(data.get("cd_automated", False)),
            "standard_pipeline_adopted": bool(data.get("standard_pipeline_adopted", False)),
            "git_hygiene_adopted":       bool(data.get("git_hygiene_adopted", False)),
            "cr_auto_creation":          bool(data.get("cr_auto_creation", False)),
            "zero_touch_deployment":     bool(data.get("zero_touch_deployment", False)),
            "automated_rollback":        bool(data.get("automated_rollback", False)),
            "feature_flags_adopted":     bool(data.get("feature_flags_adopted", False)),
            "approval_gate_count":       int(data.get("approval_gate_count", 0)),
        },

        "access_security": {
            "priv_access_for_deploy":     bool(data.get("priv_access_for_deploy", True)),
            "priv_access_reviewed_date":  str(data.get("priv_access_reviewed_date", "")),
            "sast_enabled":               bool(data.get("sast_enabled", False)),
            "data_classification":        str(data.get("data_classification", "internal")),
        },

        "datasight": {
            "app_name":      str(data.get("datasight_app_name", "")),
            "app_id":        str(data.get("datasight_app_id", "")),
        },

        "integrations": {
            "servicenow_ci_id":  str(data.get("servicenow_ci_id", "")),
            "jenkins_job_prefix":str(data.get("jenkins_job_prefix", "")),
            "jira_project_key":  str(data.get("jira_project_key", "")),
            "sonarqube_project": str(data.get("sonarqube_project", "")),
        },

        "compliance": {
            "release_page_url":         str(data.get("release_page_url", "")),
            "compliance_evidence_page": str(data.get("compliance_evidence_page", "")),
        },

        "ai_adoption": {
            "copilot_enabled":    bool(data.get("copilot_enabled", False)),
            "ai_tools_declared":  list(data.get("ai_tools_declared", [])),
            "ai_test_generation": bool(data.get("ai_test_generation", False)),
            "apis_published":     bool(data.get("apis_published", False)),
            "catalog_url":        str(data.get("catalog_url", "")),
            "api_count":          int(data.get("api_count", 0)),
        },

        "interview_notes": {
            "biggest_release_blocker":    str(data.get("biggest_release_blocker", "")),
            "last_release_description":   str(data.get("last_release_description", "")),
            "improvement_actions":        list(data.get("improvement_actions", [])),
            "notes":                      str(data.get("notes", "")),
        },

        "_registry_meta": {
            "intake_source":    "yaml_intake" if "pipeline_flags" not in data else "yaml_direct",
            "converted_at":     datetime.now().strftime("%Y-%m-%d"),
            "converted_by":     "convert_intake_to_yaml.py",
            "review_status":    "pending_champion_review",
        }
    }

    # Vendor app note
    if rf_excluded:
        final["_registry_meta"]["rf_exclusion_reason"] = (
            "app_type=vendor: release schedule controlled externally. "
            "RF excluded from portfolio total. DPI scored on 4 pillars: "
            "Flow, Stability, Automation, AI & Adoption (re-weighted to 100)."
        )

    return final


# ── Discovery ─────────────────────────────────────────────────────────────────

def find_intake_files(target_app: str = None) -> list[tuple[Path, str]]:
    """
    Returns list of (path, format) tuples for all unprocessed intake files.
    format is 'yaml' or 'markdown'.
    """
    found = []
    if not INTAKE_DIR.exists():
        return found

    for path in sorted(INTAKE_DIR.iterdir()):
        if path.name.startswith("_"):  # skip templates
            continue
        if path.suffix in (".yaml", ".yml"):
            found.append((path, "yaml"))
        elif path.suffix == ".md":
            found.append((path, "markdown"))

    # SharePoint fallback
    if SHAREPOINT_DIR.exists():
        for path in sorted(SHAREPOINT_DIR.iterdir()):
            if path.suffix in (".yaml", ".yml"):
                found.append((path, "yaml"))
            elif path.suffix == ".md":
                found.append((path, "markdown"))

    if target_app:
        found = [f for f in found if target_app.lower() in f[0].stem.lower()]

    return found


# ── Main ──────────────────────────────────────────────────────────────────────

def process_file(path: Path, fmt: str, dry_run: bool = False) -> Optional[str]:
    """Process one intake file. Returns app_id if successful, None if failed."""
    try:
        if fmt == "yaml":
            raw = parse_yaml_intake(path)
        else:
            with open(path) as f:
                text = f.read()
            raw = MarkdownIntakeParser(text).parse()

        final = build_final_yaml(raw)
        app_id = final["app_id"]

        if not app_id:
            logger.error(f"{path.name}: app_id is empty — skipping")
            return None

        output_path = APPS_DIR / f"{app_id}.yaml"

        if dry_run:
            print(f"\n{'─'*50}")
            print(f"DRY RUN — Would write: {output_path}")
            print(yaml.dump([final], default_flow_style=False, sort_keys=False, allow_unicode=True))
            return app_id

        APPS_DIR.mkdir(parents=True, exist_ok=True)

        # Don't overwrite a manually curated final file without warning
        if output_path.exists():
            existing = yaml.safe_load(output_path.read_text())
            if isinstance(existing, list): existing = existing[0]
            if existing and existing.get("_registry_meta", {}).get("review_status") == "approved":
                logger.warning(f"{app_id}: Final file already approved — skipping overwrite. Delete {output_path} to reconvert.")
                return None

        with open(output_path, "w") as f:
            yaml.dump([final], f, default_flow_style=False, sort_keys=False,
                      allow_unicode=True, width=120)

        logger.info(f"✅ {app_id} → {output_path.name}")

        # Move intake file to processed/
        processed_dir = INTAKE_DIR / "processed"
        processed_dir.mkdir(exist_ok=True)
        path.rename(processed_dir / path.name)

        return app_id

    except Exception as e:
        logger.error(f"❌ {path.name}: {e}")
        return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Convert intake files to registry YAMLs")
    parser.add_argument("app", nargs="?", help="Convert only this app (partial name match)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing files")
    args = parser.parse_args()

    files = find_intake_files(target_app=args.app)
    if not files:
        print("No intake files found in registry/intake/")
        print("Expected: registry/intake/*.yaml or registry/intake/*.md")
        sys.exit(0)

    print(f"\nFound {len(files)} intake file(s) to process...\n")
    successes, failures = [], []
    for path, fmt in files:
        result = process_file(path, fmt, dry_run=args.dry_run)
        if result:
            successes.append(result)
        else:
            failures.append(path.name)

    print(f"\n{'─'*50}")
    print(f"  Converted : {len(successes)} apps → registry/apps/")
    if successes:
        print("  App IDs   : " + ", ".join(successes))
    if failures:
        print(f"  Errors    : {len(failures)}")
        for f in failures:
            print(f"    • {f}")
    print(f"\n  Next step : git add registry/apps/ && git commit -m 'feat: add registry entries for {len(successes)} apps'")
    print(f"  Then      : python scripts/validate_registry.py to check all files\n")
