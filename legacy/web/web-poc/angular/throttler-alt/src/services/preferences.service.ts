import { Injectable, signal } from '@angular/core';

@Injectable({
  providedIn: 'root',
})
export class PreferencesService {
  iconTheme = signal<string>('neon');
}
