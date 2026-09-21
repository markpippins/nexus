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
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
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
        """Execute bounded judgment queries.
        
        Args:
            state: The evaluation frame state (context for the judgments)
            questions: List of bounded questions to evaluate
            expected_types: Optional expected primitive for each question
        
        Returns:
            List of JevJudgmentResult or JevNonOutcomeResult, one per question
        """
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
        # Build the prompt based on expected primitive type
        prompt = self._build_prompt(state, question, expected_type)
        
        # Call ollama with structured output
        response = await self._call_ollama(prompt)
        
        # Parse response into the appropriate primitive
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
        # Keep it bounded to avoid token bloat
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
        # Try to extract JSON from response
        try:
            # Find JSON object in response
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
        
        # Validate confidence
        concentration = data.get("confidence_concentration", 0.0)
        decision_mass = data.get("confidence_decision_mass", 0.0)
        confidence = JevConfidence(
            concentration=concentration,
            decision_mass=decision_mass,
        )
        
        # Check confidence threshold
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
    
    Maps JevInquiry contract to the KnowledgeBase query protocol.
    """
    
    def __init__(self, adapter: TypeSafeAdapter, default_primitive: JevPrimitiveType = JevPrimitiveType.NOUL):
        self.adapter = adapter
        self.default_primitive = default_primitive
        self._cache: Dict[str, Any] = {}  # Simple in-memory cache for spike
    
    async def query(self, key: str, context: Dict[str, Any]) -> Optional[Any]:
        """Query the TypeSafe adapter for a judgment.
        
        Args:
            key: The proposition/question key (maps to JevInquiry.question)
            context: The evaluation frame context (maps to JevInquiry.readSetScope)
        
        Returns:
            Structured judgment result or None if unevaluable
        """
        # Check cache first (per judgment cache doctrine)
        cache_key = self._make_cache_key(key, context)
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            logger.debug(f"Cache hit for key: {key}")
            return cached
        
        try:
            # Execute the judgment
            results = await self.adapter.system_one(
                state=context,
                questions=[key],
                expected_types=[self.default_primitive],
            )
            
            if results:
                result = results[0]
                # Cache successful judgments
                if not isinstance(result, JevNonOutcomeResult):
                    self._cache[cache_key] = result
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
    
    def _make_cache_key(self, key: str, context: Dict[str, Any]) -> str:
        """Create a cache key per judgment cache doctrine:
        (state_hash, question, model_version, policy_version, adapter_backend)"""
        import hashlib
        # Simplified for spike — full implementation in Jev 4
        state_str = json.dumps(context, sort_keys=True, default=str)[:1000]
        state_hash = hashlib.sha256(state_str.encode()).hexdigest()[:16]
        backend = self.adapter.get_backend_identity()
        return f"{state_hash}:{key}:{backend}"
    
    def add_knowledge(self, key: str, value: Any, context: Optional[Dict[str, Any]] = None) -> None:
        """Add manual knowledge (from base KnowledgeBase)."""
        if context:
            cache_key = self._make_cache_key(key, context)
        else:
            cache_key = f"manual:{key}"
        self._cache[cache_key] = value
    
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
    """Synchronous wrapper around JevKnowledgeBase for non-async contexts."""
    
    def __init__(self, adapter: TypeSafeAdapter, default_primitive: JevPrimitiveType = JevPrimitiveType.NOUL):
        self.adapter = adapter
        self.default_primitive = default_primitive
        self._cache: Dict[str, Any] = {}
    
    def query(self, key: str, context: Dict[str, Any]) -> Optional[Any]:
        """Synchronous query — runs async internally."""
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        return loop.run_until_complete(self._async_query(key, context))
    
    async def _async_query(self, key: str, context: Dict[str, Any]) -> Optional[Any]:
        cache_key = self._make_cache_key(key, context)
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        try:
            results = await self.adapter.system_one(
                state=context,
                questions=[key],
                expected_types=[self.default_primitive],
            )
            if results:
                result = results[0]
                if not isinstance(result, JevNonOutcomeResult):
                    self._cache[cache_key] = result
                return result
        except Exception as e:
            logger.error(f"SyncJevKnowledgeBase query failed for '{key}': {e}")
            # Fail-visible: return explicit non-outcome
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
    
    def _make_cache_key(self, key: str, context: Dict[str, Any]) -> str:
        import hashlib
        state_str = json.dumps(context, sort_keys=True, default=str)[:1000]
        state_hash = hashlib.sha256(state_str.encode()).hexdigest()[:16]
        backend = self.adapter.get_backend_identity()
        return f"{state_hash}:{key}:{backend}"
    
    def add_knowledge(self, key: str, value: Any, context: Optional[Dict[str, Any]] = None) -> None:
        if context:
            cache_key = self._make_cache_key(key, context)
        else:
            cache_key = f"manual:{key}"
        self._cache[cache_key] = value


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