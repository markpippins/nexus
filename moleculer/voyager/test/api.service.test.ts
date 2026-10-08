/**
 * Finalhandler HTML parity for the gateway's unmatched-route error page.
 *
 * PR #690 reproduced the page but omitted finalhandler's encodeUrl +
 * escapeHtml transforms, reflecting raw HTML from the request target
 * (tester review @ 2c48a78f: demonstrated reflected XSS). These pin the
 * exact transform pipeline against finalhandler 1.3.2:
 *
 *   msg  = 'Cannot ' + method + ' ' + encodeUrl(pathname)
 *   body = escapeHtml(msg).replace(/\n/g, '<br>').replace(/\x20{2}/g, ' &nbsp;')
 *
 * Hermetic: importing api.service binds nothing (no broker, no HTTP).
 */
import { expressNotFoundHtml } from "../services/api.service";

const page = (method: string, url: string) =>
  expressNotFoundHtml({ method, originalUrl: url });
const pre = (body: string) => /<pre>([\s\S]*?)<\/pre>/.exec(body)![1];

describe("expressNotFoundHtml (finalhandler 1.3.2 byte parity)", () => {
  it("renders the bare document shell", () => {
    expect(page("GET", "/api/definitely/not/a/route")).toBe(
      '<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n' +
        "<title>Error</title>\n</head>\n<body>\n" +
        "<pre>Cannot GET /api/definitely/not/a/route</pre>\n" +
        "</body>\n</html>\n",
    );
  });

  it("percent-encodes a raw tag via encodeUrl — no live <script>", () => {
    const out = page("GET", "/api/<script>alert(document.domain)</script>");
    expect(pre(out)).toBe(
      "Cannot GET /api/%3Cscript%3Ealert(document.domain)%3C/script%3E",
    );
    expect(out).not.toContain("<script>");
  });

  it("percent-encodes quotes and spaces, leaving valid %XX sequences as-is", () => {
    // encodeUrl: '"' (0x22) and space (0x20) are non-URL code points.
    expect(pre(page("GET", '/api/a"onload="x'))).toBe(
      "Cannot GET /api/a%22onload=%22x",
    );
    expect(pre(page("GET", "/api/a b"))).toBe("Cannot GET /api/a%20b");
    // already-encoded input is not double-encoded
    expect(pre(page("GET", "/api/%3Cscript%3E"))).toBe(
      "Cannot GET /api/%3Cscript%3E",
    );
  });

  it("neutralises metacharacters encodeUrl leaves intact via escapeHtml", () => {
    // '&' and "'" are legal path chars, so escapeHtml is the only neutraliser.
    expect(pre(page("GET", "/api/a&b"))).toBe("Cannot GET /api/a&amp;b");
    expect(pre(page("GET", "/api/'q'"))).toBe("Cannot GET /api/&#39;q&#39;");
  });

  it("prints the pathname only — query and fragment excluded", () => {
    expect(pre(page("GET", "/api/stats?x=1"))).toBe("Cannot GET /api/stats");
    expect(pre(page("GET", "/api/stats#frag"))).toBe("Cannot GET /api/stats");
  });

  it("escapes the whole message, including the method", () => {
    // finalhandler escapes msg, not just the pathname.
    expect(pre(page("GET<x>", "/api/x"))).toBe("Cannot GET&lt;x&gt; /api/x");
  });
});
