import { UserDTO } from './user.model';

export interface ForumDTO {
    id: string;
    name: string;
    description?: string;
    members?: Set<UserDTO>;
}

export interface PostDTO {
    id: string;
    text: string; // or content
    postedBy?: string; // alias
    forumId?: string;
    rating?: number;
    replies?: any[]; // CommentDTO
    reactions?: any[]; // ReactionDTO
}
