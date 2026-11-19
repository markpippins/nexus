import { Component, ChangeDetectionStrategy, ViewChild, ElementRef, AfterViewInit, OnDestroy, inject, effect } from '@angular/core';
import { CommonModule } from '@angular/common';
import { UiPreferencesService } from '../../services/ui-preferences.service.js';

declare var Terminal: any;
declare var FitAddon: any;

@Component({
  selector: 'app-terminal',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './terminal.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class TerminalComponent implements AfterViewInit, OnDestroy {
  @ViewChild('terminal') terminalEl!: ElementRef<HTMLDivElement>;

  private term: any;
  private fitAddon: any;
  private resizeObserver!: ResizeObserver;
  private hostEl = inject(ElementRef);
  private uiPreferencesService = inject(UiPreferencesService);

  constructor() {
    effect(() => {
      // Rerun this logic whenever the theme signal changes.
      this.uiPreferencesService.theme();

      // The app.component effect which applies the class to the body will have already run.
      // So we can now apply the new theme to the terminal if it exists.
      if (this.term) {
        this.applyTheme();
      }
    });
  }

  ngAfterViewInit(): void {
    this.term = new Terminal({
      cursorBlink: true,
      convertEol: true,
      fontFamily: `ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace`,
      fontSize: 13,
    });
    
    this.applyTheme(); // Apply initial theme

    this.fitAddon = new FitAddon.FitAddon();
    this.term.loadAddon(this.fitAddon);
    
    this.term.open(this.terminalEl.nativeElement);
    
    this.fitAddon.fit();
    
    this.term.writeln('\x1B[1;3;34mWelcome to the Throttler Console!\x1B[0m');
    this.term.writeln('----------------------------------');
    this.term.write('$ ');

    // Mock terminal interaction
    this.term.onData((data: string) => {
        if (data === '\r') { // Enter key
            this.term.write('\r\n$ ');
        } else if (data === '\x7F') { // Backspace
            // Do not delete the prompt
            if (this.term.buffer.active.cursorX > 2) {
                this.term.write('\b \b');
            }
        } else {
            this.term.write(data);
        }
    });

    this.resizeObserver = new ResizeObserver(() => {
      try {
        setTimeout(() => this.fitAddon?.fit(), 0);
      } catch(e) {
        console.warn("FitAddon resize failed. This can happen during rapid resizing.", e);
      }
    });

    this.resizeObserver.observe(this.hostEl.nativeElement);
  }

  private applyTheme(): void {
    const computedStyle = getComputedStyle(document.body);
    const newTheme = {
      background: `rgb(${computedStyle.getPropertyValue('--color-surface-muted').trim()})`,
      foreground: `rgb(${computedStyle.getPropertyValue('--color-text-muted').trim()})`,
      cursor: `rgb(${computedStyle.getPropertyValue('--color-accent-text').trim()})`,
      selectionBackground: `rgba(${computedStyle.getPropertyValue('--color-accent-bg').trim()}, 0.5)`,
      selectionForeground: `rgb(${computedStyle.getPropertyValue('--color-text-base').trim()})`,
    };
    if (this.term) {
      this.term.options.theme = newTheme;
    }
  }

  ngOnDestroy(): void {
    if (this.resizeObserver) {
      this.resizeObserver.disconnect();
    }
    if (this.term) {
      this.term.dispose();
    }
  }
}
