
import React from 'react';
import { encryptText, decryptText } from './services/cryptoService';
import { OperationType } from './types';
import EncryptionCard from './components/EncryptionCard';

const App: React.FC = () => {

  // Simulate a network delay for server operations
  const simulateServerDelay = <T,>(data: T): Promise<T> => {
    return new Promise(resolve => setTimeout(() => resolve(data), 750));
  };

  const handleProcess = async (
    type: OperationType, 
    text: string, 
    key: string
  ): Promise<string> => {
    switch (type) {
      case OperationType.CLIENT_ENCRYPT:
        return encryptText(text, key);
      case OperationType.CLIENT_DECRYPT:
        return decryptText(text, key);
      case OperationType.SERVER_ENCRYPT: {
        // Simulate sending plaintext to server for encryption
        const encrypted = await simulateServerDelay(encryptText(text, key));
        return encrypted;
      }
      case OperationType.SERVER_DECRYPT: {
        // Simulate sending ciphertext to server for decryption
        const decrypted = await simulateServerDelay(decryptText(text, key));
        return decrypted;
      }
      default:
        throw new Error("Unknown operation type");
    }
  };

  return (
    <div className="min-h-screen bg-slate-900 font-sans p-4 sm:p-6 lg:p-8">
      <div className="max-w-7xl mx-auto">
        <header className="text-center mb-10">
          <h1 className="text-4xl sm:text-5xl font-bold text-cyan-400 tracking-tight">
            CryptoGuard
          </h1>
          <p className="mt-3 text-lg text-slate-400 max-w-3xl mx-auto">
            A demonstration of client-side and server-side encryption/decryption flows using AES.
          </p>
        </header>

        <main className="grid grid-cols-1 md:grid-cols-2 gap-8">
          <EncryptionCard
            title="Client-Side Encryption"
            description="Text is encrypted directly in your browser before being sent anywhere. The server never sees the original plaintext."
            inputLabel="Plaintext to Encrypt"
            buttonText="Encrypt on Client"
            onProcess={(text, key) => handleProcess(OperationType.CLIENT_ENCRYPT, text, key)}
          />
          <EncryptionCard
            title="Server-Side Decryption"
            description="The client sends encrypted text. The server uses the shared secret to decrypt it and access the original plaintext."
            inputLabel="Ciphertext to Decrypt"
            buttonText="Decrypt on Server (Simulated)"
            isCiphertext
            onProcess={(text, key) => handleProcess(OperationType.SERVER_DECRYPT, text, key)}
          />
          <EncryptionCard
            title="Server-Side Encryption"
            description="The client sends plaintext. The server encrypts it and returns the ciphertext to the client for storage."
            inputLabel="Plaintext to Encrypt"
            buttonText="Encrypt on Server (Simulated)"
            onProcess={(text, key) => handleProcess(OperationType.SERVER_ENCRYPT, text, key)}
          />
          <EncryptionCard
            title="Client-Side Decryption"
            description="The client receives encrypted text from the server and uses the shared secret to decrypt it in the browser."
            inputLabel="Ciphertext to Decrypt"
            buttonText="Decrypt on Client"
            isCiphertext
            onProcess={(text, key) => handleProcess(OperationType.CLIENT_DECRYPT, text, key)}
          />
        </main>
        <footer className="text-center mt-12 text-slate-500">
          <p>Built with React, TypeScript, Tailwind CSS, and CryptoJS.</p>
          <p>Note: "Server" operations are simulated locally with an artificial delay.</p>
        </footer>
      </div>
    </div>
  );
};

export default App;
