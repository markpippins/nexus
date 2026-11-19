'use client';

import React, { createContext, useContext, ReactNode } from 'react';
import { useSafeLocalStorage } from '@/hooks/useSafeLocalStorage';

interface BrokerUrlContextType {
  brokerUrl: string;
  setBrokerUrl: (url: string) => void;
}

const BrokerUrlContext = createContext<BrokerUrlContextType | undefined>(undefined);

export function BrokerUrlProvider({ children }: { children: ReactNode }) {
  const defaultUrl = process.env.NEXT_PUBLIC_BROKER_SERVICE_URL ||
           process.env.BROKER_SERVICE_URL ||
           'http://localhost:8080/api/broker/submitRequest';

  const [brokerUrl, setBrokerUrl] = useSafeLocalStorage('brokerUrl', defaultUrl);

  return (
    <BrokerUrlContext.Provider value={{ brokerUrl, setBrokerUrl }}>
      {children}
    </BrokerUrlContext.Provider>
  );
}

export function useBrokerUrl() {
  const context = useContext(BrokerUrlContext);
  if (context === undefined) {
    throw new Error('useBrokerUrl must be used within a BrokerUrlProvider');
  }
  return context;
}