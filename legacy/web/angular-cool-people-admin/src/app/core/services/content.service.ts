import { Injectable, inject } from '@angular/core';
import { BrokerService } from './broker.service';
import { AuthService } from './auth.service';
import { ForumDTO, PostDTO } from '../models/content.model';
import { Page } from '../models/user.model';

@Injectable({
    providedIn: 'root'
})
export class ContentService {
    private brokerService = inject(BrokerService);
    private authService = inject(AuthService);

    // Forums
    async findAllForums(): Promise<Page<ForumDTO>> {
        const profile = this.authService.currentProfile();
        try {
            const response = await this.brokerService.submitRequest<ForumDTO[]>(
                profile.brokerUrl,
                'forumService',
                'findAll',
                {}
            );

            return {
                content: response,
                totalPages: 1,
                totalElements: response.length,
                size: response.length,
                number: 0
            };
        } catch (error) {
            console.error('Error loading forums', error);
            throw error;
        }
    }

    async saveForum(forum: ForumDTO): Promise<ForumDTO> {
        const profile = this.authService.currentProfile();
        return this.brokerService.submitRequest<ForumDTO>(
            profile.brokerUrl,
            'forumService',
            'saveForum',
            { forum }
        );
    }

    async deleteForum(forumId: string): Promise<void> {
        const profile = this.authService.currentProfile();
        await this.brokerService.submitRequest(
            profile.brokerUrl,
            'forumService',
            'delete',
            { forumId }
        );
    }

    // Posts
    async findAllPosts(): Promise<Page<PostDTO>> {
        const profile = this.authService.currentProfile();
        try {
            const response = await this.brokerService.submitRequest<PostDTO[]>(
                profile.brokerUrl,
                'postService',
                'findAll',
                {}
            );

            return {
                content: response,
                totalPages: 1,
                totalElements: response.length,
                size: response.length,
                number: 0
            };
        } catch (error) {
            console.error('Error loading posts', error);
            throw error;
        }
    }

    async deletePost(postId: string): Promise<void> {
        const profile = this.authService.currentProfile();
        await this.brokerService.submitRequest(
            profile.brokerUrl,
            'postService',
            'delete',
            { postId }
        );
    }
}
