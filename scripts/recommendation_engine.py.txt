"""
recommendation_engine.py
════════════════════════════════════════════════════════════════
Pure Python rule-based recommendation engine.
Zero LLM dependency. No Claude API calls. Fully deterministic.

HOW IT WORKS (Option C):
  Step 1 — Interview actions first:
    Reads improvement_action_1/2/3 from the app's registry
    (captured by champion during the 1:1 interview call).
    These are shown first on the score card — most specific,
    most owned by the team.

  Step 2 — Rule engine fills gaps:
    Evaluates every rule in recommendation_rules.yaml against
    the app's registry flags and DPI score.
    Rules that fire are ranked by points_gain (highest first).
    Rules are deduplicated against interview actions so nothing
    appears twice.

  Step 3 — Return top 3 total:
    Combined list (interview actions + rule suggestions) trimmed
    to top 3. Interview actions always occupy the first slots.

Rules are configured in config/recommendation_rules.yaml.
No Python code change needed to add new rules — edit the YAML.

Usage:
    from recommendation_engine import RecommendationEngine

    engine = RecommendationEngine()
    actions = engine.get_recommendations(registry_entry, score_dict)
    # returns list of Recommendation objects
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)

RULES_PATH = Path(__file__).parent.parent / "config" / "recommendation_rules.yaml"


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class Recommendation:
    source: str          # "interview" | "rule"
    rank: int            # 1, 2, 3
    rule_id: str         # rule id or "interview_1/2/3"
    action: str          # short action title
    detail: str          # 2-3 sentence explanation
    pillar: str          # which DPI pillar this improves
    points_gain: int     # estimated DPI points gained
    effort: str          # low | medium | high
    effort_emoji: str    # ⚡ | 🔧 | 🏗

    def as_dict(self) -> dict:
        return {
            "source":      self.source,
            "rank":        self.rank,
            "rule_id":     self.rule_id,
            "action":      self.action,
            "detail":      self.detail,
            "pillar":      self.pillar,
            "points_gain": self.points_gain,
            "effort":      self.effort,
        }


EFFORT_EMOJI = {"low": "⚡", "medium": "🔧", "high": "🏗"}
EFFORT_LABEL = {
    "low":    "Quick win — 1–2 hours, one person",
    "medium": "Medium effort — 1–3 days, some coordination",
    "high":   "Significant effort — 1–2 weeks, multiple teams",
}


# ── Condition evaluator ───────────────────────────────────────────────────────

class ConditionEvaluator:
    """
    Evaluates rule conditions against a flat context dict.
    Supports: ==, !=, >=, <=, >, <, in [...], not in [...]
    Dot notation: pipeline_flags.cd_automated
    Special computed fields: access_security.priv_access_reviewed_stale
    """

    def __init__(self, context: dict):
        self.ctx = context

    def evaluate(self, condition: str) -> bool:
        """Evaluate a condition string. Returns True if condition is met."""
        try:
            condition = condition.strip()

            # Handle 'in [...]' and 'not in [...]'
            in_match = re.match(r'^(.+?)\s+(not\s+in|in)\s+\[(.+)\]$', condition)
            if in_match:
                path, op, values_str = in_match.groups()
                val = self._get(path.strip())
                values = [v.strip().strip('"').strip("'") for v in values_str.split(",")]
                return (str(val) in values) if "not" not in op else (str(val) not in values)

            # Handle comparison operators
            for op in ["==", "!=", ">=", "<=", ">", "<"]:
                if op in condition:
                    parts = condition.split(op, 1)
                    if len(parts) == 2:
                        left  = self._get(parts[0].strip())
                        right = self._parse_value(parts[1].strip())
                        return self._compare(left, op, right)

            # Boolean path alone
            val = self._get(condition)
            return bool(val)

        except Exception as e:
            logger.debug(f"Condition eval error '{condition}': {e}")
            return False

    def evaluate_compound(self, condition: str) -> bool:
        """Handle 'and' compound conditions."""
        if " and " in condition:
            parts = condition.split(" and ")
            return all(self.evaluate(p.strip()) for p in parts)
        if " or " in condition:
            parts = condition.split(" or ")
            return any(self.evaluate(p.strip()) for p in parts)
        return self.evaluate(condition)

    def _get(self, path: str) -> Any:
        """Get a value from the context using dot notation."""
        parts = path.strip().split(".")
        val = self.ctx
        for part in parts:
            if isinstance(val, dict):
                val = val.get(part)
            else:
                return None
        return val

    def _parse_value(self, s: str) -> Any:
        """Parse a literal value from a condition string."""
        s = s.strip().strip('"').strip("'")
        if s.lower() == "true":  return True
        if s.lower() == "false": return False
        if s.lower() in ("null", "none", "''"): return None
        if s == "''": return ""
        try:
            return int(s)
        except ValueError:
            pass
        try:
            return float(s)
        except ValueError:
            pass
        return s

    def _compare(self, left: Any, op: str, right: Any) -> bool:
        # None handling
        if right is None and op == "==": return left is None
        if right is None and op == "!=": return left is not None
        if left  is None: return False

        # Empty string check
        if right == "" and op == "==": return left == "" or left is None
        if right == "" and op == "!=": return left != "" and left is not None

        try:
            if op == "==": return left == right
            if op == "!=": return left != right
            if op == ">=": return float(left) >= float(right)
            if op == "<=": return float(left) <= float(right)
            if op == ">":  return float(left) >  float(right)
            if op == "<":  return float(left) <  float(right)
        except (TypeError, ValueError):
            if op == "==": return str(left) == str(right)
            if op == "!=": return str(left) != str(right)
        return False


# ── Template variable substitution ───────────────────────────────────────────

def substitute_variables(text: str, context: dict) -> str:
    """
    Replace {variable_name} placeholders in rule text with actual values.
    e.g. "You have {approval_gate_count} gates" → "You have 5 gates"
    """
    def replacer(match):
        key = match.group(1)
        # Try flat lookup first, then nested
        val = context.get(key)
        if val is None:
            for v in context.values():
                if isinstance(v, dict) and key in v:
                    val = v[key]
                    break
        return str(val) if val is not None else match.group(0)

    return re.sub(r'\{(\w+)\}', replacer, text)


# ── Main engine ───────────────────────────────────────────────────────────────

class RecommendationEngine:
    """
    Pure Python rule-based recommendation engine.
    Combines interview-captured actions with rule-based suggestions.
    """

    def __init__(self, rules_path: Path = RULES_PATH):
        self.rules = self._load_rules(rules_path)

    def _load_rules(self, path: Path) -> list[dict]:
        if not path.exists():
            logger.warning(f"Rules file not found: {path}")
            return []
        with open(path) as f:
            data = yaml.safe_load(f)
        rules = data.get("rules", [])
        logger.debug(f"Loaded {len(rules)} recommendation rules")
        return rules

    def get_recommendations(
        self,
        registry_entry: dict,
        score_dict: dict,
        max_total: int = 3,
    ) -> list[Recommendation]:
        """
        Main method. Returns up to max_total recommendations.
        Interview actions first, rule engine fills remaining slots.

        Args:
            registry_entry: The app's full registry dict
            score_dict:     The app's DPI score dict from score_engine.py
            max_total:      Maximum recommendations to return (default 3)

        Returns:
            Ordered list of Recommendation objects
        """
        recommendations: list[Recommendation] = []

        # ── Step 1: Interview-captured actions ────────────────
        interview_actions = self._get_interview_actions(registry_entry)
        for i, action_text in enumerate(interview_actions, 1):
            if not action_text.strip():
                continue
            rec = Recommendation(
                source      = "interview",
                rank        = len(recommendations) + 1,
                rule_id     = f"interview_{i}",
                action      = action_text,
                detail      = (
                    f"Action agreed with your team lead during the DevOps Champion "
                    f"interview — {action_text.lower().rstrip('.')}. "
                    f"This was identified as a key improvement lever for your app."
                ),
                pillar      = self._infer_pillar_from_text(action_text),
                points_gain = 0,   # interview actions don't have a point estimate
                effort      = "medium",
                effort_emoji = "🔧",
            )
            recommendations.append(rec)
            if len(recommendations) >= max_total:
                return recommendations

        # ── Step 2: Rule engine fills remaining slots ─────────
        slots_remaining = max_total - len(recommendations)
        if slots_remaining > 0:
            rule_recs = self._evaluate_rules(registry_entry, score_dict)

            # Deduplicate: skip rules that overlap with interview actions
            interview_keywords = self._extract_keywords(interview_actions)
            filtered = [
                r for r in rule_recs
                if not self._overlaps_with_interview(r.action, interview_keywords)
            ]

            for rec in filtered[:slots_remaining]:
                rec.rank = len(recommendations) + 1
                recommendations.append(rec)

        return recommendations

    def _get_interview_actions(self, registry_entry: dict) -> list[str]:
        """Extract improvement actions captured during the interview."""
        notes = registry_entry.get("interview_notes", {}) or {}

        # Support both flat fields and nested interview_notes dict
        actions = []
        for key in ["improvement_action_1", "improvement_action_2", "improvement_action_3"]:
            # Check flat
            val = registry_entry.get(key, "") or notes.get(key, "")
            if val and str(val).strip() and str(val).strip() not in ("", "null", "None"):
                actions.append(str(val).strip())

        # Also check interview_notes.improvement_actions list format
        action_list = notes.get("improvement_actions", [])
        for action in (action_list or []):
            if action and str(action).strip() not in actions:
                actions.append(str(action).strip())

        return actions[:3]  # max 3 interview actions

    def _evaluate_rules(
        self, registry_entry: dict, score_dict: dict
    ) -> list[Recommendation]:
        """Evaluate all rules and return matching ones ranked by points_gain."""
        app_type = registry_entry.get("app_type", "traditional")

        # Build flat context for condition evaluator
        context = self._build_context(registry_entry, score_dict)
        evaluator = ConditionEvaluator(context)

        matched: list[Recommendation] = []

        for rule in self.rules:
            rule_id = rule.get("id", "unknown")

            # Check skip_for_app_types
            skip_types = rule.get("skip_for_app_types", [])
            if app_type in skip_types:
                continue

            # Evaluate condition
            condition = rule.get("condition", "")
            if not condition:
                continue

            try:
                fires = evaluator.evaluate_compound(condition)
            except Exception as e:
                logger.debug(f"Rule {rule_id} condition error: {e}")
                continue

            if not fires:
                continue

            # Substitute template variables in action and detail text
            action_text = substitute_variables(rule.get("action", ""), context)
            detail_text = substitute_variables(rule.get("detail", ""), context)

            effort = rule.get("effort", "medium")
            matched.append(Recommendation(
                source       = "rule",
                rank         = 0,   # set later
                rule_id      = rule_id,
                action       = action_text,
                detail       = detail_text.strip(),
                pillar       = rule.get("pillar", ""),
                points_gain  = int(rule.get("points_gain", 0)),
                effort       = effort,
                effort_emoji = EFFORT_EMOJI.get(effort, "🔧"),
            ))

        # Sort by points_gain descending, then effort (low first)
        effort_order = {"low": 0, "medium": 1, "high": 2}
        matched.sort(
            key=lambda r: (-r.points_gain, effort_order.get(r.effort, 1))
        )
        return matched

    def _build_context(self, registry_entry: dict, score_dict: dict) -> dict:
        """Build a flat+nested context dict for the condition evaluator."""
        ctx = dict(registry_entry)

        # Add score pillar values as score.velocity, score.flow etc.
        pillars = score_dict.get("pillars", {})
        ctx["score"] = {
            k: v.get("raw_score", 0) if isinstance(v, dict) else v
            for k, v in pillars.items()
        }
        ctx["total_score"] = score_dict.get("total_score", 0)

        # Compute derived fields
        ctx["access_security"] = ctx.get("access_security", {}) or {}
        reviewed_date = ctx["access_security"].get("priv_access_reviewed_date", "")
        ctx["access_security"]["priv_access_reviewed_stale"] = self._is_review_stale(reviewed_date)

        # Flatten pipeline_flags for convenience
        ctx["pipeline_flags"] = ctx.get("pipeline_flags", {}) or {}

        # Flatten ai_adoption
        ctx["ai_adoption"] = ctx.get("ai_adoption", {}) or {}

        # Flatten compliance
        ctx["compliance"] = ctx.get("compliance", {}) or {}

        return ctx

    def _is_review_stale(self, date_str: str) -> bool:
        """Returns True if priv access review date is older than 90 days."""
        if not date_str or str(date_str).strip() in ("", "null", "None"):
            return False  # Never reviewed is caught by separate rule
        try:
            reviewed = datetime.strptime(str(date_str).strip(), "%Y-%m-%d")
            reviewed = reviewed.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - reviewed) > timedelta(days=90)
        except ValueError:
            return False

    def _infer_pillar_from_text(self, text: str) -> str:
        """Guess which DPI pillar an interview action relates to."""
        text_lower = text.lower()
        if any(w in text_lower for w in ["release", "deploy", "frequency", "batch", "ltdd", "flag"]):
            return "Velocity"
        if any(w in text_lower for w in ["approval", "gate", "branch", "pr ", "commit", "review"]):
            return "Flow"
        if any(w in text_lower for w in ["rollback", "security", "access", "priv", "sast", "vuln", "incident"]):
            return "Stability"
        if any(w in text_lower for w in ["ci", "cd", "automat", "pipeline", "jenkins", "zero-touch", "cr "]):
            return "Automation"
        if any(w in text_lower for w in ["ai", "copilot", "api", "catalog", "agent"]):
            return "AI & Adoption"
        return ""

    def _extract_keywords(self, actions: list[str]) -> set[str]:
        """Extract meaningful keywords from interview actions for deduplication."""
        keywords = set()
        stopwords = {"the", "a", "an", "to", "from", "in", "of", "and", "or", "your", "our", "for"}
        for action in actions:
            words = re.findall(r'\b\w{4,}\b', action.lower())
            keywords.update(w for w in words if w not in stopwords)
        return keywords

    def _overlaps_with_interview(self, rule_action: str, interview_keywords: set) -> bool:
        """Returns True if a rule action is too similar to an interview action."""
        if not interview_keywords:
            return False
        rule_words = set(re.findall(r'\b\w{4,}\b', rule_action.lower()))
        overlap = rule_words & interview_keywords
        # If more than 2 significant keywords match, consider it a duplicate
        return len(overlap) >= 2

    def format_for_email(self, recommendations: list[Recommendation]) -> str:
        """Format recommendations as HTML for inclusion in alert email."""
        if not recommendations:
            return "<p style='color:#888;font-size:13px;'>No specific recommendations available yet — complete your registry entry for personalised actions.</p>"

        rows = ""
        for rec in recommendations:
            source_label = (
                '<span style="background:#e8f4fd;color:#1a5fb4;padding:1px 6px;'
                'border-radius:3px;font-size:10px;font-weight:700;">FROM YOUR INTERVIEW</span>'
                if rec.source == "interview" else ""
            )
            effort_color = {"low": "#1a7a3c", "medium": "#c8832a", "high": "#c8382a"}.get(rec.effort, "#555")
            points_str = f"+{rec.points_gain} pts" if rec.points_gain > 0 else "Score improvement"

            rows += f"""
            <tr>
              <td style="padding:12px 14px;border-bottom:1px solid #eee;vertical-align:top;width:24px;">
                <span style="font-size:20px;line-height:1;">{rec.effort_emoji}</span>
              </td>
              <td style="padding:12px 14px;border-bottom:1px solid #eee;vertical-align:top;">
                <div style="font-weight:600;font-size:14px;color:#1a1a1a;margin-bottom:4px;">
                  {rec.action} {source_label}
                </div>
                <div style="font-size:13px;color:#555;line-height:1.5;margin-bottom:6px;">{rec.detail}</div>
                <div>
                  <span style="font-size:11px;color:#888;margin-right:12px;">📊 {rec.pillar}</span>
                  <span style="font-size:11px;color:{effort_color};margin-right:12px;">{rec.effort_emoji} {EFFORT_LABEL.get(rec.effort, rec.effort)}</span>
                  <span style="font-size:11px;color:#1a7a3c;font-weight:600;">{points_str}</span>
                </div>
              </td>
            </tr>"""

        return f"""
        <table style="width:100%;border-collapse:collapse;font-size:13px;margin-top:8px;">
          {rows}
        </table>"""

    def format_for_terminal(self, recommendations: list[Recommendation], app_id: str) -> str:
        """Format recommendations as plain text for terminal/logging output."""
        if not recommendations:
            return f"  {app_id}: No recommendations generated"

        lines = [f"  Recommendations for {app_id}:"]
        for rec in recommendations:
            src = "(from interview)" if rec.source == "interview" else f"(+{rec.points_gain} pts, {rec.effort} effort)"
            lines.append(f"    {rec.rank}. [{rec.pillar}] {rec.action} {src}")
            lines.append(f"       {rec.detail[:100]}...")
        return "\n".join(lines)


# ── CLI demo ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    # Example registry entry and score for demo
    sample_registry = {
        "app_id":       "payments-api",
        "display_name": "Payments API",
        "app_type":     "traditional",
        "stack":        "java-spring",
        "in_production": True,
        "rf_excluded":  False,
        "pipeline_flags": {
            "ci_automated":              True,
            "cd_automated":              False,   # ← will trigger rule
            "standard_pipeline_adopted": True,
            "git_hygiene_adopted":       False,   # ← will trigger rule
            "cr_auto_creation":          False,   # ← will trigger rule
            "zero_touch_deployment":     False,
            "automated_rollback":        False,
            "feature_flags_adopted":     False,
            "approval_gate_count":       4,       # ← will trigger rule (>=3)
        },
        "access_security": {
            "priv_access_for_deploy":     True,   # ← will trigger rule
            "priv_access_reviewed_date":  "2025-11-01",  # ← stale
            "sast_enabled":               False,
        },
        "compliance": {
            "release_page_url":         "",       # ← will trigger rule
            "compliance_evidence_page": "",
        },
        "ai_adoption": {
            "copilot_enabled":    False,
            "ai_tools_declared":  [],
            "apis_published":     False,
            "api_count":          5,             # ← will trigger catalog rule
        },
        # Interview actions captured during champion's 1:1 call
        "improvement_action_1": "Enable CD automation — configure Jenkins deploy job to auto-trigger after CI",
        "improvement_action_2": "Reduce approval gates from 4 to 1 — work with finance and security teams",
        "improvement_action_3": "",
        "interview_notes": {
            "biggest_release_blocker": "4 people need to approve before we can deploy",
        }
    }

    sample_score = {
        "app_id":      "payments-api",
        "total_score": 52,
        "prev_score":  45,
        "delta":       7,
        "pillars": {
            "velocity":    {"raw_score": 35},
            "flow":        {"raw_score": 68},
            "stability":   {"raw_score": 55},
            "automation":  {"raw_score": 47},
            "ai_adoption": {"raw_score": 20},
        }
    }

    engine = RecommendationEngine()
    recs   = engine.get_recommendations(sample_registry, sample_score)

    print(f"\n{'═'*60}")
    print(f"  RECOMMENDATIONS — {sample_registry['display_name']}")
    print(f"{'═'*60}")
    print(engine.format_for_terminal(recs, "payments-api"))
    print(f"\n  Total: {len(recs)} recommendations")
    print(f"  Interview actions: {sum(1 for r in recs if r.source == 'interview')}")
    print(f"  Rule-based:        {sum(1 for r in recs if r.source == 'rule')}")

    print(f"\n  JSON output:")
    print(json.dumps([r.as_dict() for r in recs], indent=2))
