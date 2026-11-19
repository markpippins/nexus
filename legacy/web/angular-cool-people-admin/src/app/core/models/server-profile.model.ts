export interface ServerProfile {
    id: string;
    name: string;
    brokerUrl: string;
    description?: string;
}

export const DEFAULT_PROFILE: ServerProfile = {
    id: 'local',
    name: 'Local Development',
    brokerUrl: 'http://172.16.30.45:8080/api/broker/submitRequest',
    description: 'Local development environment'
};
