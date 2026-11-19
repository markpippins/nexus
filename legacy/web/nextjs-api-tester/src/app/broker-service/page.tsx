import { BrokerApiTester } from "@/components/broker-api-tester";

export default function BrokerServicePage() {
  return (
    <main className="container mx-auto p-4 sm:p-8">
      <div className="flex flex-col items-center justify-center space-y-2 mb-12">
        <h1 className="text-4xl font-bold tracking-tight">
          Broker Service API Test
        </h1>
        <p className="text-muted-foreground text-center">
          A simple interface to test the broker service.
        </p>
      </div>
      <BrokerApiTester />
    </main>
  );
}
