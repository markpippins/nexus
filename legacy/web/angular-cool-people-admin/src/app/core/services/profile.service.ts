import { Injectable, inject } from '@angular/core';
import { BrokerService } from './broker.service';
import { AuthService } from './auth.service';
import { ProfileDTO } from '../models/profile.model';
import { Page } from '../models/user.model';

@Injectable({
    providedIn: 'root'
})
export class ProfileService {
    private brokerService = inject(BrokerService);
    private authService = inject(AuthService);

    async findAll(page: number = 0, size: number = 10): Promise<Page<ProfileDTO>> {
        const profile = this.authService.currentProfile();
        // Note: Backend currently lacks findAll for profiles.
        // We will try to use the proposed findAllPaginated if available, or handle error.
        // For now, we might have to mock or return empty if not implemented.

        try {
            // Attempt to call the proposed operation
            const response = await this.brokerService.submitRequest<ProfileDTO[]>(
                profile.brokerUrl,
                'profileService',
                'findAll', // This might fail if not implemented
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
            console.warn('Profile findAll not implemented or failed', error);
            return { content: [], totalPages: 0, totalElements: 0, size, number: page };
        }
    }

    async findByUserId(userId: string): Promise<ProfileDTO> {
        const profile = this.authService.currentProfile();
        return this.brokerService.submitRequest<ProfileDTO>(
            profile.brokerUrl,
            'profileService',
            'findByUserId',
            { userId }
        );
    }

    async save(user: any, firstName: string, lastName: string): Promise<ProfileDTO> {
        const profile = this.authService.currentProfile();
        return this.brokerService.submitRequest<ProfileDTO>(
            profile.brokerUrl,
            'profileService',
            'save',
            { user, firstName, lastName }
        );
    }

    async createProfile(profileData: ProfileDTO): Promise<ProfileDTO> {
        const profile = this.authService.currentProfile();
        return this.brokerService.submitRequest<ProfileDTO>(
            profile.brokerUrl,
            'profileService',
            'createProfile',
            { profileData }
        );
    }

    async deleteByUserId(userId: string): Promise<void> {
        const profile = this.authService.currentProfile();
        await this.brokerService.submitRequest(
            profile.brokerUrl,
            'profileService',
            'deleteByUserId',
            { userId }
        );
    }
}
