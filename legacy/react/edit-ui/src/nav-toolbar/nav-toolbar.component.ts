import { Component, ChangeDetectionStrategy, input, output } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Theme } from '../services/ui-preferences.service';

@Component({
  selector: 'app-nav-toolbar',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="nav-toolbar flex flex-col h-full bg-[rgb(var(--color-surface-sidebar))] border-r border-[rgb(var(--color-border-base))]" [class.w-16]="collapsed()" [style.width.px]="!collapsed() ? toolbarWidth() : undefined">
      <!-- Nexus branding orb -->
      <div class="flex items-center justify-center py-3 border-b border-[rgb(var(--color-border-base))]">
        <div class="w-10 h-10 rounded-full bg-gradient-to-br from-blue-500 to-purple-600 flex items-center justify-center shadow-lg ring-2 ring-blue-400/30">
          <svg xmlns="http://www.w3.org/2000/svg" class="h-6 w-6 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
            <path stroke-linecap="round" stroke-linejoin="round" d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09zM18.259 8.715L18 9.75l-.259-1.035a3.375 3.375 0 00-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 002.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 002.455 2.456L21.75 6l-1.036.259a3.375 3.375 0 00-2.455 2.456zM16.894 20.567L16.5 21.75l-.394-1.183a2.25 2.25 0 00-1.423-1.423L13.5 18.75l1.183-.394a2.25 2.25 0 001.423-1.423l.394-1.183.394 1.183a2.25 2.25 0 001.423 1.423l1.183.394-1.183.394a2.25 2.25 0 00-1.423 1.423z" />
          </svg>
        </div>
      </div>

      <!-- Spacer -->
      <div class="flex-1"></div>

      <!-- Theme toggle (3-state: steel → light → dark) -->
      <button
        class="flex items-center justify-center w-full py-3 text-[rgb(var(--color-text-muted))] hover:bg-[rgb(var(--color-surface-hover))] transition-colors"
        [title]="'Theme: ' + (theme() === 'theme-steel' ? 'Steel' : theme() === 'theme-light' ? 'Light' : 'Dark')"
        (click)="toggleTheme()"
      >
        @switch (theme()) {
          @case ('theme-light') {
            <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
              <path stroke-linecap="round" stroke-linejoin="round" d="M12 3v2.25m6.364.386l-1.591 1.591M21 12h-2.25m-.386 6.364l-1.591-1.591M12 18.75V21m-4.773-4.227l-1.591 1.591M5.25 12H3m4.227-4.773L5.636 5.636M15.75 12a3.75 3.75 0 11-7.5 0 3.75 3.75 0 017.5 0z" />
            </svg>
          }
          @case ('theme-dark') {
            <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
              <path stroke-linecap="round" stroke-linejoin="round" d="M21.752 15.002A9.718 9.718 0 0118 15.75c-5.385 0-9.75-4.365-9.75-9.75 0-1.33.266-2.597.748-3.752A9.753 9.753 0 003 11.25C3 16.635 7.365 21 12.75 21a9.753 9.753 0 009.002-5.998z" />
            </svg>
          }
          @default {
            <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
              <path stroke-linecap="round" stroke-linejoin="round" d="M9 6.75V15m6-6v8.25m.503 3.498l4.875-2.437c.381-.19.622-.58.622-1.006V4.82c0-.836-.88-1.38-1.628-1.006l-3.869 1.934c-.317.159-.69.159-1.006 0L9.503 3.252a1.125 1.125 0 00-1.006 0L3.622 5.689C3.24 5.88 3 6.27 3 6.695V19.18c0 .836.88 1.38 1.628 1.006l3.869-1.934c.317-.159.69-.159 1.006 0l4.994 2.497c.317.158.69.158 1.006 0z" />
            </svg>
          }
        }
      </button>

      <!-- Collapse toggle -->
      <button
        class="flex items-center justify-center w-full py-3 text-[rgb(var(--color-text-muted))] hover:bg-[rgb(var(--color-surface-hover))] transition-colors border-t border-[rgb(var(--color-border-base))]"
        [title]="collapsed() ? 'Expand panel' : 'Collapse panel'"
        (click)="onToggleCollapse()"
      >
        @if (collapsed()) {
          <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
            <path stroke-linecap="round" stroke-linejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
          </svg>
        } @else {
          <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
            <path stroke-linecap="round" stroke-linejoin="round" d="M15.75 19.5L8.25 12l7.5-7.5" />
          </svg>
        }
      </button>
    </div>
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class NavToolbarComponent {
  theme = input.required<Theme>();
  collapsed = input(false);
  toolbarWidth = input(280);

  themeChange = output<Theme>();
  collapseToggled = output<void>();

  readonly THEME_CYCLE: Theme[] = ['theme-steel', 'theme-light', 'theme-dark'];

  toggleTheme(): void {
    const current = this.theme();
    const idx = this.THEME_CYCLE.indexOf(current);
    const next = this.THEME_CYCLE[(idx + 1) % this.THEME_CYCLE.length];
    this.themeChange.emit(next);
  }

  onToggleCollapse(): void {
    this.collapseToggled.emit();
  }
}
