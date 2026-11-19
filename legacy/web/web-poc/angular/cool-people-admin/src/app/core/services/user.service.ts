import { Injectable, inject } from '@angular/core';
import { BrokerService } from './broker.service';
import { AuthService } from './auth.service';
import { UserDTO, Page } from '../models/user.model';

@Injectable({
    providedIn: 'root'
})
export class UserService {
    private brokerService = inject(BrokerService);
    private authService = inject(AuthService);

    async findAll(page: number = 0, size: number = 10): Promise<Page<UserDTO>> {
        const brokerUrl = this.authService.brokerUrl();
        // Note: Backend currently lacks pagination for findAll, so we might get a list and have to mock pagination client-side
        // or use the proposed findAllPaginated if/when available.
        // For now, we'll assume the standard findAll returns a list and we'll wrap it.

        try {
            const response = await this.brokerService.submitRequest<UserDTO[]>(
                brokerUrl,
                'userService',
                'findAll',
                {}
            );

            // Mock pagination for now since backend returns full list
            return {
                content: response,
                totalPages: 1,
                totalElements: response.length,
                size: response.length,
                number: 0
            };
        } catch (error) {
            console.error('Failed to fetch users', error);
            throw error;
        }
    }

    async findById(userId: string): Promise<UserDTO> {
        const brokerUrl = this.authService.brokerUrl();
        return this.brokerService.submitRequest<UserDTO>(
            brokerUrl,
            'userService',
            'findById',
            { userId }
        );
    }

    async createUser(user: Partial<UserDTO>): Promise<UserDTO> {
        const brokerUrl = this.authService.brokerUrl();
        return this.brokerService.submitRequest<UserDTO>(
            brokerUrl,
            'userService',
            'createUser',
            { ...user }
        );
    }

    async updateUser(user: UserDTO): Promise<UserDTO> {
        const brokerUrl = this.authService.brokerUrl();
        return this.brokerService.submitRequest<UserDTO>(
            brokerUrl,
            'userService',
            'update',
            { user }
        );
    }

    async deleteUser(userId: string): Promise<void> {
        const brokerUrl = this.authService.brokerUrl();
        await this.brokerService.submitRequest(
            brokerUrl,
            'userService',
            'delete',
            { userId }
        );
    }
}
