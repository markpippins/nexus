# `ChatComponent` Documentation (`/src/components/chat/Gemini.md`)

This component provides a chat interface within a pane in the `SidebarComponent`.

## Core Responsibilities

1.  **Real-time AI Chat:** If a Gemini API key is successfully configured in the environment (`process.env.API_KEY`), this component initializes a real chat session with the `gemini-2.5-flash` model. It handles streaming responses, creating a fluid, real-time conversational experience.
2.  **Demo Mode Fallback:** If the API key is missing or invalid, the component gracefully falls back to a "demo mode." In this mode, the UI remains functional, but instead of communicating with the AI, it simply echoes the user's messages back to them with a "You said:" prefix. This ensures the UI is always interactive and provides clear feedback about the connection status.
3.  **Standard Chat UI:** It provides a familiar chat interface with a scrollable conversation history, a text input area that grows with content, and a send button that shows a loading state during AI responses.

## API and Data Flow

-   **Inputs:** None.
-   **Outputs:** None.
-   **Dependencies:** It has no external dependencies and manages its own state. It directly checks the `process.env` global, which is assumed to be populated by the build environment, to initialize the `@google/genai` client.