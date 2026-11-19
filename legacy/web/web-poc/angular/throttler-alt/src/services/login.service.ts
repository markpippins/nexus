import { Injectable, inject } from '@angular/core';
import { BrokerService } from './broker.service.js';
import { User } from '../models/user.model.js';
import { ServerProfile } from '../models/server-profile.model.js';

const SERVICE_NAME = 'loginService';

interface LoginResponse {
  token: string;
  message?: string;
  ok: boolean;
  errors?: { message: string }[];
}

@Injectable({
  providedIn: 'root'
})
export class LoginService {
  private brokerService = inject(BrokerService);

  private constructBrokerUrl(baseUrl: string): string {
    let fullUrl = baseUrl.trim();
    if (!fullUrl.startsWith('http://') && !fullUrl.startsWith('https://')) {
        fullUrl = `http://${fullUrl}`;
    }
    if (fullUrl.endsWith('/')) {
        fullUrl = fullUrl.slice(0, -1);
    }
    fullUrl += '/api/broker/submitRequest';
    return fullUrl;
  }

  async login(profile: ServerProfile, username: string, password: string): Promise<User> {
    const response = await this.brokerService.submitRequest<LoginResponse>(this.constructBrokerUrl(profile.brokerUrl), SERVICE_NAME, 'login', {
        alias: username,
        identifier: password
    });
    
    if (!response || !response.ok) {
        const errorMessage = response?.errors?.map(e => e.message).join(', ') || response?.message || 'Login failed due to an unknown error.';
        throw new Error(errorMessage);
    }
    
    // Since the user object is no longer returned, we construct a partial user object
    // for display purposes. The username is the key piece of information we have.
    return {
      id: username,
      profileId: profile.id,
      alias: username,
      email: `${username}@mock.com`, // No email info from this response
      avatarUrl: '' // No avatar info from this response
    };
  }
}
