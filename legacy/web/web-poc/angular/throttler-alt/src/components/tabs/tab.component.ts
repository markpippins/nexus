import { Component, ChangeDetectionStrategy, input, signal } from '@angular/core';

@Component({
  selector: 'app-tab',
  standalone: true,
  template: `
    @if (active()) {
      <div class="h-full">
        <ng-content></ng-content>
      </div>
    }
  `,
  host: {
    class: 'block h-full'
  },
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class TabComponent {
  title = input.required<string>();
  active = signal(false);
}
