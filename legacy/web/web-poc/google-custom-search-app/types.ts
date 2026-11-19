
export interface SearchResultItem {
  title: string;
  link: string;
  snippet: string;
}

export interface SearchResult {
  items: SearchResultItem[];
  rawResponse: object;
}
