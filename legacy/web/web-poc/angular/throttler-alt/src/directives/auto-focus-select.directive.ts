import { Directive, ElementRef, AfterViewInit, inject } from '@angular/core';

@Directive({
  selector: '[autoFocusSelect]',
  standalone: true,
})
export class AutoFocusSelectDirective implements AfterViewInit {
  private el = inject(ElementRef<HTMLInputElement>);

  ngAfterViewInit() {
    // Use a timeout to ensure the element is fully rendered and focusable
    // in the DOM before we try to interact with it.
    setTimeout(() => {
      this.el.nativeElement.focus();
      this.el.nativeElement.select();
    }, 0);
  }
}
