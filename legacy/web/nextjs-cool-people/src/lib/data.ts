import { cookies } from 'next/headers';
import * as api from './api';
import type { User, Post, Forum, ForumThread } from './types';

// Helper to get token from cookies
async function getToken(): Promise<string> {
  const cookieStore = await cookies();
  const token = cookieStore.get('coolpeople-token')?.value;
  // If no token, we might return a guest token or throw. 
  // For now, return empty string which might cause backend to fail auth, 
  // or we can handle it gracefully.
  return token || '';
}

// --- Users ---
export const getUserByUsername = async (username: string): Promise<User | undefined> => {
  const token = await getToken();
  if (!token) return undefined;

  try {
    // Note: Backend might not have findByAlias, checking requirements...
    // If not, we might need to search or use findById if we have the ID.
    // Assuming findByAlias exists or we map it.
    const response = await api.getUserByUsername(token, username);
    // Map backend DTO to frontend User type
    // Backend UserDTO: { id, alias, email, ... }
    // Frontend User: { id, name, username, avatar, bio, ... }
    if (response && !response.error) {
      const u = response as any;
      return {
        id: u.id,
        name: u.alias, // Map alias to name
        username: u.alias,
        avatar: u.avatarUrl || 'avatar1', // Default or map
        bio: u.bio || '',
        alias: u.alias,
        email: u.email
      };
    }
  } catch (e) {
    console.error('Failed to fetch user', e);
  }
  return undefined;
};

// --- Posts ---
export const getPosts = async (): Promise<Post[]> => {
  const token = await getToken();
  if (!token) return [];

  try {
    const response = await api.getPosts(token);
    if (Array.isArray(response)) {
      return response.map((p: any) => ({
        id: p.id,
        user: {
          id: p.userId || 'unknown', // We might need to fetch user details or if backend returns populated user
          name: p.postedBy || 'Unknown',
          username: p.postedBy || 'unknown',
          avatar: 'avatar1',
          bio: '',
          alias: p.postedBy,
          email: ''
        },
        content: p.text,
        tags: [], // Backend might not have tags yet
        createdAt: p.createdDate || new Date().toISOString(),
        likes: p.rating || 0,
        comments: p.replies?.length || 0
      }));
    }
  } catch (e) {
    console.error('Failed to fetch posts', e);
  }
  return [];
};

export const getPostsByUserId = async (userId: string): Promise<Post[]> => {
  const token = await getToken();
  if (!token) return [];

  try {
    const response = await api.getPostsByUser(token, userId);
    if (Array.isArray(response)) {
      return response.map((p: any) => ({
        id: p.id,
        user: {
          id: p.userId || userId,
          name: p.postedBy || 'Unknown',
          username: p.postedBy || 'unknown',
          avatar: 'avatar1',
          bio: '',
          alias: p.postedBy,
          email: ''
        },
        content: p.text,
        tags: [],
        createdAt: p.createdDate || new Date().toISOString(),
        likes: p.rating || 0,
        comments: p.replies?.length || 0
      }));
    }
  } catch (e) {
    console.error('Failed to fetch posts by user', e);
  }
  return [];
};

// --- Forums ---
export const getForums = async (): Promise<Forum[]> => {
  const token = await getToken();
  if (!token) return [];

  try {
    const response = await api.getForums(token);
    if (Array.isArray(response)) {
      return response.map((f: any) => ({
        id: f.id,
        slug: f.name.toLowerCase().replace(/\s+/g, '-'), // Generate slug from name
        name: f.name,
        description: f.description || '',
        threadCount: 0, // Backend might not return counts
        postCount: 0,
        image: 'forum_general' // Default
      }));
    }
  } catch (e) {
    console.error('Failed to fetch forums', e);
  }
  return [];
};

export const getForumBySlug = async (slug: string): Promise<Forum | undefined> => {
  const token = await getToken();
  if (!token) return undefined;

  // Since backend finds by name, we might need to map slug to name or just try to find by name (slug-like)
  // Or fetch all and filter. Fetching all is safer if we don't have exact name match.
  try {
    const forums = await getForums();
    return forums.find(f => f.slug === slug);
  } catch (e) {
    console.error('Failed to fetch forum by slug', e);
  }
  return undefined;
};

// --- Threads (Mocked for now as backend Forum model might not have explicit threads yet, or maps to Posts) ---
// Assuming Forum -> Posts structure. If "Threads" are top-level posts in a forum:
export const getThreadsByForumSlug = async (slug: string): Promise<ForumThread[]> => {
  // For now, return empty or mock, as backend might not support "Threads" distinct from Posts
  // If we treat Posts with forumId as threads:
  const token = await getToken();
  if (!token) return [];

  // We need getPostsByForumId or similar. 
  // If not available, we might have to fetch all posts and filter (inefficient)
  // Or just return empty for now until backend supports it.
  return [];
};

// Export empty arrays for compatibility if needed, but prefer functions
export const users: User[] = [];
export const posts: Post[] = [];
export const forums: Forum[] = [];
export const threads: ForumThread[] = [];
