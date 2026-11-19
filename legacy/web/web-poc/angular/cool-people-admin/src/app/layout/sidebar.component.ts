import { Component, inject } from '@angular/core';
import { RouterLink, RouterLinkActive } from '@angular/router';
import { AuthService } from '../core/services/auth.service';

@Component({
    selector: 'app-sidebar',
    standalone: true,
    imports: [RouterLink, RouterLinkActive],
    template: `
    <aside class="sidebar">
      <div class="sidebar-header">
        <h1 class="logo">ATOMIC<span class="highlight">ADMIN</span></h1>
      </div>
      
      <nav class="sidebar-nav">
        <a routerLink="/dashboard" routerLinkActive="active" [routerLinkActiveOptions]="{exact: true}" class="nav-item">
          <span class="material-icons">dashboard</span>
          Dashboard
        </a>

        <div class="nav-section">Management</div>

        <a routerLink="/users" routerLinkActive="active" class="nav-item">
          <span class="material-icons">people</span>
          Users
        </a>
        
        <a routerLink="/profiles" routerLinkActive="active" class="nav-item">
          <span class="material-icons">badge</span>
          Profiles
        </a>

        <div class="nav-section">Content</div>

        <a routerLink="/forums" routerLinkActive="active" class="nav-item">
          <span class="material-icons">forum</span>
          Forums
        </a>

        <a routerLink="/posts" routerLinkActive="active" class="nav-item">
          <span class="material-icons">article</span>
          Posts
        </a>
      </nav>

      <div class="sidebar-footer">
        <div class="user-profile">
          <div class="avatar">
            {{ authService.currentUser()?.alias?.charAt(0)?.toUpperCase() }}
          </div>
          <div class="user-info">
            <div class="username">{{ authService.currentUser()?.alias }}</div>
            <div class="role">Admin</div>
          </div>
          <button (click)="authService.logout()" class="logout-btn">
            <span class="material-icons">logout</span>
          </button>
        </div>
      </div>
    </aside>
  `,
    styles: [`
    .sidebar {
      width: 260px;
      background-color: var(--sidebar-bg);
      color: var(--sidebar-text);
      height: 100vh;
      display: flex;
      flex-direction: column;
      position: fixed;
      left: 0;
      top: 0;
      border-right: 1px solid #1e293b;
    }

    .sidebar-header {
      padding: 1.5rem;
      border-bottom: 1px solid #1e293b;
    }

    .logo {
      font-size: 1.25rem;
      font-weight: 700;
      letter-spacing: 0.05em;
      color: white;
      margin: 0;
    }

    .highlight {
      color: var(--primary-color);
    }

    .sidebar-nav {
      flex: 1;
      padding: 1rem;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
      gap: 0.5rem;
    }

    .nav-section {
      padding: 1rem 1rem 0.5rem;
      font-size: 0.75rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: #64748b;
    }

    .nav-item {
      display: flex;
      align-items: center;
      gap: 0.75rem;
      padding: 0.75rem 1rem;
      color: var(--sidebar-text);
      text-decoration: none;
      border-radius: 0.5rem;
      transition: all 0.2s;
    }

    .nav-item:hover {
      background-color: var(--sidebar-hover);
      color: white;
    }

    .nav-item.active {
      background-color: var(--primary-color);
      color: white;
    }

    .nav-item .material-icons {
      font-size: 1.25rem;
    }

    .sidebar-footer {
      padding: 1rem;
      border-top: 1px solid #1e293b;
    }

    .user-profile {
      display: flex;
      align-items: center;
      gap: 0.75rem;
      padding: 0.75rem;
      background-color: rgba(255, 255, 255, 0.05);
      border-radius: 0.5rem;
    }

    .avatar {
      width: 2rem;
      height: 2rem;
      border-radius: 50%;
      background-color: var(--primary-color);
      color: white;
      display: flex;
      align-items: center;
      justify-content: center;
      font-weight: 700;
      font-size: 0.875rem;
    }

    .user-info {
      flex: 1;
      min-width: 0;
    }

    .username {
      font-size: 0.875rem;
      font-weight: 500;
      color: white;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .role {
      font-size: 0.75rem;
      color: #64748b;
    }

    .logout-btn {
      background: none;
      border: none;
      color: #64748b;
      cursor: pointer;
      padding: 0.25rem;
      border-radius: 0.25rem;
      transition: color 0.2s;
    }

    .logout-btn:hover {
      color: white;
    }
  `]
})
export class SidebarComponent {
    authService = inject(AuthService);
}
