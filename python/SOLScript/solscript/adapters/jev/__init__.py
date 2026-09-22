"""Jev (TypeSafe System One) adapter package.

Provides the OllamaTypeSafeAdapter and JevKnowledgeBase for integrating
TypeSafe calibrated judgments into the SOLScript evaluation chain via
InferenceEngine.external_knowledge_base.

Jev 4 additions: CacheRetentionPolicy, StaleReadSetRejection, CacheEntryStatus,
JudgmentCacheKey, CacheValidationResult, ReplayIdentity, JudgmentStoreLocation,
CanonicalJudgmentRecord, CacheEntry.
"""
from .adapter import (
    JevPrimitiveType,
    NoulResult,
    ChoiceResult,
    ScoreResult,
    JevJudgmentResult,
    JevNonOutcomeResult,
    OllamaConfig,
    JevAdapterConfig,
    TypeSafeAdapter,
    OllamaTypeSafeAdapter,
    JevKnowledgeBase,
    SyncJevKnowledgeBase,
    create_jev_adapter,
    create_sync_jev_knowledge_base,
    # Jev 4 cache models
    CacheRetentionPolicy,
    StaleReadSetRejection,
    CacheEntryStatus,
    JudgmentCacheKey,
    CacheValidationResult,
    ReplayIdentity,
    JudgmentStoreLocation,
    CanonicalJudgmentRecord,
    CacheEntry,
)

__all__ = [
    "JevPrimitiveType",
    "NoulResult",
    "ChoiceResult", 
    "ScoreResult",
    "JevJudgmentResult",
    "JevNonOutcomeResult",
    "OllamaConfig",
    "JevAdapterConfig",
    "TypeSafeAdapter",
    "OllamaTypeSafeAdapter",
    "JevKnowledgeBase",
    "SyncJevKnowledgeBase",
    "create_jev_adapter",
    "create_sync_jev_knowledge_base",
    # Jev 4 cache models
    "CacheRetentionPolicy",
    "StaleReadSetRejection",
    "CacheEntryStatus",
    "JudgmentCacheKey",
    "CacheValidationResult",
    "ReplayIdentity",
    "JudgmentStoreLocation",
    "CanonicalJudgmentRecord",
    "CacheEntry",
]