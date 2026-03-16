"""
score_engine.py
Computes the DevOps Score (0–100) for each app by aggregating:
  - Git hygiene scores from hygiene_checker.py output
  - Release frequency & LTFD from GitHub
  - Pipeline maturity & compliance flags from the registry
  - Code quality from SonarQube (if configured)

Score pillars and weights:
  Release Velocity   30%
  Git Hygiene        20%
  Pipeline Maturity  20%
  Compliance         15%
  Quality & Security 10%
  Adoption            5%

Usage:
    python score_engine.py                        # Score all apps
    python score_engine.py --app payments-gw      # Score one app
    python score_engine.py --output scores.json   # Custom output path
"""

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import yaml

from registry_loader import RegistryLoader, AppEntry

logger = logging.getLogger(__name__)


# ── Score weights (must sum to 100) ──────────────────────────────────────────

WEIGHTS = {
    "release_velocity":  30,
    "git_hygiene":       20,
    "pipeline_maturity": 20,
    "compliance":        15,
    "quality_security":  10,
    "adoption":           5,
}
assert sum(WEIGHTS.values()) == 100, "Weights must sum to 100"


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class PillarScore:
    name: str
    weight: int
    raw_score: float       # 0–100 within this pillar
    weighted_score: float  # raw_score * weight / 100
    breakdown: dict = field(default_factory=dict)
    missing_data: list[str] = field(default_factory=list)


@dataclass
class AppDevOpsScore:
    app_id:       str
    display_name: str
    service_line: str
    tier:         int
    total_score:  float
    pillars:      list[PillarScore]
    repo_count:   int
    scored_at:    str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    prev_score:   Optional[float] = None
    delta:        Optional[float] = None

    @property
    def grade(self) -> str:
        if self.total_score >= 85: return "A"
        if self.total_score >= 70: return "B"
        if self.total_score >= 55: return "C"
        if self.total_score >= 40: return "D"
        return "F"

    @property
    def trend(self) -> str:
        if self.delta is None: return "—"
        if self.delta > 2:  return "↑"
        if self.delta < -2: return "↓"
        return "→"


# ── Score engine ──────────────────────────────────────────────────────────────

class ScoreEngine:

    def __init__(self, config_path: str = "../config/settings.yaml",
                 registry_dir: str = None):
        with open(config_path) as f:
            self.cfg = yaml.safe_load(f)

        self.loader = RegistryLoader(registry_dir)
        self.apps = {a.app_id: a for a in self.loader.load_all()}

        # Load latest hygiene results (produced by hygiene_checker.py)
        hygiene_path = Path(self.cfg.get("reporting", {}).get("output_dir", "../reports")) / "latest_hygiene.json"
        self.hygiene_by_repo: dict[str, dict] = {}
        if hygiene_path.exists():
            with open(hygiene_path) as f:
                raw = json.load(f)
            for item in raw:
                self.hygiene_by_repo[item["repo"]] = item
        else:
            logger.warning(f"No hygiene results found at {hygiene_path}. Run hygiene_checker.py first.")

        # Load previous scores for delta calculation
        scores_path = Path(self.cfg.get("reporting", {}).get("output_dir", "../reports")) / "latest_scores.json"
        self.prev_scores: dict[str, float] = {}
        if scores_path.exists():
            with open(scores_path) as f:
                prev = json.load(f)
            for item in prev:
                self.prev_scores[item["app_id"]] = item["total_score"]

    def score_all(self) -> list[AppDevOpsScore]:
        results = []
        for app in self.apps.values():
            try:
                score = self.score_app(app)
                results.append(score)
                logger.info(f"  [{app.app_id}] {score.total_score:.1f}/100 ({score.grade}) {score.trend}")
            except Exception as e:
                logger.error(f"Error scoring {app.app_id}: {e}")
        return sorted(results, key=lambda x: x.total_score, reverse=True)

    def score_app(self, app: AppEntry) -> AppDevOpsScore:
        pillars = [
            self._score_release_velocity(app),
            self._score_git_hygiene(app),
            self._score_pipeline_maturity(app),
            self._score_compliance(app),
            self._score_quality_security(app),
            self._score_adoption(app),
        ]

        total = round(sum(p.weighted_score for p in pillars), 1)
        prev = self.prev_scores.get(app.app_id)
        delta = round(total - prev, 1) if prev is not None else None

        return AppDevOpsScore(
            app_id=app.app_id,
            display_name=app.display_name,
            service_line=app.service_line,
            tier=app.tier,
            total_score=total,
            pillars=pillars,
            repo_count=len(app.repos),
            prev_score=prev,
            delta=delta,
        )

    # ── Pillar 1: Release Velocity (30%) ─────────────────────────────────────

    def _score_release_velocity(self, app: AppEntry) -> PillarScore:
        """
        Scores based on:
        - Release frequency (releases this week across all repos) — 60% of pillar
        - LTFD (lead time to deploy) — 40% of pillar
        """
        weight = WEIGHTS["release_velocity"]
        missing = []
        breakdown = {}

        # Release frequency score (target: >=5 releases/week per app = 100%)
        # Pulled from hygiene/reporter data already computed
        freq_total = self._get_release_count_for_app(app)
        freq_target = max(5, app.tier * 2)  # Tier 1 apps expected to release more
        freq_score = min(freq_total / freq_target * 100, 100)
        breakdown["release_count_this_week"] = freq_total
        breakdown["freq_target"] = freq_target
        breakdown["freq_score"] = round(freq_score, 1)

        if freq_total == 0:
            missing.append("No releases detected this week")

        # LTFD score (target: <=1.8 days = 100%)
        ltfd = self._get_ltfd_for_app(app)
        if ltfd is not None:
            # Linear scale: 0 days=100, 1.8 days=100, 8+ days=0
            ltfd_score = max(0, min(100, (8 - ltfd) / (8 - 1.8) * 100)) if ltfd > 1.8 else 100.0
            breakdown["ltfd_days"] = ltfd
            breakdown["ltfd_score"] = round(ltfd_score, 1)
        else:
            ltfd_score = 50  # Neutral if no data
            missing.append("LTFD data unavailable — using neutral 50")

        # Zero-touch bonus: +5 bonus points if zero-touch enabled
        zero_touch_bonus = 5 if app.zero_touch_deployment else 0
        breakdown["zero_touch_bonus"] = zero_touch_bonus

        raw = min(100, (freq_score * 0.6) + (ltfd_score * 0.4) + zero_touch_bonus)

        return PillarScore(
            name="release_velocity",
            weight=weight,
            raw_score=round(raw, 1),
            weighted_score=round(raw * weight / 100, 2),
            breakdown=breakdown,
            missing_data=missing,
        )

    def _get_release_count_for_app(self, app: AppEntry) -> int:
        """Sum release counts from all repos belonging to this app."""
        total = 0
        for repo in app.repos:
            key = f"{repo.org}/{repo.name}"
            # Primary repo counted double to reflect its importance
            multiplier = 2 if repo.is_primary else 1
            # Try to get from reporter output; fall back to 0
            reporter_path = Path("../reports/latest_metrics.json")
            if reporter_path.exists():
                with open(reporter_path) as f:
                    metrics = json.load(f)
                for m in metrics:
                    if m["repo"] == key:
                        total += m.get("release_count", 0) * multiplier
            # If no reporter data, hygiene JSON doesn't have release counts
        return total

    def _get_ltfd_for_app(self, app: AppEntry) -> Optional[float]:
        """Get LTFD from primary repo's reporter data."""
        if not app.primary_repo:
            return None
        key = f"{app.primary_repo.org}/{app.primary_repo.name}"
        reporter_path = Path("../reports/latest_metrics.json")
        if reporter_path.exists():
            with open(reporter_path) as f:
                metrics = json.load(f)
            for m in metrics:
                if m["repo"] == key:
                    return m.get("ltfd_days")
        return None

    # ── Pillar 2: Git Hygiene (20%) ───────────────────────────────────────────

    def _score_git_hygiene(self, app: AppEntry) -> PillarScore:
        """Average hygiene score across all repos of the app."""
        weight = WEIGHTS["git_hygiene"]
        missing = []
        repo_scores = []

        for repo in app.repos:
            key = f"{repo.org}/{repo.name}"
            data = self.hygiene_by_repo.get(key)
            if data:
                repo_scores.append(data["score"])
            else:
                missing.append(f"No hygiene data for {key}")

        if not repo_scores:
            raw = 50  # Neutral if no data at all
            missing.append("No hygiene data for any repo — run hygiene_checker.py")
        else:
            raw = sum(repo_scores) / len(repo_scores)

        return PillarScore(
            name="git_hygiene",
            weight=weight,
            raw_score=round(raw, 1),
            weighted_score=round(raw * weight / 100, 2),
            breakdown={
                "repo_count": len(app.repos),
                "repos_with_data": len(repo_scores),
                "avg_hygiene_score": round(raw, 1),
                "per_repo": {
                    f"{r.org}/{r.name}": self.hygiene_by_repo.get(f"{r.org}/{r.name}", {}).get("score", "N/A")
                    for r in app.repos
                }
            },
            missing_data=missing,
        )

    # ── Pillar 3: Pipeline Maturity (20%) ─────────────────────────────────────

    def _score_pipeline_maturity(self, app: AppEntry) -> PillarScore:
        """Scores based on pipeline capability flags in registry."""
        weight = WEIGHTS["pipeline_maturity"]
        checks = {}

        # CI exists (20 pts)
        ci_exists = app.ci_tool != "none"
        checks["ci_pipeline"] = (20, ci_exists, "CI pipeline configured")

        # CD enabled (25 pts)
        checks["cd_enabled"] = (25, app.cd_enabled, "CD pipeline enabled")

        # CR auto-creation (20 pts)
        checks["cr_auto_creation"] = (20, app.cr_auto_creation, "Change requests auto-created")

        # Feature flags (15 pts)
        checks["feature_flags"] = (15, app.feature_flags_adopted, "Feature flags adopted")

        # Pipeline standard version (10 pts — v2+ = full, v1 = half)
        try:
            std_ver = int(str(app.pipeline_standard).lstrip("v") or "1")
        except ValueError:
            std_ver = 1
        std_score = 10 if std_ver >= 2 else 5
        checks["pipeline_standard"] = (10, std_ver >= 2, f"Pipeline standard v{std_ver}")

        # Zero-touch (10 pts)
        checks["zero_touch"] = (10, app.zero_touch_deployment, "Zero-touch deployment")

        raw = sum(pts for pts, passed, _ in checks.values() if passed)

        return PillarScore(
            name="pipeline_maturity",
            weight=weight,
            raw_score=float(raw),
            weighted_score=round(raw * weight / 100, 2),
            breakdown={k: {"points": v[0], "passed": v[1], "label": v[2]} for k, v in checks.items()},
            missing_data=[v[2] for v in checks.values() if not v[1]],
        )

    # ── Pillar 4: Compliance (15%) ────────────────────────────────────────────

    def _score_compliance(self, app: AppEntry) -> PillarScore:
        """Scores based on compliance fields in registry."""
        weight = WEIGHTS["compliance"]
        checks = {}
        missing = []

        # Release page exists (30 pts)
        checks["release_page"] = (30, app.has_release_page, "Release page URL declared")
        if not app.has_release_page:
            missing.append("No release page URL in registry")

        # Compliance evidence (25 pts — Tier 1 required, others optional)
        has_evidence = app.has_compliance_evidence
        if app.tier == 1:
            checks["compliance_evidence"] = (25, has_evidence, "Compliance evidence linked (required for Tier 1)")
            if not has_evidence:
                missing.append("CRITICAL: Tier-1 app missing compliance evidence")
        else:
            checks["compliance_evidence"] = (15, has_evidence, "Compliance evidence linked")

        # Privileged access reviewed (30 pts)
        checks["priv_access"] = (30, app.is_priv_access_current, "Privileged access reviewed <90 days ago")
        if not app.is_priv_access_current:
            missing.append(f"Privileged access review overdue (last: {app.priv_access_reviewed_date or 'never'})")

        # Data classification declared (15 pts)
        classified = bool(app.data_classification and app.data_classification != "internal")
        checks["data_classification"] = (15, bool(app.data_classification), "Data classification declared")

        raw = sum(pts for pts, passed, _ in checks.values() if passed)

        return PillarScore(
            name="compliance",
            weight=weight,
            raw_score=float(min(raw, 100)),
            weighted_score=round(min(raw, 100) * weight / 100, 2),
            breakdown={k: {"points": v[0], "passed": v[1], "label": v[2]} for k, v in checks.items()},
            missing_data=missing,
        )

    # ── Pillar 5: Quality & Security (10%) ────────────────────────────────────

    def _score_quality_security(self, app: AppEntry) -> PillarScore:
        """
        Ideally pulls from SonarQube. Falls back to hygiene violation counts.
        """
        weight = WEIGHTS["quality_security"]
        missing = []
        breakdown = {}

        # Try SonarQube if configured
        if app.sonarqube_project:
            sq_score = self._fetch_sonarqube_score(app.sonarqube_project)
            if sq_score is not None:
                breakdown["sonarqube_score"] = sq_score
                breakdown["source"] = "sonarqube"
                return PillarScore(
                    name="quality_security",
                    weight=weight,
                    raw_score=sq_score,
                    weighted_score=round(sq_score * weight / 100, 2),
                    breakdown=breakdown,
                )
            else:
                missing.append(f"SonarQube project '{app.sonarqube_project}' not reachable")

        # Fallback: use hygiene violation counts as proxy for code quality
        total_violations = 0
        for repo in app.repos:
            key = f"{repo.org}/{repo.name}"
            data = self.hygiene_by_repo.get(key, {})
            total_violations += data.get("critical", 0) * 2 + data.get("warnings", 0)

        # Each violation deducts from 100
        raw = max(0, 100 - (total_violations * 5))
        breakdown["source"] = "hygiene_proxy"
        breakdown["total_violations"] = total_violations
        breakdown["note"] = "Configure sonarqube_project in registry for accurate quality scores"
        missing.append("No SonarQube project configured — using hygiene violations as proxy")

        return PillarScore(
            name="quality_security",
            weight=weight,
            raw_score=float(raw),
            weighted_score=round(raw * weight / 100, 2),
            breakdown=breakdown,
            missing_data=missing,
        )

    def _fetch_sonarqube_score(self, project_key: str) -> Optional[float]:
        """Fetch quality gate and coverage from SonarQube API. Returns 0-100 score."""
        try:
            import requests
            sq_cfg = self.cfg.get("sonarqube", {})
            base_url = sq_cfg.get("base_url", "")
            token = os.environ.get(sq_cfg.get("token_env", "SONARQUBE_TOKEN"), "")
            if not base_url or not token:
                return None

            resp = requests.get(
                f"{base_url}/api/measures/component",
                params={"component": project_key, "metricKeys": "coverage,code_smells,vulnerabilities,bugs"},
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
            )
            if resp.status_code != 200:
                return None

            measures = {m["metric"]: float(m["value"]) for m in resp.json().get("component", {}).get("measures", [])}
            coverage = measures.get("coverage", 50)
            smells = measures.get("code_smells", 0)
            vulns = measures.get("vulnerabilities", 0)
            bugs = measures.get("bugs", 0)

            # Coverage: 0–40 pts, Issues: deduct up to 60 pts
            coverage_score = min(coverage * 0.4, 40)
            issue_deduction = min((smells * 0.5 + vulns * 5 + bugs * 3), 60)
            return max(0, round(coverage_score + (60 - issue_deduction), 1))
        except Exception as e:
            logger.debug(f"SonarQube fetch error for {project_key}: {e}")
            return None

    # ── Pillar 6: Adoption (5%) ───────────────────────────────────────────────

    def _score_adoption(self, app: AppEntry) -> PillarScore:
        weight = WEIGHTS["adoption"]
        checks = {}

        # API catalog published (40 pts)
        checks["api_catalog"] = (40, app.apis_published, "APIs published in catalog")

        # AI tools declared (40 pts — any tool = full score)
        has_ai = bool(app.ai_tools_declared or app.copilot_enabled)
        checks["ai_adoption"] = (40, has_ai, f"AI tools declared: {app.ai_tools_declared or 'none'}")

        # Pipeline standard v2+ (20 pts)
        try:
            std_ver = int(str(app.pipeline_standard).lstrip("v") or "1")
        except ValueError:
            std_ver = 1
        checks["pipeline_std_v2"] = (20, std_ver >= 2, f"Pipeline on standard v2+")

        raw = sum(pts for pts, passed, _ in checks.values() if passed)

        return PillarScore(
            name="adoption",
            weight=weight,
            raw_score=float(raw),
            weighted_score=round(raw * weight / 100, 2),
            breakdown={k: {"points": v[0], "passed": v[1], "label": v[2]} for k, v in checks.items()},
            missing_data=[v[2] for v in checks.values() if not v[1]],
        )


# ── Output helpers ─────────────────────────────────────────────────────────────

def scores_to_json(scores: list[AppDevOpsScore]) -> list[dict]:
    output = []
    for s in scores:
        output.append({
            "app_id": s.app_id,
            "display_name": s.display_name,
            "service_line": s.service_line,
            "tier": s.tier,
            "total_score": s.total_score,
            "grade": s.grade,
            "trend": s.trend,
            "delta": s.delta,
            "repo_count": s.repo_count,
            "scored_at": s.scored_at,
            "pillars": {
                p.name: {
                    "raw_score": p.raw_score,
                    "weighted_score": p.weighted_score,
                    "weight_pct": p.weight,
                    "breakdown": p.breakdown,
                    "missing_data": p.missing_data,
                }
                for p in s.pillars
            },
        })
    return output


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser()
    parser.add_argument("--app", help="Score a single app by app_id")
    parser.add_argument("--output", default="../reports/latest_scores.json")
    parser.add_argument("--config", default="../config/settings.yaml")
    parser.add_argument("--registry", default=None, help="Path to registry directory")
    args = parser.parse_args()

    engine = ScoreEngine(config_path=args.config, registry_dir=args.registry)

    if args.app:
        app = engine.apps.get(args.app)
        if not app:
            print(f"ERROR: App '{args.app}' not found in registry")
            sys.exit(1)
        scores = [engine.score_app(app)]
    else:
        print(f"Scoring {len(engine.apps)} apps...")
        scores = engine.score_all()

    output = scores_to_json(scores)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(output, f, indent=2, default=str)

    print(f"\n{'─'*60}")
    print(f"  {'APP':<28} {'SCORE':>6} {'GRADE':>5} {'TREND':>5}")
    print(f"{'─'*60}")
    for s in scores:
        print(f"  {s.display_name:<28} {s.total_score:>6.1f} {s.grade:>5} {s.trend:>5}")
    print(f"{'─'*60}")
    avg = sum(s.total_score for s in scores) / len(scores) if scores else 0
    print(f"  {'AVERAGE':<28} {avg:>6.1f}")
    print(f"\n  Scores saved to: {args.output}\n")
