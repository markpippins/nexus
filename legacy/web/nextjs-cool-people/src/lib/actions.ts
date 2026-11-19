'use server';

import { suggestPostTags } from '@/ai/flows/suggest-post-tags';
import { revalidatePath } from 'next/cache';
import { cookies } from 'next/headers';
// import { users } from './data'; // No longer used
import type { User } from './types';
import { redirect } from 'next/navigation';
import * as api from './api';

const IS_USER_CREATE_MODE = process.env.NEXT_PUBLIC_USER_CREATE_MODE === 'true';
const IS_USER_LOGIN_MODE = process.env.NEXT_PUBLIC_USER_LOGIN_MODE === 'true';

// Helper to get token
async function getToken(): Promise<string> {
  const cookieStore = await cookies();
  return cookieStore.get('coolpeople-token')?.value || '';
}

// Real function to save a post via Broker
async function savePost(post: { content: string; tags: string[] }) {
  const token = await getToken();
  if (!token) throw new Error("Not authenticated");

  // Construct the post object expected by the backend
  // Backend PostDTO: { text, ... }
  const postDto = {
    text: post.content,
    // tags: post.tags // Backend might not support tags yet
  };

  const response = await api.savePost(token, postDto);
  if (!response.ok) {
    throw new Error(response.error || "Failed to save post");
  }
  return { success: true };
}

export async function createPostAction(prevState: any, formData: FormData) {
  const content = formData.get('content') as string;
  const tags = formData.getAll('tags') as string[];

  if (!content || content.trim().length === 0) {
    return { message: 'Content cannot be empty.', type: 'error' };
  }

  try {
    await savePost({ content, tags });
    revalidatePath('/');
    return { message: 'Post created successfully!', type: 'success' };
  } catch (error) {
    console.error("Create post error", error);
    return { message: 'Failed to create post.', type: 'error' };
  }
}

export async function suggestTagsAction(postContent: string) {
  if (!postContent) {
    return { suggestedTags: [], error: null };
  }
  try {
    const result = await suggestPostTags({ postContent });
    return { suggestedTags: result.suggestedTags, error: null };
  } catch (error) {
    console.error('Error suggesting tags:', error);
    return { suggestedTags: [], error: 'Could not suggest tags.' };
  }
}

export async function loginAction(prevState: any, formData: FormData) {
  if (!IS_USER_LOGIN_MODE) {
    return { message: 'Login is only available when USER_LOGIN_MODE is enabled.', type: 'error' };
  }

  const email = formData.get('email') as string;
  const password = formData.get('password') as string;

  if (!email || !password) {
    return { message: 'Email and password are required.', type: 'error' };
  }

  // Use the alias as the email for login purposes
  const alias = email; // Use email as alias for login
  const identifier = password; // Use password as identifier

  // Generate a unique request ID
  const requestId = typeof crypto !== 'undefined' && crypto.randomUUID
    ? crypto.randomUUID()
    : `req-${Date.now()}`;

  const requestBody = {
    service: "loginService",
    operation: "login",
    params: { alias, identifier },
    requestId
  };

  try {
    // Server-side fetch to Broker
    const backendUrl =
      process.env.BROKER_SERVICE_URL ||
      'http://localhost:8080/api/broker/submitRequest';

    const response = await fetch(backendUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(requestBody),
    });

    const data = await response.json();

    if (!response.ok || !data.ok || !data.token) {
      const errorDetails = data.errors?.[0]?.message || data.message || "Login failed";
      return { message: errorDetails, type: 'error' };
    }

    // Store the token in a cookie for client-side access
    const cookieStore = await cookies();
    await cookieStore.set('coolpeople-token', data.token, {
      httpOnly: true,
      secure: process.env.NODE_ENV === 'production',
      maxAge: 60 * 60 * 24, // 1 day
      path: '/',
    });

    // Store user info in cookie as well
    // Backend might not return user object in login response (based on Angular experience)
    // If not, we construct a minimal one or fetch it.
    // Angular service constructed a partial user.
    const userInfo = {
      id: data.user?.id || alias,
      alias: alias,
      email: email,
      name: alias,
      username: alias,
      avatar: 'avatar1'
    };

    await cookieStore.set('coolpeople-user', JSON.stringify(userInfo), {
      httpOnly: false, // This can be accessible by client-side JavaScript if needed
      secure: process.env.NODE_ENV === 'production',
      maxAge: 60 * 60 * 24, // 1 day
      path: '/',
    });

    return { message: 'Logged in successfully!', type: 'success' };
  } catch (error) {
    console.error('Login error:', error);
    return { message: 'Login failed due to network error.', type: 'error' };
  }
}

export async function signupAction(prevState: any, formData: FormData) {
  if (!IS_USER_CREATE_MODE) {
    return {
      message: 'Signup is only available when USER_CREATE_MODE is enabled.',
      type: 'error',
    };
  }

  const name = formData.get('name') as string;
  const email = formData.get('email') as string;
  const password = formData.get('password') as string;
  // const username = name.toLowerCase().replace(/\s/g, '');

  if (!name || !email || !password) {
    return { message: 'All fields are required.', type: 'error' };
  }

  // Generate a unique request ID
  const requestId = typeof crypto !== 'undefined' && crypto.randomUUID
    ? crypto.randomUUID()
    : `req-${Date.now()}`;

  // Call the user service to create a new user via the broker
  const requestBody = {
    service: "userService",
    operation: "createUser",
    params: { email, alias: name, identifier: password },
    requestId
  };

  try {
    const backendUrl =
      process.env.BROKER_SERVICE_URL ||
      'http://localhost:8080/api/broker/submitRequest';

    const response = await fetch(backendUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(requestBody),
    });

    const data = await response.json();

    if (!response.ok || !data.ok) {
      const errorDetails = data.errors?.[0]?.message || data.message || "User creation failed";
      return { message: errorDetails, type: 'error' };
    }

    // Automatically log the user in by storing the token
    if (data.token) {
      const cookieStore = await cookies();
      await cookieStore.set('coolpeople-token', data.token, {
        httpOnly: true,
        secure: process.env.NODE_ENV === 'production',
        maxAge: 60 * 60 * 24, // 1 day
        path: '/',
      });

      const userInfo = {
        id: data.user?.id || name,
        alias: name,
        email: email,
        name: name,
        username: name,
        avatar: 'avatar1'
      };
      await cookieStore.set('coolpeople-user', JSON.stringify(userInfo), {
        httpOnly: false,
        secure: process.env.NODE_ENV === 'production',
        maxAge: 60 * 60 * 24, // 1 day
        path: '/',
      });
    }

    redirect('/feed');
  } catch (error) {
    console.error('Signup error:', error);
    return { message: 'Signup failed due to network error.', type: 'error' };
  }
}

export async function logoutAction() {
  const cookieStore = await cookies();
  await cookieStore.delete('coolpeople-token');
  await cookieStore.delete('coolpeople-user');

  // Optional: Call logout on backend
}

export async function getSessionAction(): Promise<{ user: any; token: string | null }> {
  const cookieStore = await cookies();
  const tokenCookie = await cookieStore.get('coolpeople-token');
  const userCookie = await cookieStore.get('coolpeople-user');

  if (!tokenCookie) {
    return { user: null, token: null };
  }

  try {
    const user = userCookie ? JSON.parse(userCookie.value) : null;
    return { user, token: tokenCookie.value };
  } catch (error) {
    console.error('Error parsing session cookie:', error);
    return { user: null, token: null };
  }
}
