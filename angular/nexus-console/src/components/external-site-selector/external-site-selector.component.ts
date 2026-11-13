import { Component, ChangeDetectionStrategy, input, output } from '@angular/core';
import { CommonModule } from '@angular/common';
import { uiOriginFor } from '../../ui-origins.js';

export interface ExternalSite {
  name: string;
  url: string;
  shortName: string;
}

export const EXTERNAL_SITES: ExternalSite[] = [
  { name: 'View Architect',  url: uiOriginFor(3003),                shortName: 'view-architect' },
  { name: 'Google',          url: 'https://www.google.com',               shortName: 'google' },
  { name: 'GitHub',          url: 'https://github.com',                   shortName: 'github' },
  { name: 'ChatGPT',         url: 'https://chatgpt.com',                  shortName: 'chatgpt' },
  { name: 'Copilot',         url: 'https://copilot.microsoft.com',        shortName: 'copilot' },
  { name: 'Claude',           url: 'https://claude.ai',                   shortName: 'claude' },
  { name: 'AI Studio',        url: 'https://aistudio.google.com',         shortName: 'aistudio' },
  { name: 'Gemini',          url: 'https://gemini.google.com',            shortName: 'gemini' },
  { name: 'Google Cloud Console', url: 'https://console.cloud.google.com', shortName: 'google-cloud-console' },
];

@Component({
  selector: 'app-external-site-selector',
  templateUrl: './external-site-selector.component.html',
  imports: [CommonModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ExternalSiteSelectorComponent {
  /** Currently selected site URL, or null if none selected */
  selectedUrl = input<string | null>(null);
  /** Emits the selected site URL (or null to clear) */
  selectedUrlChange = output<string | null>();
  /** Emits the selected site display name (or null to clear) */
  selectedNameChange = output<string | null>();

  readonly sites = EXTERNAL_SITES;

  onSelect(event: Event): void {
    const select = event.target as HTMLSelectElement;
    const value = select.value;
    if (!value) {
      this.selectedUrlChange.emit(null);
      this.selectedNameChange.emit(null);
    } else {
      this.selectedUrlChange.emit(value);
      const site = this.sites.find(s => s.url === value);
      this.selectedNameChange.emit(site?.name ?? null);
    }
  }
}
