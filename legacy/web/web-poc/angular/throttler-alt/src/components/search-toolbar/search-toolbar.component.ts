
import { Component, ChangeDetectionStrategy, output, signal } from '@angular/core';
import { CommonModule } from '@angular/common';

@Component({
  selector: 'app-search-toolbar',
  templateUrl: './search-toolbar.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule],
})
export class SearchToolbarComponent {
  search = output<string>();

  query = signal('');

  onQueryChange(event: Event): void {
    const value = (event.target as HTMLInputElement).value;
    this.query.set(value);
  }

  submitSearch(): void {
    if (this.query().trim()) {
      this.search.emit(this.query().trim());
    }
  }
}
