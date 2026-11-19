export interface ServerProfile {
  id: string;
  name: string;
  brokerUrl: string;
  imageUrl: string;
  searchUrl?: string;
  autoConnect?: boolean;
}