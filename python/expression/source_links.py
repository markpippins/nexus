"""Read-only resolution of Expression source links against governed registries."""
from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable


RELATION_BINDINGS = {"mentions_decision_language": "mentions"}
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.I)
_PR_RE = re.compile(r"^PR\s*#?(\d+)$", re.I)
_VERSION_RE = re.compile(r"^V(\d{3,4})$", re.I)
_GITHUB_PR_RE = re.compile(r"^https?://github\.com/([^/]+)/([^/]+)/pull/(\d+)(?:[/?#].*)?$", re.I)


def _result(status: str, kind: str, artifact_id: str | None = None, reason: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"status": status}
    if status == "resolved":
        result["resolved_to"] = {"artifact_kind": kind, "id": artifact_id}
    if reason:
        result["reason"] = reason
    return result


class LiveGovernedArtifactResolver:
    """Resolve artifacts via Nebula REST, Resolution REST, GitHub CLI and repo files.

    HTTP and process adapters are injectable to make resolution policy hermetic
    in tests. No resolver operation writes canonical state.
    """

    def __init__(
        self,
        *,
        repo_root: str | Path | None = None,
        nebula_api: str | None = None,
        resolution_api: str | None = None,
        github_repo: str | None = None,
        http_get: Callable[[str], Any] | None = None,
        command_runner: Callable[..., Any] | None = None,
    ) -> None:
        self.repo_root = Path(repo_root or Path(__file__).resolve().parents[2]).resolve()
        self.nebula_api = (nebula_api or os.getenv("NEBULA_API_URL", "http://localhost:3101/api")).rstrip("/")
        resolution = (resolution_api or os.getenv("RESOLUTION_API_URL", "http://localhost:3171")).rstrip("/")
        self.resolution_api = resolution[:-4] if resolution.endswith("/api") else resolution
        self._http_get = http_get or self._default_http_get
        self._command_runner = command_runner or subprocess.run
        self.github_repo = github_repo or self._github_repo_from_remote()

    @staticmethod
    def _default_http_get(url: str) -> Any:
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def _github_repo_from_remote(self) -> str | None:
        try:
            result = self._command_runner(
                ["git", "remote", "get-url", "origin"], cwd=str(self.repo_root),
                capture_output=True, text=True, timeout=3, check=False,
            )
            remote = (result.stdout or "").strip()
            match = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$", remote, re.I)
            return match.group(1) if match else None
        except Exception:
            return None

    def _get(self, url: str, kind: str, artifact_id: str) -> dict[str, Any]:
        try:
            data = self._http_get(url)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return _result("unresolved", kind, reason="not_found")
            return _result("unavailable", kind, reason=f"registry_http_{exc.code}")
        except Exception as exc:
            return _result("unavailable", kind, reason=f"registry_unavailable:{type(exc).__name__}")
        if data is None or (isinstance(data, dict) and data.get("error")):
            return _result("unresolved", kind, reason="not_found")
        if isinstance(data, dict) and data.get("id") is not None and str(data["id"]) != str(artifact_id):
            return _result("unresolved", kind, reason="registry_identity_mismatch")
        return _result("resolved", kind, artifact_id)

    def _resolve_pr(self, number: str, repo: str | None = None) -> dict[str, Any]:
        target = repo or self.github_repo
        if not self.github_repo:
            return _result("unavailable", "pull_request", reason="github_repository_unknown")
        if target.lower() != self.github_repo.lower():
            return _result("unresolved", "pull_request", reason="outside_governed_repository")
        try:
            result = self._command_runner(
                ["gh", "pr", "view", number, "--repo", target,
                 "--json", "number,url,state,mergedAt,headRefName"],
                cwd=str(self.repo_root), capture_output=True, text=True,
                timeout=8, check=False,
            )
        except Exception as exc:
            return _result("unavailable", "pull_request", reason=f"github_registry_unavailable:{type(exc).__name__}")
        if result.returncode:
            message = (result.stderr or result.stdout or "").lower()
            if "not found" in message or "could not resolve" in message:
                return _result("unresolved", "pull_request", reason="not_found")
            return _result("unavailable", "pull_request", reason="github_registry_query_failed")
        try:
            pull = json.loads(result.stdout)
            if int(pull.get("number", -1)) != int(number):
                return _result("unresolved", "pull_request", reason="registry_identity_mismatch")
        except (ValueError, TypeError, json.JSONDecodeError):
            return _result("unavailable", "pull_request", reason="invalid_registry_response")
        return _result("resolved", "pull_request", f"{target}#{number}")

    def _resolve_migration(self, version: str) -> dict[str, Any]:
        matches = sorted({
            path.resolve() for pattern in (
                f"sql/V{version}__*.sql", f"schemas/migrations/**/V{version}__*.sql"
            ) for path in self.repo_root.glob(pattern) if path.is_file()
        })
        if not matches:
            return _result("unresolved", "migration", reason="migration_not_in_repository")
        if len(matches) > 1:
            return _result("ambiguous", "migration", reason="multiple_migrations_claim_version")
        return _result("resolved", "migration", matches[0].relative_to(self.repo_root).as_posix())

    def _resolve_path(self, token: str) -> dict[str, Any]:
        raw = token.strip().rstrip(".,;:)]}")
        if raw.startswith("nexus/"):
            raw = raw[len("nexus/"):]
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = self.repo_root / candidate
        try:
            path = candidate.resolve(strict=True)
            path.relative_to(self.repo_root)
        except (OSError, ValueError):
            return _result("unresolved", "repository_path", reason="path_not_found_or_outside_repository")
        if not path.is_file():
            return _result("unresolved", "repository_path", reason="path_not_a_file")
        return _result("resolved", "repository_path", path.relative_to(self.repo_root).as_posix())

    def _resolve_url(self, value: str) -> dict[str, Any]:
        match = _GITHUB_PR_RE.fullmatch(value)
        if match:
            owner, repo, number = match.groups()
            return self._resolve_pr(number, f"{owner}/{repo}")
        parsed = urllib.parse.urlsplit(value)
        if parsed.hostname not in {"localhost", "127.0.0.1"}:
            return _result("unresolved", "url", reason="url_is_not_a_registered_governed_artifact")
        routes = (
            (r"^/api/agent-records/([0-9a-f-]{36})$", self.nebula_api, "agent_record"),
            (r"^/api/harvests/([0-9a-f-]{36})$", self.nebula_api, "harvest"),
            (r"^/api/harvest-candidates/([0-9a-f-]{36})$", self.nebula_api, "harvest_candidate"),
            (r"^/api/plans/(\d+)$", self.nebula_api, "implementation_plan"),
        )
        for pattern, base, kind in routes:
            match = re.fullmatch(pattern, parsed.path.rstrip("/"), re.I)
            if match:
                artifact_id = match.group(1)
                return self._get(base + parsed.path.rstrip("/").removeprefix("/api"), kind, artifact_id)
        return _result("unresolved", "url", reason="url_is_not_a_registered_governed_artifact")

    def resolve_reference(self, token: str, reference_kind: str | None = None) -> dict[str, Any]:
        value = str(token).strip().rstrip(".,;:)]}")
        kind = (reference_kind or "").lower()
        pr, version = _PR_RE.fullmatch(value), _VERSION_RE.fullmatch(value)
        if kind == "url" or value.lower().startswith(("http://", "https://")):
            return self._resolve_url(value)
        if kind == "pr" or pr:
            return self._resolve_pr(pr.group(1)) if pr else _result("unresolved", "pull_request", reason="invalid_pull_request_reference")
        if kind in {"migration", "migration_version", "version"} or version:
            return self._resolve_migration(version.group(1)) if version else _result("unresolved", "migration", reason="invalid_migration_reference")
        if kind == "path" or value.startswith(("./", "../", "/", "nexus/")):
            return self._resolve_path(value)
        if kind in {"record", "agent_record"}:
            if not _UUID_RE.fullmatch(value):
                return _result("unresolved", "agent_record", reason="invalid_record_reference")
            return self._get(f"{self.nebula_api}/agent-records/{value}", "agent_record", value)
        if _UUID_RE.fullmatch(value):
            unavailable = None
            for route, artifact_kind in (
                (f"/agent-records/{value}", "agent_record"),
                (f"/harvests/{value}", "harvest"),
                (f"/harvest-candidates/{value}", "harvest_candidate"),
            ):
                result = self._get(self.nebula_api + route, artifact_kind, value)
                if result["status"] == "resolved":
                    return result
                if result["status"] == "unavailable":
                    unavailable = result
            return unavailable or _result("unresolved", "nexus_record", reason="uuid_not_in_governed_registries")
        if value.isdecimal():
            return self._get(f"{self.nebula_api}/plans/{urllib.parse.quote(value)}", "implementation_plan", value)
        return _result("unresolved", "unknown", reason="unsupported_reference_shape")

    def resolve_relation(self, relation_name: str) -> dict[str, Any]:
        """Require exactly one active row from Resolution's live relation registry."""
        url = self.resolution_api + "/api/relation_vocabulary?limit=1000&offset=0"
        try:
            data = self._http_get(url)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                try:
                    meta = self._http_get(self.resolution_api + "/api/meta")
                except Exception as meta_exc:
                    return _result("unavailable", "governed_relation", reason=f"relation_registry_unavailable:{type(meta_exc).__name__}")
                tables = {row.get("table") for row in meta.get("tables", [])} if isinstance(meta, dict) else set()
                reason = "relation_registry_route_unavailable" if "relation_vocabulary" in tables else "relation_registry_not_exposed_by_live_service"
                return _result("unavailable", "governed_relation", reason=reason)
            return _result("unavailable", "governed_relation", reason=f"relation_registry_http_{exc.code}")
        except Exception as exc:
            return _result("unavailable", "governed_relation", reason=f"relation_registry_unavailable:{type(exc).__name__}")
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            return _result("unavailable", "governed_relation", reason="invalid_relation_registry_response")
        matches = [row for row in data["items"] if row.get("name") == relation_name and row.get("expired_at") is None]
        if len(matches) == 1:
            return _result("resolved", "governed_relation", str(matches[0].get("id") or relation_name))
        if len(matches) > 1:
            return _result("ambiguous", "governed_relation", reason="duplicate_active_relation_name")
        if data.get("count", len(data["items"])) >= 1000:
            return _result("unavailable", "governed_relation", reason="relation_registry_page_limit_reached")
        return _result("unresolved", "governed_relation", reason="relation_not_active_in_registry")


def resolve_source_links(bundle: dict[str, Any], resolver: Any) -> dict[str, Any]:
    """Resolve all extracted references and produce a gate for each candidate."""
    observations = bundle.get("observations", [])
    by_id = {item.get("observation_id"): item for item in observations}
    source_links = []
    for observation in observations:
        if observation.get("kind") not in {"reference", "version"}:
            continue
        resolution = resolver.resolve_reference(str(observation.get("value", "")), observation.get("reference_kind"))
        observation["source_link_resolution"] = resolution
        source_links.append({"observation_id": observation.get("observation_id"), "token": observation.get("value"), **resolution})

    bindings, gates = [], []
    for proposition in bundle.get("proposition_candidates", []):
        token = str(proposition.get("predicate", ""))
        relation_name = RELATION_BINDINGS.get(token)
        relation = resolver.resolve_relation(relation_name) if relation_name else _result("unresolved", "governed_relation", reason="predicate_token_unmapped")
        bindings.append({"proposition_id": proposition.get("proposition_id"), "token": token, "relation_name": relation_name, **relation})
        missing_ids = [oid for oid in proposition.get("source_observation_ids", []) if oid not in by_id]
        # The current bundle has no explicit cross-segment link edge, so fail
        # closed over every extracted governed reference rather than infer one.
        links = [row for row in source_links]
        unresolved_ids = [row["observation_id"] for row in links if row.get("status") != "resolved"]
        reasons = []
        if missing_ids:
            reasons.append("proposition_source_observation_missing")
        if relation.get("status") != "resolved":
            reasons.append("governed_relation_unresolved")
        if unresolved_ids:
            reasons.append("source_link_unresolved")
        gates.append({
            "proposition_id": proposition.get("proposition_id"),
            "eligible": not reasons,
            "relation_name": relation_name,
            "relation_resolution": relation,
            "source_observation_ids": list(proposition.get("source_observation_ids", [])),
            "source_link_observation_ids": [row["observation_id"] for row in links],
            "source_link_resolutions": links,
            "unresolved_source_link_ids": unresolved_ids,
            "reasons": reasons,
        })
    return {
        "resolver_revision": "expression-source-links-v0.1",
        "source_links": source_links,
        "relation_bindings": bindings,
        "proposition_gates": gates,
        "eligible_proposition_ids": [gate["proposition_id"] for gate in gates if gate["eligible"]],
        "blocked_proposition_ids": [gate["proposition_id"] for gate in gates if not gate["eligible"]],
        "all_resolved": all(row.get("status") == "resolved" for row in source_links + bindings),
    }
