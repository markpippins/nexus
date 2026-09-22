"""TypeSafe System One adapter — implements the `system_one` API backed by ollama.

This adapter provides the same interface as the hosted TypeSafe jev model,
but backed by a local ollama endpoint on helium. The adapter is backend-
replaceable: swap in jev-hosted or jev-self-hosted later with zero call-site churn.

Reference: Architect assessment `1564cb0a` — the adapter, not the SDK, is the
strategic piece. Implements the three TypeSafe primitives:
- Noul: P(yes) in [0, 1] — proposition truth
- Choice: per-label probabilities + confidence — classification with "flag for review"
- Score: rubric → probability-weighted expected value + confidence + legend

Guardrails (enforced at code level):
- Read-only: no governance writes, no admission authority, no reviewer settlement
- Fail-visible: timeout/error/unknown behavior surfaces explicitly
- No hosted Jev commitment: this is the ollama adapter spike only

Jev 4 additions:
- Full judgment cache with key: (state_hash, question, expected_outcome_type, model_version, policy_version_hash, adapter_backend)
- Stale-read-set rejection via read-set digest comparison
- Replay identity for verification
- Retention policies with TTL and eviction rules
- Source-of-truth placement: evidence store (primary), cache store (secondary)
- Negative case handling for stale, drifted, mismatched versions
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Union

import httpx

from ...inference_engine import KnowledgeBase

logger = logging.getLogger(__name__)


# ── TypeSafe Primitives (matching models.tsp) ───────────────────────────

class JevPrimitiveType(str, Enum):
    NOUL = "noul"
    CHOICE = "choice"
    SCORE = "score"


@dataclass
class JevConfidence:
    """Confidence is concentration-based (calibrated, not vibes)."""
    concentration: float = 0.0
    decision_mass: float = 0.0


# ── Jev 4: Cache Models ─────────────────────────────────────────────────

class CacheEntryStatus(str, Enum):
    """Cache entry status — tracks freshness and validity."""
    FRESH = "fresh"
    STALE_READ_SET = "stale_read_set"
    MODEL_VERSION_MISMATCH = "model_version_mismatch"
    POLICY_VERSION_MISMATCH = "policy_version_mismatch"
    BACKEND_MISMATCH = "backend_mismatch"
    EVICTED = "evicted"
    CORRUPTED = "corrupted"


@dataclass
class CacheValidationResult:
    """Cache validation result — determines if cached entry can be served."""
    valid: bool
    status: Optional[CacheEntryStatus] = None
    detail: str = ""
    validated_key: Optional["JudgmentCacheKey"] = None
    current_read_set_digest: Optional[str] = None
    expected_read_set_digest: Optional[str] = None


@dataclass
class JudgmentCacheKey:
    """Judgment cache key per Jev 4 doctrine:
    (state_hash, question, expected_outcome_type, model_version, policy_version_hash, adapter_backend)"""
    state_hash: str
    question: str
    expected_outcome_type: JevPrimitiveType
    model_version: str
    policy_version_hash: str
    adapter_backend: str
    
    def to_string(self) -> str:
        return f"{self.state_hash}:{self.question}:{self.expected_outcome_type.value}:{self.model_version}:{self.policy_version_hash}:{self.adapter_backend}"
    
    @classmethod
    def from_string(cls, key_str: str) -> "JudgmentCacheKey":
        parts = key_str.split(":")
        if len(parts) != 6:
            raise ValueError(f"Invalid cache key format: {key_str}")
        return cls(
            state_hash=parts[0],
            question=parts[1],
            expected_outcome_type=JevPrimitiveType(parts[2]),
            model_version=parts[3],
            policy_version_hash=parts[4],
            adapter_backend=parts[5],
        )


@dataclass
class CacheRetentionPolicy:
    """Cache eviction/retention policy."""
    max_ttl: timedelta = field(default_factory=lambda: timedelta(days=30))
    max_entries_per_group: int = 10
    max_total_entries: int = 10000
    evict_on_model_version_change: bool = True
    evict_on_policy_version_change: bool = True
    evict_on_backend_change: bool = True


@dataclass
class StaleReadSetRejection:
    """Stale read-set rejection configuration."""
    enabled: bool = True
    max_read_set_age: timedelta = field(default_factory=lambda: timedelta(days=1))
    reject_on_digest_mismatch: bool = True
    on_stale: str = "reject"  # "reject" | "retry_fresh" | "return_stale_with_warning"


@dataclass
class ReplayIdentity:
    """Replay identity — ensures cached judgments are replayable with identical results."""
    replay_id: str
    original_cache_key: JudgmentCacheKey
    original_judgment: Any
    replay_context: Dict[str, Any]
    outcome_identical: bool
    difference: Optional[str] = None
    replayed_at: datetime = field(default_factory=datetime.utcnow)


class JudgmentStoreLocation(str, Enum):
    """Source-of-truth placement for judgment records."""
    EVIDENCE_STORE = "evidence_store"      # PRIMARY - append-only, immutable
    KEYCHAINS_MANIFEST = "keychains_manifest"  # For provenance linkage
    WITNESSED_RUN = "witnessed_run"        # For audit trail
    CACHE_STORE = "cache_store"            # SECONDARY - ephemeral, TTL-based


@dataclass
class CanonicalJudgmentRecord:
    """Canonical judgment record — the authoritative record of a judgment.
    Stored in evidence store; cache is a derived projection."""
    record_id: str
    cache_key: JudgmentCacheKey
    outcome: Any
    provenance: Dict[str, Any]
    calibration: Dict[str, Any]
    store_location: JudgmentStoreLocation = JudgmentStoreLocation.EVIDENCE_STORE
    validation_status: CacheEntryStatus = CacheEntryStatus.FRESH
    created_at: datetime = field(default_factory=datetime.utcnow)
    retention_policy: Optional[CacheRetentionPolicy] = None
    replay_identity: Optional[ReplayIdentity] = None


@dataclass
class CacheEntry:
    """Internal cache entry with full metadata."""
    key: JudgmentCacheKey
    outcome: Any
    provenance: Dict[str, Any]
    calibration: Dict[str, Any]
    read_set_digest: str
    created_at: datetime = field(default_factory=datetime.utcnow)
    accessed_at: datetime = field(default_factory=datetime.utcnow)
    access_count: int = 0
    validation_status: CacheEntryStatus = CacheEntryStatus.FRESH


@dataclass
class NoulResult:
    primitive: str = "noul"
    probability: float = 0.0
    confidence: JevConfidence = field(default_factory=JevConfidence)


@dataclass
class ChoiceResult:
    primitive: str = "choice"
    label_probabilities: Dict[str, float] = field(default_factory=dict)
    top_label: str = ""
    confidence: JevConfidence = field(default_factory=JevConfidence)
    flag_for_review: bool = False


@dataclass
class ScoreResult:
    primitive: str = "score"
    expected_value: float = 0.0
    confidence: JevConfidence = field(default_factory=JevConfidence)
    legend: Dict[str, Any] = field(default_factory=dict)


JevJudgmentResult = Union[NoulResult, ChoiceResult, ScoreResult]


@dataclass
class JevNonOutcomeResult:
    """Explicit non-outcome: uncertain, stale, refused, unevaluable."""
    kind: str  # "uncertain" | "stale" | "refused" | "unevaluable"
    reason: str
    retryable: bool = False


# ── Adapter Configuration ───────────────────────────────────────────────

@dataclass
class OllamaConfig:
    """Configuration for the ollama backend."""
    base_url: str = "http://helium:11434"  # Default helium ollama endpoint
    model: str = "qwen2.5-coder:latest"    # Default model
    timeout_seconds: float = 30.0
    max_retries: int = 2
    temperature: float = 0.1  # Low temperature for calibrated judgments


@dataclass
class JevAdapterConfig:
    """Configuration for the Jev adapter."""
    ollama: OllamaConfig = field(default_factory=OllamaConfig)
    # Confidence threshold below which we flag for review
    confidence_threshold: float = 0.75
    # Calibration metadata (populated after differential pilot)
    calibration: Optional[Dict[str, Any]] = None


# ── Core Adapter Interface ──────────────────────────────────────────────

class TypeSafeAdapter(ABC):
    """Abstract interface for TypeSafe System One adapters.
    
    Both the ollama adapter and the hosted Jev adapter implement this.
    Zero call-site churn when swapping backends.
    """
    
    @abstractmethod
    async def system_one(
        self,
        state: Dict[str, Any],
        questions: List[str],
        expected_types: Optional[List[JevPrimitiveType]] = None,
    ) -> List[Union[JevJudgmentResult, JevNonOutcomeResult]]:
        """Execute bounded judgment queries."""
        pass
    
    @abstractmethod
    async def health_check(self) -> bool:
        """Check if the backend is available."""
        pass
    
    @abstractmethod
    def get_backend_identity(self) -> str:
        """Return backend identifier for provenance/cache key."""
        pass


# ── Ollama Adapter Implementation ──────────────────────────────────────

class OllamaTypeSafeAdapter(TypeSafeAdapter):
    """TypeSafe adapter backed by ollama on helium.
    
    Implements the three primitives by prompting an LLM with structured
    output formats. This is the spike implementation — the same interface
    will be used for jev-hosted and jev-self-hosted (vLLM fork) later.
    """
    
    def __init__(self, config: JevAdapterConfig):
        self.config = config
        self.client: Optional[httpx.AsyncClient] = None
    
    async def __aenter__(self) -> "OllamaTypeSafeAdapter":
        self.client = httpx.AsyncClient(
            base_url=self.config.ollama.base_url,
            timeout=self.config.ollama.timeout_seconds,
        )
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if self.client:
            await self.client.aclose()
    
    def get_backend_identity(self) -> str:
        return f"ollama-helium:{self.config.ollama.model}"
    
    async def health_check(self) -> bool:
        if not self.client:
            return False
        try:
            resp = await self.client.get("/api/tags", timeout=5.0)
            return resp.status_code == 200
        except Exception:
            return False
    
    async def system_one(
        self,
        state: Dict[str, Any],
        questions: List[str],
        expected_types: Optional[List[JevPrimitiveType]] = None,
    ) -> List[Union[JevJudgmentResult, JevNonOutcomeResult]]:
        """Execute judgment queries against ollama."""
        if not self.client:
            raise RuntimeError("Adapter not initialized — use async context manager")
        
        if expected_types is None:
            expected_types = [JevPrimitiveType.NOUL] * len(questions)
        
        results = []
        for question, expected_type in zip(questions, expected_types):
            try:
                result = await self._execute_judgment(state, question, expected_type)
                results.append(result)
            except Exception as e:
                logger.error(f"Judgment failed for question '{question}': {e}")
                # Fail-visible: return explicit non-outcome
                results.append(JevNonOutcomeResult(
                    kind="unevaluable",
                    reason=f"Adapter error: {e}",
                    retryable=True,
                ))
        
        return results
    
    async def _execute_judgment(
        self,
        state: Dict[str, Any],
        question: str,
        expected_type: JevPrimitiveType,
    ) -> Union[JevJudgmentResult, JevNonOutcomeResult]:
        """Execute a single judgment against ollama."""
        prompt = self._build_prompt(state, question, expected_type)
        response = await self._call_ollama(prompt)
        return self._parse_response(response, expected_type)
    
    def _build_prompt(
        self,
        state: Dict[str, Any],
        question: str,
        expected_type: JevPrimitiveType,
    ) -> str:
        """Build a structured prompt for the given primitive type."""
        state_summary = self._summarize_state(state)
        
        if expected_type == JevPrimitiveType.NOUL:
            return f"""You are a calibrated judgment engine. Answer the proposition with a probability P(yes) in [0,1].

STATE CONTEXT:
{state_summary}

PROPOSITION: {question}

Return ONLY a JSON object:
{{
  "probability": <float 0-1>,
  "confidence_concentration": <float>,
  "confidence_decision_mass": <float>
}}"""
        
        elif expected_type == JevPrimitiveType.CHOICE:
            return f"""You are a calibrated judgment engine. Classify the input into one of the predefined labels with probabilities.

STATE CONTEXT:
{state_summary}

QUESTION: {question}

Return ONLY a JSON object:
{{
  "label_probabilities": {{"label1": <float>, "label2": <float>, ...}},
  "top_label": "<string>",
  "confidence_concentration": <float>,
  "confidence_decision_mass": <float>,
  "flag_for_review": <bool>
}}"""
        
        elif expected_type == JevPrimitiveType.SCORE:
            return f"""You are a calibrated judgment engine. Evaluate the input against a rubric and return a probability-weighted expected value.

STATE CONTEXT:
{state_summary}

RUBRIC QUESTION: {question}

Return ONLY a JSON object:
{{
  "expected_value": <float>,
  "confidence_concentration": <float>,
  "confidence_decision_mass": <float>,
  "legend": {{"band": "description", ...}}
}}"""
        
        return ""
    
    def _summarize_state(self, state: Dict[str, Any]) -> str:
        """Summarize state for prompt context (truncated)."""
        summary = {}
        for k, v in state.items():
            if isinstance(v, (str, int, float, bool)):
                summary[k] = v
            elif isinstance(v, dict):
                summary[k] = f"<dict with {len(v)} keys>"
            elif isinstance(v, list):
                summary[k] = f"<list of {len(v)} items>"
            else:
                summary[k] = str(v)[:100]
        return json.dumps(summary, indent=2)[:2000]
    
    async def _call_ollama(self, prompt: str) -> str:
        """Call ollama API with the prompt."""
        if not self.client:
            raise RuntimeError("HTTP client not initialized")
        
        payload = {
            "model": self.config.ollama.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": self.config.ollama.temperature,
            },
        }
        
        last_error = None
        for attempt in range(self.config.ollama.max_retries + 1):
            try:
                resp = await self.client.post("/api/generate", json=payload)
                resp.raise_for_status()
                data = resp.json()
                return data.get("response", "")
            except httpx.TimeoutException as e:
                last_error = e
                logger.warning(f"Ollama timeout (attempt {attempt + 1}/{self.config.ollama.max_retries + 1})")
            except httpx.HTTPStatusError as e:
                last_error = e
                logger.error(f"Ollama HTTP error: {e.response.status_code} - {e.response.text}")
                break
            except Exception as e:
                last_error = e
                logger.error(f"Ollama call failed: {e}")
                break
        
        raise last_error or RuntimeError("Ollama call failed after retries")
    
    def _parse_response(
        self,
        response: str,
        expected_type: JevPrimitiveType,
    ) -> Union[JevJudgmentResult, JevNonOutcomeResult]:
        """Parse ollama response into structured judgment."""
        try:
            start = response.find("{")
            end = response.rfind("}") + 1
            if start >= 0 and end > start:
                json_str = response[start:end]
                data = json.loads(json_str)
            else:
                raise ValueError("No JSON object found in response")
        except (json.JSONDecodeError, ValueError) as e:
            return JevNonOutcomeResult(
                kind="unevaluable",
                reason=f"Failed to parse response as JSON: {e}",
                retryable=True,
            )
        
        concentration = data.get("confidence_concentration", 0.0)
        decision_mass = data.get("confidence_decision_mass", 0.0)
        confidence = JevConfidence(
            concentration=concentration,
            decision_mass=decision_mass,
        )
        
        flag_for_review = concentration < self.config.confidence_threshold
        
        if expected_type == JevPrimitiveType.NOUL:
            return NoulResult(
                probability=max(0.0, min(1.0, data.get("probability", 0.0))),
                confidence=confidence,
            )
        
        elif expected_type == JevPrimitiveType.CHOICE:
            label_probs = data.get("label_probabilities", {})
            top_label = data.get("top_label", max(label_probs, key=label_probs.get) if label_probs else "")
            return ChoiceResult(
                label_probabilities=label_probs,
                top_label=top_label,
                confidence=confidence,
                flag_for_review=flag_for_review,
            )
        
        elif expected_type == JevPrimitiveType.SCORE:
            return ScoreResult(
                expected_value=data.get("expected_value", 0.0),
                confidence=confidence,
                legend=data.get("legend", {}),
            )
        
        return JevNonOutcomeResult(
            kind="unevaluable",
            reason=f"Unknown primitive type: {expected_type}",
            retryable=False,
        )


# ── KnowledgeBase Adapter (SolScript seam) ──────────────────────────────

class JevKnowledgeBase(KnowledgeBase):
    """KnowledgeBase implementation that wraps the TypeSafe adapter.
    
    This is the SOLScript seam — implements the `query(target_key, context)`
    protocol expected by InferenceEngine.external_knowledge_base.
    
    Jev 4: Full judgment cache with:
    - Cache key: (state_hash, question, expected_outcome_type, model_version, policy_version_hash, adapter_backend)
    - Stale-read-set rejection via read-set digest comparison
    - Replay identity for verification
    - Retention policies with TTL and eviction rules
    - Source-of-truth placement: evidence store (primary), cache store (secondary)
    - Negative case handling for stale, drifted, mismatched versions
    """
    
    def __init__(
        self, 
        adapter: TypeSafeAdapter, 
        default_primitive: JevPrimitiveType = JevPrimitiveType.NOUL,
        retention_policy: Optional[CacheRetentionPolicy] = None,
        stale_rejection: Optional[StaleReadSetRejection] = None,
    ):
        self.adapter = adapter
        self.default_primitive = default_primitive
        self.retention_policy = retention_policy or CacheRetentionPolicy()
        self.stale_rejection = stale_rejection or StaleReadSetRejection()
        
        # Internal cache: key_string -> CacheEntry
        self._cache: Dict[str, CacheEntry] = {}
        
        # Evidence store for canonical records (append-only)
        self._evidence_store: Dict[str, CanonicalJudgmentRecord] = {}
        
        # Cache statistics
        self._stats = {
            "hits": 0,
            "misses": 0,
            "evictions": 0,
            "stale_rejections": 0,
            "validation_failures": 0,
        }
    
    def _make_judgment_cache_key(self, key: str, context: Dict[str, Any]) -> JudgmentCacheKey:
            """Create a full JudgmentCacheKey per Jev 4 doctrine.
        
            NOTE: read_set_digest is NOT part of the cache key — it's stored separately
            in CacheEntry for stale read-set detection."""
            # Exclude read_set_digest from cache key computation so that
            # stale read-set detection works via validation comparison
            cache_context = {k: v for k, v in context.items() if k != "read_set_digest"}
            state_str = json.dumps(cache_context, sort_keys=True, default=str)[:1000]
            state_hash = hashlib.sha256(state_str.encode()).hexdigest()[:16]
        
            model_version = getattr(self.adapter.config, 'model_version', 'jev-latest')
            if hasattr(self.adapter.config, 'ollama') and hasattr(self.adapter.config.ollama, 'model'):
                model_version = self.adapter.config.ollama.model
        
            policy_version_hash = context.get("policy_version_hash", "policy-v1")
            adapter_backend = self.adapter.get_backend_identity()
        
            return JudgmentCacheKey(
                state_hash=state_hash,
                question=key,
                expected_outcome_type=self.default_primitive,
                model_version=model_version,
                policy_version_hash=policy_version_hash,
                adapter_backend=adapter_backend,
            )

    def _make_cache_key(self, key: str, context: Dict[str, Any]) -> str:
        """Create cache key string from JudgmentCacheKey."""
        return self._make_judgment_cache_key(key, context).to_string()
    
    def _compute_read_set_digest(self, context: Dict[str, Any]) -> str:
        """Compute SHA-256 digest of the read-set for stale detection.
        
        If read_set_digest is explicitly provided in context, use it directly.
        Otherwise compute from the context data (excluding metadata fields)."""
        # Use explicitly provided digest if available
        if "read_set_digest" in context:
            return context["read_set_digest"]
        
        # Otherwise compute from context data
        read_set_data = {
            k: v for k, v in context.items() 
            if not k.startswith("_") and k not in ["read_set_digest", "policy_version_hash"]
        }
        read_set_str = json.dumps(read_set_data, sort_keys=True, default=str)
        return hashlib.sha256(read_set_str.encode()).hexdigest()[:32]
    
    def _validate_cache_entry(self, entry: CacheEntry, context: Dict[str, Any]) -> CacheValidationResult:
        """Validate a cache entry against current context."""
        key = entry.key
        
        # Check model version
        if self.retention_policy.evict_on_model_version_change:
            current_model = getattr(self.adapter.config, 'model_version', 'jev-latest')
            if hasattr(self.adapter.config, 'ollama') and hasattr(self.adapter.config.ollama, 'model'):
                current_model = self.adapter.config.ollama.model
            if key.model_version != current_model:
                return CacheValidationResult(
                    valid=False,
                    status=CacheEntryStatus.MODEL_VERSION_MISMATCH,
                    detail=f"Model version mismatch: cached={key.model_version}, current={current_model}",
                    validated_key=key,
                )
        
        # Check policy version
        if self.retention_policy.evict_on_policy_version_change:
            current_policy = context.get("policy_version_hash", "policy-v1")
            if key.policy_version_hash != current_policy:
                return CacheValidationResult(
                    valid=False,
                    status=CacheEntryStatus.POLICY_VERSION_MISMATCH,
                    detail=f"Policy version mismatch: cached={key.policy_version_hash}, current={current_policy}",
                    validated_key=key,
                )
        
        # Check backend
        if self.retention_policy.evict_on_backend_change:
            current_backend = self.adapter.get_backend_identity()
            if key.adapter_backend != current_backend:
                return CacheValidationResult(
                    valid=False,
                    status=CacheEntryStatus.BACKEND_MISMATCH,
                    detail=f"Backend mismatch: cached={key.adapter_backend}, current={current_backend}",
                    validated_key=key,
                )
        
        # Check read-set staleness
        if self.stale_rejection.enabled:
            current_digest = self._compute_read_set_digest(context)
            if self.stale_rejection.reject_on_digest_mismatch:
                if entry.read_set_digest != current_digest:
                    return CacheValidationResult(
                        valid=False,
                        status=CacheEntryStatus.STALE_READ_SET,
                        detail=f"Read-set digest mismatch: cached={entry.read_set_digest[:16]}, current={current_digest[:16]}",
                        validated_key=key,
                        current_read_set_digest=current_digest,
                        expected_read_set_digest=entry.read_set_digest,
                    )
            
            # Check age
            age = datetime.utcnow() - entry.created_at
            if age > self.stale_rejection.max_read_set_age:
                return CacheValidationResult(
                    valid=False,
                    status=CacheEntryStatus.STALE_READ_SET,
                    detail=f"Read-set age {age} exceeds max {self.stale_rejection.max_read_set_age}",
                    validated_key=key,
                )
        
        # Check TTL
        age = datetime.utcnow() - entry.created_at
        if age > self.retention_policy.max_ttl:
            return CacheValidationResult(
                valid=False,
                status=CacheEntryStatus.EVICTED,
                detail=f"Entry age {age} exceeds max TTL {self.retention_policy.max_ttl}",
                validated_key=key,
            )
        
        # All validations passed
        return CacheValidationResult(
            valid=True,
            detail="Cache entry valid",
            validated_key=key,
        )
    
    def _evict_if_needed(self) -> None:
        """Evict entries if cache exceeds limits."""
        # Check total entries
        if len(self._cache) >= self.retention_policy.max_total_entries:
            # Evict oldest entries
            sorted_entries = sorted(
                self._cache.items(), 
                key=lambda kv: kv[1].accessed_at
            )
            to_evict = len(self._cache) - self.retention_policy.max_total_entries + 1
            for i in range(to_evict):
                key_str, entry = sorted_entries[i]
                entry.validation_status = CacheEntryStatus.EVICTED
                del self._cache[key_str]
                self._stats["evictions"] += 1
        
        # Check per-group limit (same state_hash + question)
        groups: Dict[str, List[tuple]] = {}
        for key_str, entry in self._cache.items():
            group_key = f"{entry.key.state_hash}:{entry.key.question}"
            if group_key not in groups:
                groups[group_key] = []
            groups[group_key].append((key_str, entry))
        
        for group_key, entries in groups.items():
            if len(entries) > self.retention_policy.max_entries_per_group:
                # Evict oldest in group
                entries.sort(key=lambda kv: kv[1].accessed_at)
                to_evict = len(entries) - self.retention_policy.max_entries_per_group
                for i in range(to_evict):
                    key_str, entry = entries[i]
                    entry.validation_status = CacheEntryStatus.EVICTED
                    del self._cache[key_str]
                    self._stats["evictions"] += 1
    
    def _write_canonical_record(self, entry: CacheEntry, context: Dict[str, Any]) -> CanonicalJudgmentRecord:
        """Write canonical judgment record to evidence store."""
        record = CanonicalJudgmentRecord(
            record_id=str(uuid.uuid4()),
            cache_key=entry.key,
            outcome=entry.outcome,
            provenance=entry.provenance,
            calibration=entry.provenance.get("calibration", {}),
            store_location=JudgmentStoreLocation.EVIDENCE_STORE,
            validation_status=entry.validation_status,
            created_at=entry.created_at,
            retention_policy=self.retention_policy,
        )
        self._evidence_store[record.record_id] = record
        return record
    
    async def query(self, key: str, context: Dict[str, Any]) -> Optional[Any]:
        """Query the TypeSafe adapter for a judgment with full Jev 4 cache logic.
        
        Args:
            key: The proposition/question key (maps to JevInquiry.question)
            context: The evaluation frame context (maps to JevInquiry.readSetScope)
        
        Returns:
            Structured judgment result or JevNonOutcomeResult if unevaluable
        """
        # Create full cache key
        judgment_key = self._make_judgment_cache_key(key, context)
        cache_key_str = judgment_key.to_string()
        
        # Compute read-set digest for stale detection
        read_set_digest = self._compute_read_set_digest(context)
        
        # Check cache first
        if cache_key_str in self._cache:
            entry = self._cache[cache_key_str]
            
            # Validate the entry
            validation = self._validate_cache_entry(entry, context)
            
            if validation.valid:
                # Cache hit - update stats and access time
                entry.accessed_at = datetime.utcnow()
                entry.access_count += 1
                self._stats["hits"] += 1
                logger.debug(f"Cache hit for key: {key}")
                return entry.outcome
            else:
                # Cache validation failed - handle per stale rejection config
                self._stats["validation_failures"] += 1
                logger.warning(f"Cache validation failed for {key}: {validation.detail}")
                
                if validation.status == CacheEntryStatus.STALE_READ_SET:
                    self._stats["stale_rejections"] += 1
                    if self.stale_rejection.on_stale == "reject":
                        return JevNonOutcomeResult(
                            kind="stale",
                            reason=validation.detail,
                            retryable=True,
                        )
                    elif self.stale_rejection.on_stale == "return_stale_with_warning":
                        # Return stale entry with warning (for emergency use)
                        logger.warning(f"Returning stale entry for {key}: {validation.detail}")
                        return entry.outcome
                    # "retry_fresh" falls through to fresh query
                # For other failures, fall through to fresh query
        
        # Cache miss or validation failure with retry
        self._stats["misses"] += 1
        
        try:
            # Execute the judgment
            results = await self.adapter.system_one(
                state=context,
                questions=[key],
                expected_types=[self.default_primitive],
            )
            
            if results:
                result = results[0]
                
                # Create cache entry for successful judgments
                if not isinstance(result, JevNonOutcomeResult):
                    # Build provenance
                    provenance = {
                        "source_system": "type-safe-system-one" if "jev" in self.adapter.get_backend_identity() else "system-one-adapter",
                        "verifier_method": "concentration" if self.default_primitive == JevPrimitiveType.NOUL else "rubric" if self.default_primitive == JevPrimitiveType.SCORE else "adapter-llm",
                        "policy_version_hash": context.get("policy_version_hash", "policy-v1"),
                        "read_set_digest": read_set_digest,
                        "evaluator_ref": self.adapter.get_backend_identity(),
                        "adapter_backend": self.adapter.get_backend_identity(),
                        "calibration": self.adapter.config.calibration or {},
                    }
                    
                    calibration = self.adapter.config.calibration or {}
                    
                    entry = CacheEntry(
                        key=judgment_key,
                        outcome=result,
                        provenance=provenance,
                        calibration=calibration,
                        read_set_digest=read_set_digest,
                    )
                    
                    # Evict if needed before adding
                    self._evict_if_needed()
                    
                    # Store in cache
                    self._cache[cache_key_str] = entry
                    
                    # Write canonical record to evidence store
                    canonical_record = self._write_canonical_record(entry, context)
                    logger.debug(f"Cached judgment for {key} (record: {canonical_record.record_id})")
                
                return result
            
        except Exception as e:
            logger.error(f"JevKnowledgeBase query failed for '{key}': {e}")
            # Fail-visible: return explicit non-outcome instead of None
            return JevNonOutcomeResult(
                kind="unevaluable",
                reason=f"Adapter error: {e}",
                retryable=True,
            )
        
        return JevNonOutcomeResult(
            kind="unevaluable",
            reason="No results returned from adapter",
            retryable=True,
        )
    
        """Compute SHA-256 digest of the read-set for stale detection."""
        read_set_data = {
            k: v for k, v in context.items() 
            if not k.startswith("_") and k not in ["read_set_digest", "policy_version_hash"]
        }
        read_set_str = json.dumps(read_set_data, sort_keys=True, default=str)
        return hashlib.sha256(read_set_str.encode()).hexdigest()[:32]
    
    def _write_canonical_record(self, entry: CacheEntry, context: Dict[str, Any]) -> CanonicalJudgmentRecord:
        """Write canonical judgment record to evidence store."""
        record = CanonicalJudgmentRecord(
            record_id=str(uuid.uuid4()),
            cache_key=entry.key,
            outcome=entry.outcome,
            provenance=entry.provenance,
            calibration=entry.provenance.get("calibration", {}),
            store_location=JudgmentStoreLocation.EVIDENCE_STORE,
            validation_status=entry.validation_status,
            created_at=entry.created_at,
            retention_policy=self.retention_policy,
        )
        self._evidence_store[record.record_id] = record
        return record
    
    def replay_judgment(self, record_id: str, replay_context: Dict[str, Any]) -> Optional[ReplayIdentity]:
        """Replay a judgment from the evidence store to verify reproducibility."""
        if record_id not in self._evidence_store:
            return None
        
        record = self._evidence_store[record_id]
        
        # Re-execute with replay context
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                self.adapter.system_one(
                    state=replay_context,
                    questions=[record.cache_key.question],
                    expected_types=[record.cache_key.expected_outcome_type],
                )
            )
            outcome_identical = False
            difference = None
            if result and not isinstance(result[0], JevNonOutcomeResult):
                # Compare outcomes (simplified)
                outcome_identical = str(result[0]) == str(record.outcome)
                if not outcome_identical:
                    difference = f"Original: {record.outcome}, Replay: {result[0]}"
        finally:
            loop.close()
        
        replay = ReplayIdentity(
            replay_id=str(uuid.uuid4()),
            original_cache_key=record.cache_key,
            original_judgment=record.outcome,
            replay_context=replay_context,
            outcome_identical=outcome_identical,
            difference=difference,
        )
        
        # Update record with replay identity
        record.replay_identity = replay
        return replay
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        return {
            **self._stats,
            "cache_size": len(self._cache),
            "evidence_store_size": len(self._evidence_store),
            "hit_rate": self._stats["hits"] / max(1, self._stats["hits"] + self._stats["misses"]),
        }
    
    def get_canonical_records(self) -> List[CanonicalJudgmentRecord]:
        """Get all canonical judgment records from evidence store."""
        return list(self._evidence_store.values())
    
    def add_knowledge(self, key: str, value: Any, context: Optional[Dict[str, Any]] = None) -> None:
        """Add manual knowledge (from base KnowledgeBase)."""
        if context:
            cache_key = self._make_cache_key(key, context)
            read_set_digest = self._compute_read_set_digest(context)
        else:
            cache_key = f"manual:{key}"
            read_set_digest = ""
        # Store as manual entry with special provenance
        judgment_key = self._make_judgment_cache_key(key, context or {})
        entry = CacheEntry(
            key=judgment_key,
            outcome=value,
            provenance={"source_system": "manual", "verifier_method": "manual"},
            calibration={},
            read_set_digest=read_set_digest,
        )
        self._cache[cache_key] = entry
    
    def add_pattern(self, pattern: str, key: str, resolver: callable) -> None:
        """Add pattern-based knowledge (from base KnowledgeBase)."""
        # Defer to base class behavior for patterns
        pass


# ── Factory for easy integration ────────────────────────────────────────

async def create_jev_adapter(
    ollama_url: str = "http://helium:11434",
    model: str = "qwen2.5-coder:latest",
) -> tuple[OllamaTypeSafeAdapter, JevKnowledgeBase]:
    """Factory to create adapter and knowledge base for SOLScript integration.
    
    Usage:
        adapter, kb = await create_jev_adapter()
        async with adapter:
            engine.external_knowledge_base = kb
            # ... use engine
    """
    config = JevAdapterConfig(
        ollama=OllamaConfig(base_url=ollama_url, model=model),
    )
    adapter = OllamaTypeSafeAdapter(config)
    kb = JevKnowledgeBase(adapter)
    return adapter, kb


# ── Synchronous wrapper for non-async contexts ──────────────────────────

class SyncJevKnowledgeBase(KnowledgeBase):
    """Synchronous wrapper around JevKnowledgeBase for non-async contexts.
    
    Uses the full Jev 4 cache implementation with synchronous execution.
    """
    
    def __init__(
        self, 
        adapter: TypeSafeAdapter, 
        default_primitive: JevPrimitiveType = JevPrimitiveType.NOUL,
        retention_policy: Optional[CacheRetentionPolicy] = None,
        stale_rejection: Optional[StaleReadSetRejection] = None,
    ):
        self.adapter = adapter
        self.default_primitive = default_primitive
        self.retention_policy = retention_policy or CacheRetentionPolicy()
        self.stale_rejection = stale_rejection or StaleReadSetRejection()
        
        # Internal cache: key_string -> CacheEntry
        self._cache: Dict[str, CacheEntry] = {}
        
        # Evidence store for canonical records (append-only)
        self._evidence_store: Dict[str, CanonicalJudgmentRecord] = {}
        
        # Cache statistics
        self._stats = {
            "hits": 0,
            "misses": 0,
            "evictions": 0,
            "stale_rejections": 0,
            "validation_failures": 0,
        }
    
    def _make_judgment_cache_key(self, key: str, context: Dict[str, Any]) -> JudgmentCacheKey:
        """Create a full JudgmentCacheKey per Jev 4 doctrine.
        
        NOTE: read_set_digest is NOT part of the cache key — it's stored separately
        in CacheEntry for stale read-set detection."""
        # Exclude read_set_digest from cache key computation so that
        # stale read-set detection works via validation comparison
        cache_context = {k: v for k, v in context.items() if k != "read_set_digest"}
        state_str = json.dumps(cache_context, sort_keys=True, default=str)[:1000]
        state_hash = hashlib.sha256(state_str.encode()).hexdigest()[:16]
        
        model_version = getattr(self.adapter.config, 'model_version', 'jev-latest')
        if hasattr(self.adapter.config, 'ollama') and hasattr(self.adapter.config.ollama, 'model'):
            model_version = self.adapter.config.ollama.model
        
        policy_version_hash = context.get("policy_version_hash", "policy-v1")
        adapter_backend = self.adapter.get_backend_identity()
        
        return JudgmentCacheKey(
            state_hash=state_hash,
            question=key,
            expected_outcome_type=self.default_primitive,
            model_version=model_version,
            policy_version_hash=policy_version_hash,
            adapter_backend=adapter_backend,
        )
    
    def _make_cache_key(self, key: str, context: Dict[str, Any]) -> str:
        return self._make_judgment_cache_key(key, context).to_string()
    
    def _compute_read_set_digest(self, context: Dict[str, Any]) -> str:
        """Compute SHA-256 digest of the read-set for stale detection.
        
        If read_set_digest is explicitly provided in context, use it directly.
        Otherwise compute from the context data (excluding metadata fields)."""
        # Use explicitly provided digest if available
        if "read_set_digest" in context:
            return context["read_set_digest"]
        
        # Otherwise compute from context data
        read_set_data = {
            k: v for k, v in context.items() 
            if not k.startswith("_") and k not in ["read_set_digest", "policy_version_hash"]
        }
        read_set_str = json.dumps(read_set_data, sort_keys=True, default=str)
        return hashlib.sha256(read_set_str.encode()).hexdigest()[:32]
    
    def _validate_cache_entry(self, entry: CacheEntry, context: Dict[str, Any]) -> CacheValidationResult:
        key = entry.key
        
        # Check model version
        if self.retention_policy.evict_on_model_version_change:
            current_model = getattr(self.adapter.config, 'model_version', 'jev-latest')
            if hasattr(self.adapter.config, 'ollama') and hasattr(self.adapter.config.ollama, 'model'):
                current_model = self.adapter.config.ollama.model
            if key.model_version != current_model:
                return CacheValidationResult(
                    valid=False,
                    status=CacheEntryStatus.MODEL_VERSION_MISMATCH,
                    detail=f"Model version mismatch: cached={key.model_version}, current={current_model}",
                    validated_key=key,
                )
        
        # Check policy version
        if self.retention_policy.evict_on_policy_version_change:
            current_policy = context.get("policy_version_hash", "policy-v1")
            if key.policy_version_hash != current_policy:
                return CacheValidationResult(
                    valid=False,
                    status=CacheEntryStatus.POLICY_VERSION_MISMATCH,
                    detail=f"Policy version mismatch: cached={key.policy_version_hash}, current={current_policy}",
                    validated_key=key,
                )
        
        # Check backend
        if self.retention_policy.evict_on_backend_change:
            current_backend = self.adapter.get_backend_identity()
            if key.adapter_backend != current_backend:
                return CacheValidationResult(
                    valid=False,
                    status=CacheEntryStatus.BACKEND_MISMATCH,
                    detail=f"Backend mismatch: cached={key.adapter_backend}, current={current_backend}",
                    validated_key=key,
                )
        
        # Check read-set staleness
        if self.stale_rejection.enabled:
            current_digest = self._compute_read_set_digest(context)
            if self.stale_rejection.reject_on_digest_mismatch:
                if entry.read_set_digest != current_digest:
                    return CacheValidationResult(
                        valid=False,
                        status=CacheEntryStatus.STALE_READ_SET,
                        detail=f"Read-set digest mismatch: cached={entry.read_set_digest[:16]}, current={current_digest[:16]}",
                        validated_key=key,
                        current_read_set_digest=current_digest,
                        expected_read_set_digest=entry.read_set_digest,
                    )
            
            age = datetime.utcnow() - entry.created_at
            if age > self.stale_rejection.max_read_set_age:
                return CacheValidationResult(
                    valid=False,
                    status=CacheEntryStatus.STALE_READ_SET,
                    detail=f"Read-set age {age} exceeds max {self.stale_rejection.max_read_set_age}",
                    validated_key=key,
                )
        
        # Check TTL
        age = datetime.utcnow() - entry.created_at
        if age > self.retention_policy.max_ttl:
            return CacheValidationResult(
                valid=False,
                status=CacheEntryStatus.EVICTED,
                detail=f"Entry age {age} exceeds max TTL {self.retention_policy.max_ttl}",
                validated_key=key,
            )
        
        return CacheValidationResult(
            valid=True,
            detail="Cache entry valid",
            validated_key=key,
        )
    
    def _evict_if_needed(self) -> None:
        if len(self._cache) >= self.retention_policy.max_total_entries:
            sorted_entries = sorted(
                self._cache.items(), 
                key=lambda kv: kv[1].accessed_at
            )
            to_evict = len(self._cache) - self.retention_policy.max_total_entries + 1
            for i in range(to_evict):
                key_str, entry = sorted_entries[i]
                entry.validation_status = CacheEntryStatus.EVICTED
                del self._cache[key_str]
                self._stats["evictions"] += 1
        
        groups: Dict[str, List[tuple]] = {}
        for key_str, entry in self._cache.items():
            group_key = f"{entry.key.state_hash}:{entry.key.question}"
            if group_key not in groups:
                groups[group_key] = []
            groups[group_key].append((key_str, entry))
        
        for group_key, entries in groups.items():
            if len(entries) > self.retention_policy.max_entries_per_group:
                entries.sort(key=lambda kv: kv[1].accessed_at)
                to_evict = len(entries) - self.retention_policy.max_entries_per_group
                for i in range(to_evict):
                    key_str, entry = entries[i]
                    entry.validation_status = CacheEntryStatus.EVICTED
                    del self._cache[key_str]
                    self._stats["evictions"] += 1
    
    def _write_canonical_record(self, entry: CacheEntry, context: Dict[str, Any]) -> CanonicalJudgmentRecord:
        record = CanonicalJudgmentRecord(
            record_id=str(uuid.uuid4()),
            cache_key=entry.key,
            outcome=entry.outcome,
            provenance=entry.provenance,
            calibration=entry.provenance.get("calibration", {}),
            store_location=JudgmentStoreLocation.EVIDENCE_STORE,
            validation_status=entry.validation_status,
            created_at=entry.created_at,
            retention_policy=self.retention_policy,
        )
        self._evidence_store[record.record_id] = record
        return record
    
    def query(self, key: str, context: Dict[str, Any]) -> Optional[Any]:
        """Synchronous query — runs async internally.
        
        Handles both cases: when an event loop is running (e.g., in pytest-asyncio)
        and when no loop exists."""
        try:
            loop = asyncio.get_running_loop()
            # If we get here, a loop is running — we can't use run_until_complete
            # Create a new thread to run the async query
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(asyncio.run, self._async_query(key, context))
                return future.result()
        except RuntimeError:
            # No running loop. Run the coroutine on a fresh private loop and
            # always close it — never call asyncio.set_event_loop, which leaks
            # global loop state (a closed loop left set breaks any later
            # asyncio.run / get_event_loop caller in the same process).
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(self._async_query(key, context))
            finally:
                loop.close()
    
    async def _async_query(self, key: str, context: Dict[str, Any]) -> Optional[Any]:
        judgment_key = self._make_judgment_cache_key(key, context)
        cache_key_str = judgment_key.to_string()
        read_set_digest = self._compute_read_set_digest(context)
        
        if cache_key_str in self._cache:
            entry = self._cache[cache_key_str]
            validation = self._validate_cache_entry(entry, context)
            
            if validation.valid:
                entry.accessed_at = datetime.utcnow()
                entry.access_count += 1
                self._stats["hits"] += 1
                logger.debug(f"Sync cache hit for key: {key}")
                return entry.outcome
            else:
                self._stats["validation_failures"] += 1
                logger.warning(f"Sync cache validation failed for {key}: {validation.detail}")
                
                if validation.status == CacheEntryStatus.STALE_READ_SET:
                    self._stats["stale_rejections"] += 1
                    if self.stale_rejection.on_stale == "reject":
                        return JevNonOutcomeResult(
                            kind="stale",
                            reason=validation.detail,
                            retryable=True,
                        )
                    elif self.stale_rejection.on_stale == "return_stale_with_warning":
                        logger.warning(f"Returning stale entry for {key}: {validation.detail}")
                        return entry.outcome
        
        self._stats["misses"] += 1
        
        try:
            results = await self.adapter.system_one(
                state=context,
                questions=[key],
                expected_types=[self.default_primitive],
            )
            
            if results:
                result = results[0]
                
                if not isinstance(result, JevNonOutcomeResult):
                    provenance = {
                        "source_system": "type-safe-system-one" if "jev" in self.adapter.get_backend_identity() else "system-one-adapter",
                        "verifier_method": "concentration" if self.default_primitive == JevPrimitiveType.NOUL else "rubric" if self.default_primitive == JevPrimitiveType.SCORE else "adapter-llm",
                        "policy_version_hash": context.get("policy_version_hash", "policy-v1"),
                        "read_set_digest": read_set_digest,
                        "evaluator_ref": self.adapter.get_backend_identity(),
                        "adapter_backend": self.adapter.get_backend_identity(),
                        "calibration": self.adapter.config.calibration or {},
                    }
                    
                    calibration = self.adapter.config.calibration or {}
                    
                    entry = CacheEntry(
                        key=judgment_key,
                        outcome=result,
                        provenance=provenance,
                        calibration=calibration,
                        read_set_digest=read_set_digest,
                    )
                    
                    self._evict_if_needed()
                    self._cache[cache_key_str] = entry
                    self._write_canonical_record(entry, context)
                
                return result
            
        except Exception as e:
            logger.error(f"SyncJevKnowledgeBase query failed for '{key}': {e}")
            return JevNonOutcomeResult(
                kind="unevaluable",
                reason=f"Adapter error: {e}",
                retryable=True,
            )
        
        return JevNonOutcomeResult(
            kind="unevaluable",
            reason="No results returned from adapter",
            retryable=True,
        )
    
    def _compute_read_set_digest(self, context: Dict[str, Any]) -> str:
        read_set_data = {
            k: v for k, v in context.items() 
            if not k.startswith("_") and k not in ["read_set_digest", "policy_version_hash"]
        }
        read_set_str = json.dumps(read_set_data, sort_keys=True, default=str)
        return hashlib.sha256(read_set_str.encode()).hexdigest()[:32]
    
    def get_cache_stats(self) -> Dict[str, Any]:
        return {
            **self._stats,
            "cache_size": len(self._cache),
            "evidence_store_size": len(self._evidence_store),
            "hit_rate": self._stats["hits"] / max(1, self._stats["hits"] + self._stats["misses"]),
        }
    
    def get_canonical_records(self) -> List[CanonicalJudgmentRecord]:
        return list(self._evidence_store.values())
    
    def add_knowledge(self, key: str, value: Any, context: Optional[Dict[str, Any]] = None) -> None:
        if context:
            cache_key = self._make_cache_key(key, context)
        else:
            cache_key = f"manual:{key}"
        judgment_key = self._make_judgment_cache_key(key, context or {})
        entry = CacheEntry(
            key=judgment_key,
            outcome=value,
            provenance={"source_system": "manual", "verifier_method": "manual"},
            calibration={},
            read_set_digest="",
        )
        self._cache[cache_key] = entry
    
    def add_pattern(self, pattern: str, key: str, resolver: callable) -> None:
        pass


def create_sync_jev_knowledge_base(
    ollama_url: str = "http://helium:11434",
    model: str = "qwen2.5-coder:latest",
) -> SyncJevKnowledgeBase:
    """Create a synchronous JevKnowledgeBase for immediate use."""
    config = JevAdapterConfig(
        ollama=OllamaConfig(base_url=ollama_url, model=model),
    )
    adapter = OllamaTypeSafeAdapter(config)
    return SyncJevKnowledgeBase(adapter)