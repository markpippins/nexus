import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';

export interface ServiceRequest<T> {
    service: string;
    operation: string;
    params: T;
    requestId?: string;
}

export interface ServiceResponse<T> {
    data: T;
    message?: string;
    ok: boolean;
    errors?: { message: string }[];
    requestId?: string;
    ts?: string;
}

@Injectable({
    providedIn: 'root'
})
export class BrokerService {
    private http = inject(HttpClient);

    async submitRequest<T>(
        brokerUrl: string,
        service: string,
        operation: string,
        params: any = {}
    ): Promise<T> {
        const requestId = crypto.randomUUID();
        const request: ServiceRequest<any> = {
            service,
            operation,
            params,
            requestId
        };

        try {
            const response = await firstValueFrom(
                this.http.post<ServiceResponse<T>>(brokerUrl, request)
            );

            if (!response.ok) {
                const errorMessage = response.errors?.map(e => e.message).join(', ') || response.message || 'Unknown error';
                throw new Error(errorMessage);
            }

            return response.data;
        } catch (error) {
            console.error(`Broker request failed: ${service}.${operation}`, error);
            throw error;
        }
    }
}
