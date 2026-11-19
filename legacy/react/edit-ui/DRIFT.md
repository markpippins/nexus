# DRIFT.md — edit-ui Client vs Backend

**Date:** 2026-07-23
**Compared:** `src/services/` ↔ no dedicated backend service found in `nexus/typescript/`
**Status:** UI-preference only — no API client to compare

---

## Critical

### C1 — No API Client Found

Edit-ui's service layer consists solely of `ui-preferences.service.ts`, which manages client-side UI state (theme, sidebar visibility, etc.) via Angular signals. No HTTP API calls are made.

| Expected Backend | Service | Found? |
|---|---|---|
| Generic REST / CRUD for code editing | `edit-srv` or similar in `nexus/typescript/` | ❌ |

Edit-ui appears to be a code editor component that stores preferences locally and communicates indirectly (e.g., via the nexus-console event bus) rather than through a dedicated backend.

---

## Summary

| Priority | Area | Notes |
|---|---|---|
| **None** | No drift possible | No backend API client to compare against |
