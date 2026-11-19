import { Injectable, inject, signal, computed } from '@angular/core';
import { BrokerService } from './broker.service';
import { ServerProfile, DEFAULT_PROFILE, buildBrokerUrl } from '../models/server-profile.model';
import { Router } from '@angular/router';

export interface User {
    id: string;
    alias: string;
    email?: string;
    avatarUrl?: string;
    profileId?: string;
}

interface LoginResponse {
    token: string;
    user?: User;
}

@Injectable({
    providedIn: 'root'
})
export class AuthService {
    private brokerService = inject(BrokerService);
    private router = inject(Router);

    // State
    private _currentUser = signal<User | null>(null);
    private _token = signal<string | null>(null);
    private _currentProfile = signal<ServerProfile>(DEFAULT_PROFILE);
    private _gatewayIp = signal<string>(DEFAULT_PROFILE.gatewayIp);

    // Computed
    public currentUser = this._currentUser.asReadonly();
    public isLoggedIn = computed(() => !!this._token());
    public currentProfile = this._currentProfile.asReadonly();
    public gatewayIp = this._gatewayIp.asReadonly();
    public brokerUrl = computed(() => buildBrokerUrl(this._gatewayIp()));

    constructor() {
        // Restore from local storage
        if (typeof localStorage !== 'undefined') {
            const token = localStorage.getItem('admin-token');
            const user = localStorage.getItem('admin-user');
            const savedGatewayIp = localStorage.getItem('admin-gateway-ip');
            if (token) this._token.set(token);
            if (user) this._currentUser.set(JSON.parse(user));
            if (savedGatewayIp) {
                this._gatewayIp.set(savedGatewayIp);
                this._currentProfile.set({ ...DEFAULT_PROFILE, gatewayIp: savedGatewayIp });
            }
        }
    }

    setGatewayIp(ip: string): void {
        this._gatewayIp.set(ip);
        this._currentProfile.set({ ...this._currentProfile(), gatewayIp: ip });
        if (typeof localStorage !== 'undefined') {
            localStorage.setItem('admin-gateway-ip', ip);
        }
    }

    async login(alias: string, identifier: string): Promise<void> {
        try {
            const response = await this.brokerService.submitRequest<LoginResponse>(
                this.brokerUrl(),
                'loginService',
                'login',
                { alias, identifier }
            );

            if (response.token) {
                this.setSession(response.token, response.user || { id: alias, alias });
                this.router.navigate(['/dashboard']);
            }
        } catch (error) {
            console.error('Login failed', error);
            throw error;
        }
    }

    logout(): void {
        this._token.set(null);
        this._currentUser.set(null);
        localStorage.removeItem('admin-token');
        localStorage.removeItem('admin-user');
        this.router.navigate(['/login']);
    }

    getToken(): string | null {
        return this._token();
    }

    private setSession(token: string, user: User): void {
        this._token.set(token);
        this._currentUser.set(user);
        localStorage.setItem('admin-token', token);
        localStorage.setItem('admin-user', JSON.stringify(user));
    }
}
