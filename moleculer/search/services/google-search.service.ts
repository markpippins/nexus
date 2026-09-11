import { Service, ServiceBroker, Context, Errors } from "moleculer";
import axios from "axios";

interface GoogleSearchParams {
  query: string;
  token?: string;
}

interface SearchResultItem {
  title: string;
  link: string;
  snippet: string;
  // Canonical item contract (slice-1, Option C): displayLink is required.
  // Legacy already carries it and IdeaStream renders it as `source`.
  displayLink: string;
}

interface GoogleSearchResponse {
  items: SearchResultItem[];
  searchInformation?: {
    totalResults: string;
    searchTime: number;
  };
  // Echo of the caller's token when supplied (D4). Reserved for slice-3
  // rate-limit accounting; echoing the caller's own correlation key back
  // to the same caller is not a secret leak. Absent when not supplied.
  token?: string;
}

// Typed search errors (D3: non-2xx is the permanent moleculer convention).
// The {code, retryable} pair lives in `data` so it survives moleculer-web
// JSON serialization; `retryable` is also set on the instance for broker
// retry-policy consumers.
export const SearchErrorCode = {
  CREDENTIALS: "SEARCH_CREDENTIALS",
  PROVIDER: "SEARCH_PROVIDER",
} as const;

function searchError(
  code: keyof typeof SearchErrorCode,
  message: string,
  retryable: boolean,
  extra: Record<string, unknown> = {}
): Errors.MoleculerError {
  const codeValue = SearchErrorCode[code];
  const err = new Errors.MoleculerError(message, 500, codeValue, {
    code: codeValue,
    retryable,
    provider: "google",
    ...extra,
  });
  err.retryable = retryable;
  return err;
}

// displayLink fallback: Google returns it per item; if absent, derive the
// registrable hostname from the link so every item still satisfies the
// canonical contract. Final fallback is the link itself (never undefined).
function displayLinkOf(item: any): string {
  if (item.displayLink) return item.displayLink;
  try {
    return new URL(item.link).hostname;
  } catch {
    return item.link;
  }
}

export default class GoogleSearchService extends Service {
  private apiKey: string;
  private searchEngineId: string;

  constructor(broker: ServiceBroker) {
    super(broker);

    this.parseServiceSchema({
      name: "google-search",

      settings: {
        apiKey: process.env.GOOGLE_API_KEY || "",
        searchEngineId: process.env.GOOGLE_SEARCH_ENGINE_ID || "",
      },

      actions: {
        simpleSearch: {
          params: {
            query: "string",
            token: { type: "string", optional: true }
          },
          async handler(ctx: Context<GoogleSearchParams>): Promise<GoogleSearchResponse> {
            return this.performSearch(ctx.params.query, ctx.params.token);
          }
        },

        health: {
          async handler(): Promise<{ status: string; service: string }> {
            return {
              status: "ok",
              service: "google-search"
            };
          }
        }
      },

      started: async () => {
        this.apiKey = this.settings.apiKey;
        this.searchEngineId = this.settings.searchEngineId;

        if (!this.apiKey || !this.searchEngineId) {
          this.logger.warn("Google API credentials not configured. Set GOOGLE_API_KEY and GOOGLE_SEARCH_ENGINE_ID environment variables.");
        } else {
          this.logger.info("Google Search Service initialized with API credentials");
        }
      }
    });

    this.apiKey = "";
    this.searchEngineId = "";
  }

  // NOTE: several existing tests call performSearch(query) directly (token
  // omitted). The token stays optional end-to-end: absent means unkeyed.
  async performSearch(query: string, token?: string): Promise<GoogleSearchResponse> {
    if (!this.apiKey || !this.searchEngineId) {
      throw searchError(
        "CREDENTIALS",
        "Google API credentials not configured",
        false
      );
    }

    const url = `https://www.googleapis.com/customsearch/v1`;

    try {
      const response = await axios.get(url, {
        params: {
          key: this.apiKey,
          cx: this.searchEngineId,
          q: query
        }
      });

      const items: SearchResultItem[] = response.data.items?.map((item: any) => ({
        title: item.title,
        link: item.link,
        snippet: item.snippet,
        displayLink: displayLinkOf(item),
      })) || [];

      return {
        items,
        searchInformation: response.data.searchInformation,
        ...(token !== undefined ? { token } : {}),
      };
    } catch (error: any) {
      this.logger.error("Google Search API error:", error.message);
      if (error.response) {
        this.logger.error("Google API response status:", error.response.status);
        this.logger.error("Google API response data:", JSON.stringify(error.response.data));
      }
      // Retryable on network failure (no status), 429, or 5xx. Other 4xx
      // (e.g. 400 invalid argument / bad cx) will fail identically on retry.
      const status: number | undefined = error.response?.status;
      const retryable = status == null || status === 429 || status >= 500;
      throw searchError(
        "PROVIDER",
        `Failed to perform search: ${error.message}`,
        retryable,
        status !== undefined ? { status } : {}
      );
    }
  }
}