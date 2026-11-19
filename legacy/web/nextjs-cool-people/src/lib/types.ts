export type User = {
  id: string;
  name: string;
  username: string;
  avatar: string; 
  bio: string;
  alias?: string; // For token-based auth
  email?: string; // For token-based auth
  profileId?: string; // For token-based auth
  admin?: boolean;
};

export type Post = {
  id: string;
  user: User;
  content: string;
  tags: string[];
  createdAt: string; // ISO string
  likes: number;
  comments: number;
};

export type Forum = {
  id: string;
  slug: string;
  name: string;
  description: string;
  threadCount: number;
  postCount: number;
  image: string; // Corresponds to an ID in placeholder-images.json
};

export type ForumThread = {
  id: string;
  forumSlug: string;
  title: string;
  author: User;
  createdAt: string; // ISO string
  replyCount: number;
  viewCount: number;
  lastReply: {
    user: User;
    createdAt: string;
  } | null;
};

export interface ApiResponse {
  ok: boolean;
  error?: string;
  [key: string]: any; // Allow additional properties as needed by different API calls
}
