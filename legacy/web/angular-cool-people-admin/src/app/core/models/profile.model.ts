import { UserDTO } from './user.model';

export interface ProfileDTO {
    id: string;
    firstName?: string;
    lastName?: string;
    city?: string;
    state?: string;
    profileImageUrl?: string;
    interests?: Set<string>;
    skills?: Set<string>;
    languages?: Set<string>;
    // Add other fields as needed
    user?: UserDTO; // Optional, might not be in DTO but useful for UI if joined
}
