import { Component, inject, signal, OnInit } from '@angular/core';
import { UserService } from '../../core/services/user.service';
import { UserDTO } from '../../core/models/user.model';
import { RouterLink } from '@angular/router';
import { DatePipe } from '@angular/common';

@Component({
    selector: 'app-user-list',
    standalone: true,
    imports: [RouterLink, DatePipe],
    template: `
    <div class="page-container">
      <div class="page-header">
        <h1 class="page-title">Users</h1>
        <a routerLink="/users/new" class="btn btn-primary" style="text-decoration: none; display: flex; align-items: center; gap: 0.5rem;">
          <span class="material-icons">add</span>
          Add User
        </a>
      </div>

      <div class="card table-container">
        @if (isLoading()) {
          <div class="loading-state">
            <span class="spinner"></span>
            Loading users...
          </div>
        } @else {
          <table class="data-table">
            <thead>
              <tr>
                <th>User</th>
                <th>Email</th>
                <th>Identifier</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              @for (user of users(); track user.id) {
                <tr>
                  <td>
                    <div class="user-cell">
                      <div class="avatar-sm">
                        {{ user.alias.charAt(0).toUpperCase() }}
                      </div>
                      <span class="username">{{ user.alias }}</span>
                    </div>
                  </td>
                  <td>{{ user.email || 'N/A' }}</td>
                  <td>{{ user.identifier }}</td>
                  <td>
                    <div class="actions">
                      <a [routerLink]="['/users', user.id]" class="action-btn edit" title="Edit">
                        <span class="material-icons">edit</span>
                      </a>
                      <button class="action-btn delete" (click)="deleteUser(user)" title="Delete">
                        <span class="material-icons">delete</span>
                      </button>
                    </div>
                  </td>
                </tr>
              } @empty {
                <tr>
                  <td colspan="4" class="empty-state">No users found.</td>
                </tr>
              }
            </tbody>
          </table>
        }
      </div>
    </div>
  `,
    styles: [`
    .page-container {
      display: flex;
      flex-direction: column;
      gap: 1.5rem;
    }

    .page-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
    }

    .page-title {
      font-size: 1.5rem;
      font-weight: 700;
      color: var(--text-primary);
      margin: 0;
    }

    .table-container {
      overflow-x: auto;
    }

    .data-table {
      width: 100%;
      border-collapse: collapse;
      text-align: left;
    }

    .data-table th {
      padding: 1rem;
      background-color: #f8fafc;
      border-bottom: 1px solid var(--border-color);
      font-weight: 600;
      color: var(--text-secondary);
      font-size: 0.875rem;
    }

    .data-table td {
      padding: 1rem;
      border-bottom: 1px solid var(--border-color);
      color: var(--text-primary);
      font-size: 0.875rem;
    }

    .user-cell {
      display: flex;
      align-items: center;
      gap: 0.75rem;
    }

    .avatar-sm {
      width: 2rem;
      height: 2rem;
      border-radius: 50%;
      background-color: var(--primary-color);
      color: white;
      display: flex;
      align-items: center;
      justify-content: center;
      font-weight: 600;
      font-size: 0.75rem;
    }

    .username {
      font-weight: 500;
    }

    .actions {
      display: flex;
      gap: 0.5rem;
    }

    .action-btn {
      background: none;
      border: none;
      cursor: pointer;
      padding: 0.25rem;
      border-radius: 0.25rem;
      transition: background-color 0.2s;
      color: var(--text-secondary);
    }

    .action-btn:hover {
      background-color: #f1f5f9;
      color: var(--text-primary);
    }

    .action-btn.delete:hover {
      color: var(--danger-color);
      background-color: #fef2f2;
    }

    .action-btn .material-icons {
      font-size: 1.25rem;
    }

    .loading-state {
      padding: 3rem;
      text-align: center;
      color: var(--text-secondary);
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 1rem;
    }

    .spinner {
      width: 2rem;
      height: 2rem;
      border: 3px solid #e2e8f0;
      border-top-color: var(--primary-color);
      border-radius: 50%;
      animation: spin 1s linear infinite;
    }

    @keyframes spin {
      to { transform: rotate(360deg); }
    }

    .empty-state {
      text-align: center;
      padding: 2rem;
      color: var(--text-secondary);
    }
  `]
})
export class UserListComponent implements OnInit {
    private userService = inject(UserService);

    users = signal<UserDTO[]>([]);
    isLoading = signal(true);

    ngOnInit() {
        this.loadUsers();
    }

    async loadUsers() {
        try {
            this.isLoading.set(true);
            const page = await this.userService.findAll();
            this.users.set(page.content);
        } catch (error) {
            console.error('Error loading users', error);
        } finally {
            this.isLoading.set(false);
        }
    }

    async deleteUser(user: UserDTO) {
        if (confirm(`Are you sure you want to delete user ${user.alias}?`)) {
            try {
                await this.userService.deleteUser(user.id);
                await this.loadUsers();
            } catch (error) {
                console.error('Error deleting user', error);
                alert('Failed to delete user');
            }
        }
    }
}
