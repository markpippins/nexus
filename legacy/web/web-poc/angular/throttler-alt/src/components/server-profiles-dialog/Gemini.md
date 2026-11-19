# `ServerProfilesDialogComponent` Documentation (`/src/components/server-profiles-dialog/Gemini.md`)

This component is a modal dialog that provides a full user interface for managing remote server connection profiles and handling user authentication for those connections.

## Core Responsibilities

1.  **Profile CRUD Operations:** It allows users to Create, Read, Update, and Delete server profiles via the `ServerProfileService`.
2.  **User Authentication:** For profiles that are not currently mounted, it provides a "Connect..." button. Clicking this button opens a separate, focused `LoginDialogComponent` where the user enters their credentials. This keeps the profile management UI clean and separates concerns.
3.  **Connection Management:** It provides the UI to mount and unmount server profiles.
    -   When a user submits the login form in the separate dialog, it emits a `loginAndMount` event with the profile and credentials.
    -   When a user clicks "Unmount" on an active connection, it emits an `unmountProfile` event.
4.  **Displaying Connection State:** It clearly indicates which profiles are mounted and, for those that are, displays the name of the logged-in user.
5.  **UI State Management:** It manages its own internal state, such as which profile is selected for editing and the content of the forms.

## API and Data Flow

### Inputs (`input()`)

-   `mountedProfileIds: string[]`: An array of IDs for profiles that are currently mounted.
-   `mountedProfileUsers: Map<string, User>`: A map where keys are profile IDs and values are the `User` objects for authenticated sessions on those profiles.

### Outputs (`output()`)

-   `close: void`: Emitted when the user closes the dialog.
-   `loginAndMount: { profile: ServerProfile, username: string, password: string }`: Emitted when the user submits the login form for a profile.
-   `unmountProfile: ServerProfile`: Emitted when the "Unmount" button is clicked.

### Interactions

-   **`ServerProfileService`:** The component's primary dependency for managing the list of profiles. It calls methods like `updateProfile()`, `addProfile()`, and `deleteProfile()`.

## Internal State (Signals)

-   `selectedProfileId: string | null`: Tracks which profile is selected in the list.
-   `formState: FormState | null`: Holds the data for the profile being edited or created.
-   `isLoginDialogOpen: boolean`: Controls the visibility of the separate login dialog.
-   `profileToLogin: ServerProfile | null`: Holds the profile object that the user is attempting to log into.