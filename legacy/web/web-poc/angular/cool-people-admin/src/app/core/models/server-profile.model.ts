export interface ServerProfile {
    id: string;
    name: string;
    gatewayIp: string;
    description?: string;
}

export const DEFAULT_PROFILE: ServerProfile = {
    id: 'local',
    name: 'Local Development',
    gatewayIp: 'localhost',
    description: 'Local development environment'
};

export function buildBrokerUrl(gatewayIp: string): string {
    return `http://${gatewayIp}:8080/api/broker/submitRequest`;
}
