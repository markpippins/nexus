import { Component, ChangeDetectionStrategy, input, output, computed } from '@angular/core';
import { CommonModule } from '@angular/common';

interface GeminiResult {
  query: string;
  text: string;
}

@Component({
  selector: 'app-gemini-result-card',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './gemini-result-card.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class GeminiResultCardComponent {
  result = input.required<GeminiResult>();
  isBookmarked = input(false);
  bookmarkToggled = output<GeminiResult>();

  truncatedText = computed(() => {
    const text = this.result().text;
    if (text.length > 140) {
        return text.slice(0, 140) + '...';
    }
    return text;
  });

  onToggleBookmark(): void {
    this.bookmarkToggled.emit(this.result());
  }
}
