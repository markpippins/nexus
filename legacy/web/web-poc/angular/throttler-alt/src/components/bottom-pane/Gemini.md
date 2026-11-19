# "Idea Stream" Documentation

This document describes the "Idea Stream," the application's multi-faceted contextual content view, which is managed by the root `AppComponent`.

## Feature Overview

The "Idea Stream" is a resizable and collapsible pane located at the bottom of the main content area, directly below the file explorer(s). It is not part of the `FileExplorerComponent` itself, but is a sibling component managed by the `AppComponent`.

Its purpose is to provide users with dynamically fetched, relevant content related to their current context. It displays an interleaved feed of mock data from various sources, including web search results, images, videos, academic papers, and AI-generated summaries.

### Key Features:
-   **Adaptive Layout:** The stream's layout automatically adapts to the active file explorer's display mode. When the explorer is in **Grid View**, the stream shows content as a grid of rich "Card" components. When in **List View**, it shows a more compact list of "List Item" components.
-   **Source Filtering:** A toolbar allows users to filter the stream content by source (e.g., show only images and videos).
-   **Sorting:** Content can be sorted by relevance, title, source, or date.
-   **Split-View Awareness:** In split-view mode, the stream can be configured to show content relevant to the left pane, right pane, active pane, or a combined feed from both panes.
-   **Bookmarking:** Any item in the stream can be saved as a bookmark, which associates it with the folder currently open in the active file explorer pane.

## Stream Result Components

The stream is built from a suite of presentational components, each tailored to display a specific type of search result. They are organized into two visual styles:

### `stream-cards`
Used for the grid view display.
-   **`web-result-card`**: Displays mock web search results.
-   **`image-result-card`**: Displays mock image results.
-   **`gemini-result-card`**: Displays a formatted text block for a mock generative AI response.
-   **`youtube-result-card`**: Displays mock video results.
-   **`academic-result-card`**: Displays mock academic paper results.

### `stream-list-items`
Used for the list view display. This directory contains a list-item equivalent for each of the card components above, providing a more compact representation of the same data.