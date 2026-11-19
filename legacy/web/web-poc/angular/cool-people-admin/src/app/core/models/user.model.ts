export interface UserDTO {
    id: string;
    alias: string;
    identifier: string;
    email?: string;
    avatarUrl?: string;
    // Add other fields as needed based on backend DTO
}

export interface Page<T> {
    content: T[];
    totalPages: number;
    totalElements: number;
    size: number;
    number: number;
}
