'use client';

import { useState, useEffect } from 'react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useBrokerUrl } from '@/contexts/broker-url-context';

export function BrokerUrlDialog({ children }: { children: React.ReactNode }) {
  const { brokerUrl, setBrokerUrl } = useBrokerUrl();
  const [newBrokerUrl, setNewBrokerUrl] = useState(brokerUrl);
  const [isMounted, setIsMounted] = useState(false);
  const [isOpen, setIsOpen] = useState(false);

  useEffect(() => {
    setIsMounted(true);
  }, []);

  const handleSave = () => {
    setBrokerUrl(newBrokerUrl);
    // The context already handles localStorage updates
    setIsOpen(false);
  };

  const handleReset = () => {
    setNewBrokerUrl(
      process.env.NEXT_PUBLIC_BROKER_SERVICE_URL ||
      process.env.BROKER_SERVICE_URL ||
      'http://localhost:8080/api/broker/submitRequest'
    );
  };

  // Don't render until mounted to avoid hydration issues
  if (!isMounted) {
    return <>{children}</>;
  }

  return (
    <Dialog open={isOpen} onOpenChange={setIsOpen}>
      <DialogTrigger asChild>
        {children}
      </DialogTrigger>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Edit Broker Service URL</DialogTitle>
          <DialogDescription>
            Update the URL for the broker service that handles API requests.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4 py-4">
          <div className="space-y-2">
            <Label htmlFor="broker-url">Broker Service URL</Label>
            <Input
              id="broker-url"
              value={newBrokerUrl}
              onChange={(e) => setNewBrokerUrl(e.target.value)}
              placeholder="Enter broker service URL"
              className="w-full"
            />
          </div>
          <div className="flex gap-2">
            <Button
              type="button"
              variant="outline"
              onClick={handleReset}
              className="flex-1"
            >
              Reset to Default
            </Button>
            <Button
              type="button"
              onClick={handleSave}
              className="flex-1"
            >
              Save
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}