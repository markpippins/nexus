import { Component, ChangeDetectionStrategy, input, output } from '@angular/core';
import { CommonModule } from '@angular/common';

interface GeminiResult {
  query: string;
  text: string;
}

@Component({
  selector: 'app-gemini-result-list-item',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './gemini-result-list-item.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class GeminiResultListItemComponent {
  result = input.required<GeminiResult>();
  isBookmarked = input(false);
  bookmarkToggled = output<GeminiResult>();

  onToggleBookmark(): void {
    this.bookmarkToggled.emit(this.result());
  }
}
