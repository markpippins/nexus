import { Component, ChangeDetectionStrategy, input, output, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ImageSearchResult } from '../../models/image-search-result.model.js';
import { WebviewService } from '../../services/webview.service.js';

@Component({
  selector: 'app-image-result-card',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './image-result-card.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ImageResultCardComponent {
  result = input.required<ImageSearchResult>();
  isBookmarked = input(false);
  bookmarkToggled = output<ImageSearchResult>();

  private webviewService = inject(WebviewService);

  onToggleBookmark(): void {
    this.bookmarkToggled.emit(this.result());
  }

  openLink(event: MouseEvent): void {
    event.preventDefault();
    this.webviewService.open(this.result().url, this.result().description);
  }
}
