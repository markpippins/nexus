import { Component, ChangeDetectionStrategy, input, output, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { AcademicSearchResult } from '../../models/academic-search-result.model.js';
import { WebviewService } from '../../services/webview.service.js';

@Component({
  selector: 'app-academic-result-list-item',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './academic-result-list-item.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AcademicResultListItemComponent {
  result = input.required<AcademicSearchResult>();
  isBookmarked = input(false);
  bookmarkToggled = output<AcademicSearchResult>();

  private webviewService = inject(WebviewService);

  onToggleBookmark(): void {
    this.bookmarkToggled.emit(this.result());
  }

  openLink(event: MouseEvent): void {
    event.preventDefault();
    this.webviewService.open(this.result().link, this.result().title);
  }
}
