# Project Root Documentation (`/Gemini.md`)

This document provides a high-level overview of the files located in the project's root directory.

## Core Application Files

### `index.tsx` - Application Entry Point

This is the main entry point for the entire Angular application. Its primary responsibilities are:
- **Bootstrapping:** It uses `bootstrapApplication` to launch the root `AppComponent`.
- **Zoneless Change Detection:** It enables Angular's modern, more performant change detection strategy.
- **Dependency Injection:** It configures application-wide providers. It sets up `provideHttpClient(withFetch())` for modern, fetch-based HTTP requests.

### `index.html` - Main HTML Document

This is the single HTML page that hosts the application.
- **Root Element:** Contains `<app-root></app-root>`, where the `AppComponent` is rendered.
- **Styling:** Loads **Tailwind CSS** from a CDN and defines a sophisticated theming system using CSS variables for light, dark, and steel themes. It also includes a keyframe animation (`slide-in-right`) used for the details pane.
- **ESM Configuration:** Uses an `importmap` to manage ES module resolution for Angular and other dependencies directly in the browser.

### `metadata.json`

This file contains metadata specific to the AI Studio development environment.

### `package.json`

This is the standard Node.js manifest file, configured for a web-based Angular application using the Angular CLI. It contains all the necessary scripts (`start`, `build`, `test`) and a list of project dependencies.