import { Component } from '@angular/core';
import { RouterOutlet } from '@angular/router';
import { SidebarComponent } from './sidebar.component';

@Component({
    selector: 'app-shell',
    standalone: true,
    imports: [RouterOutlet, SidebarComponent],
    template: `
    <div class="app-container">
      <app-sidebar />
      <main class="main-content">
        <router-outlet />
      </main>
    </div>
  `,
    styles: [`
    .app-container {
      display: flex;
      min-height: 100vh;
      background-color: var(--bg-color);
    }

    .main-content {
      flex: 1;
      margin-left: 260px; /* Match sidebar width */
      padding: 2rem;
    }
  `]
})
export class ShellComponent { }
