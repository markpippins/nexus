import { Component, ChangeDetectionStrategy, input, output, inject, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { DomSanitizer, SafeResourceUrl } from '@angular/platform-browser';

@Component({
  selector: 'app-webview-dialog',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './webview-dialog.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class WebviewDialogComponent {
  url = input.required<string>();
  title = input.required<string>();
  close = output<void>();

  // FIX: Explicitly type the injected service to help the TypeScript compiler.
  private sanitizer: DomSanitizer = inject(DomSanitizer);

  safeUrl = computed<SafeResourceUrl>(() => {
    return this.sanitizer.bypassSecurityTrustResourceUrl(this.url());
  });
}
