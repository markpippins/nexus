import { Component, ChangeDetectionStrategy, input, output, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { GoogleSearchResult } from '../../models/google-search-result.model.js';
import { WebviewService } from '../../services/webview.service.js';

@Component({
  selector: 'app-web-result-card',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './web-result-card.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class WebResultCardComponent {
  result = input.required<GoogleSearchResult>();
  isBookmarked = input(false);
  bookmarkToggled = output<GoogleSearchResult>();

  private webviewService = inject(WebviewService);

  onToggleBookmark(): void {
    this.bookmarkToggled.emit(this.result());
  }

  openLink(event: MouseEvent): void {
    event.preventDefault();
    this.webviewService.open(this.result().link, this.result().title);
  }
}
