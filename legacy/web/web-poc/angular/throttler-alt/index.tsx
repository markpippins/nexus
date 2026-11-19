import '@angular/compiler';
import { bootstrapApplication } from '@angular/platform-browser';
import { provideZonelessChangeDetection } from '@angular/core';
import { provideHttpClient, withFetch } from '@angular/common/http';

import { AppComponent } from './src/app.component.js';
import { IS_DEBUG_MODE } from './src/services/app-config.js';

// We assume the build process exposes DEBUG from .env as process.env.DEBUG
declare const process: any;
const isDebugMode = true;

bootstrapApplication(AppComponent, {
  providers: [
    provideZonelessChangeDetection(),
    provideHttpClient(withFetch()),
    { provide: IS_DEBUG_MODE, useValue: isDebugMode },
  ],
}).catch((err) => console.error(err));

// AI Studio always uses an `index.tsx` file for all project types.