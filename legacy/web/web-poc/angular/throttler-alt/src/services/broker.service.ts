import { Injectable, inject } from '@angular/core';
import { ServerProfileService } from './server-profile.service.js';
import { LocalConfigService } from './local-config.service.js';

@Injectable({
  providedIn: 'root',
})
export class BrokerService {
  private localConfigService = inject(LocalConfigService);
  
  private generateUUID(): string {
    var d = new Date().getTime();
    var d2 = ((typeof performance !== 'undefined') && performance.now && (performance.now()*1000)) || 0;
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
        var r = Math.random() * 16;
        if(d > 0){
            r = (d + r)%16 | 0;
            d = Math.floor(d/16);
        } else {
            r = (d2 + r)%16 | 0;
            d2 = Math.floor(d2/16);
        }
        return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
    });
  }

  async submitRequest<T>(brokerUrl: string, service: string, operation: string, params: object = {}): Promise<T> {
    const request = {
        service,
        operation,
        params,
        requestId: this.generateUUID()
    };

    const shouldLog = this.localConfigService.currentConfig().logBrokerMessages;

    if (shouldLog) {
      console.groupCollapsed(`[Broker Request] ${service}/${operation} (ID: ${request.requestId})`);
      console.log('Request Payload:', request);
      console.time(`[Broker Response] ${request.requestId}`);
    }

    try {
      const response = await fetch(brokerUrl, {
          method: 'POST',
          mode: 'cors',
          credentials: 'include',
          headers: {
              'Content-Type': 'application/json',
          },
          body: JSON.stringify(request),
      });

      if (!response.ok) {
          const errorBody = await response.text();
          throw new Error(`Broker request failed with status ${response.status}: ${errorBody}`);
      }

      const serviceResponse = await response.json();

      if (shouldLog) {
          console.timeEnd(`[Broker Response] ${request.requestId}`);
          console.log('Response Payload:', serviceResponse);
      }

      if (serviceResponse.ok) {
          if (shouldLog) {
            console.groupEnd();
          }
          return serviceResponse.data as T;
      } else {
          const errorDetails = serviceResponse.errors.map((e: any) => e.message || `${e.code}: ${e.path}`).join(', ');
          throw new Error(`Service operation ${service}/${operation} failed: ${errorDetails}`);
      }
    } catch (error) {
      if (shouldLog) {
        console.timeEnd(`[Broker Response] ${request.requestId}`);
        console.error('Request Failed:', error);
        console.groupEnd();
      }
      throw error;
    }
  }
}
