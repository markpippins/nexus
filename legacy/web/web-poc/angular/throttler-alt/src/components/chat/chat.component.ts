import { Component, ChangeDetectionStrategy, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { GoogleGenAI, Chat } from '@google/genai';

// We assume the build process exposes API_KEY from .env as process.env.API_KEY
declare const process: any;

interface ChatMessage {
  role: 'user' | 'model';
  text: string;
}

@Component({
  selector: 'app-chat',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './chat.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ChatComponent {
  isLoading = signal(false);
  
  messages = signal<ChatMessage[]>([]);
  
  newMessage = signal('');

  private chat: Chat | null = null;

  constructor() {
    let apiKey: string | undefined;
    try {
      // This is the only place we access `process`. If it fails, we catch it.
      if (typeof process !== 'undefined' && process.env && process.env.API_KEY) {
        apiKey = process.env.API_KEY;
      }
    } catch (e) {
      console.warn('Could not access process.env.API_KEY. Chat will run in demo mode.');
    }

    if (apiKey) {
      try {
        const ai = new GoogleGenAI({ apiKey: apiKey });
        this.chat = ai.chats.create({
          model: 'gemini-2.5-flash',
        });
        this.messages.set([{ role: 'model', text: 'Hello! How can I help you today?' }]);
      } catch (e) {
        console.error("Failed to initialize Gemini Chat", e);
        this.chat = null; // Ensure chat is null on failure
        this.messages.set([
          { role: 'model', text: 'Error: Could not initialize the AI Chat service. The API key might be invalid. Running in demo mode.' }
        ]);
      }
    } else {
      this.chat = null;
      this.messages.set([
        { role: 'model', text: 'Hello! This is a demo chat. I will echo your messages. To enable the real AI chat, please configure a Gemini API key.' }
      ]);
    }
  }

  async sendMessage(): Promise<void> {
    const messageText = this.newMessage().trim();
    if (!messageText || this.isLoading()) {
      return;
    }

    this.isLoading.set(true);
    
    // Add user message to chat
    this.messages.update(msgs => [...msgs, { role: 'user', text: messageText }]);
    this.newMessage.set('');

    // If there is no real chat session, run in demo/echo mode.
    if (!this.chat) {
      setTimeout(() => {
        const echoMessage = `You said: "${messageText}"`;
        this.messages.update(msgs => [...msgs, { role: 'model', text: echoMessage }]);
        this.isLoading.set(false);
      }, 500);
      return;
    }
    
    // --- Real chat logic ---
    this.messages.update(msgs => [...msgs, { role: 'model', text: '' }]);

    try {
      const result = await this.chat.sendMessageStream({ message: messageText });
      
      for await (const chunk of result) {
        const chunkText = chunk.text;
        this.messages.update(msgs => {
          const lastMsgIndex = msgs.length - 1;
          const updatedMsgs = [...msgs];
          if (updatedMsgs[lastMsgIndex].role === 'model') {
            updatedMsgs[lastMsgIndex].text += chunkText;
          }
          return updatedMsgs;
        });
      }

    } catch (e) {
      console.error("Error sending message to Gemini", e);
      const errorMessage = `Sorry, I encountered an error. Please try again. Details: ${(e as Error).message}`;
      this.messages.update(msgs => {
        const lastMsgIndex = msgs.length - 1;
        const updatedMsgs = [...msgs];
        if (updatedMsgs[lastMsgIndex].role === 'model' && updatedMsgs[lastMsgIndex].text === '') {
          updatedMsgs[lastMsgIndex].text = errorMessage;
        } else {
            updatedMsgs.push({role: 'model', text: errorMessage});
        }
        return updatedMsgs;
      });
    } finally {
      this.isLoading.set(false);
    }
  }

  onInput(event: Event): void {
    this.newMessage.set((event.target as HTMLTextAreaElement).value);
  }
}
