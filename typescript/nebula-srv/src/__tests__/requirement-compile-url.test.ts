import { describe, expect, it } from "vitest";

import { requirementCompileUrl } from "../routes";

// ── CodeQL SSRF: requirement compile trigger URL (backfill of twin #685) ──
// The Backlog→ToDo trigger interpolates a request-derived requirement id
// into an outgoing URL. encodeURIComponent is the sanitizer CodeQL
// recognises for js/request-forgery: it escapes path separators so a
// crafted id cannot traverse the path or reach another host. A regex guard
// alone is not a sanitizer for that query, so the helper both encodes and
// (at the call site) narrows on the numeric shape requirements ids use.
describe("requirementCompileUrl (CodeQL js/request-forgery fix)", () => {
  it("URI-encodes the id; benign ids are unchanged", () => {
    expect(requirementCompileUrl(42)).toBe(
      "http://localhost:3101/api/requirements/42/compile",
    );
    expect(requirementCompileUrl("2717")).toBe(
      "http://localhost:3101/api/requirements/2717/compile",
    );
  });

  it("escapes path separators so a crafted id cannot retarget the request", () => {
    expect(requirementCompileUrl("../../evil")).toBe(
      "http://localhost:3101/api/requirements/..%2F..%2Fevil/compile",
    );
    expect(requirementCompileUrl("a/b")).toBe(
      "http://localhost:3101/api/requirements/a%2Fb/compile",
    );
  });
});
