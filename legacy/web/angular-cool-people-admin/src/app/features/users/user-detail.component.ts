import { Component, inject, signal, OnInit, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { UserService } from '../../core/services/user.service';
import { UserDTO } from '../../core/models/user.model';

@Component({
    selector: 'app-user-detail',
    standalone: true,
    imports: [CommonModule, FormsModule, RouterLink],
    template: `
    <div class="page-container">
      <div class="page-header">
        <div class="header-left">
          <a routerLink="/users" class="back-link">
            <span class="material-icons">arrow_back</span>
            Back to Users
          </a>
          <h1 class="page-title">{{ isNew() ? 'Create User' : 'Edit User' }}</h1>
        </div>
      </div>

      <div class="card form-container">
        @if (isLoading()) {
          <div class="loading-state">
            <span class="spinner"></span>
            Loading user...
          </div>
        } @else {
          <form (ngSubmit)="onSubmit()" class="user-form">
            <div class="form-grid">
              <div class="form-group">
                <label>Alias</label>
                <input type="text" [(ngModel)]="user.alias" name="alias" class="input-field" required>
              </div>

              <div class="form-group">
                <label>Identifier</label>
                <input type="text" [(ngModel)]="user.identifier" name="identifier" class="input-field" required>
              </div>

              <div class="form-group">
                <label>Email</label>
                <input type="email" [(ngModel)]="user.email" name="email" class="input-field">
              </div>

              <div class="form-group">
                <label>Avatar URL</label>
                <input type="text" [(ngModel)]="user.avatarUrl" name="avatarUrl" class="input-field">
              </div>
            </div>

            <div class="form-actions">
              <button type="button" routerLink="/users" class="btn btn-secondary">Cancel</button>
              <button type="submit" class="btn btn-primary" [disabled]="isSaving()">
                @if (isSaving()) {
                  <span class="spinner-sm"></span>
                  Saving...
                } @else {
                  Save User
                }
              </button>
            </div>
          </form>
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
      align-items: center;
    }

    .header-left {
      display: flex;
      flex-direction: column;
      gap: 0.5rem;
    }

    .back-link {
      display: flex;
      align-items: center;
      gap: 0.25rem;
      color: var(--text-secondary);
      text-decoration: none;
      font-size: 0.875rem;
      font-weight: 500;
    }

    .back-link:hover {
      color: var(--primary-color);
    }

    .back-link .material-icons {
      font-size: 1.25rem;
    }

    .page-title {
      font-size: 1.5rem;
      font-weight: 700;
      color: var(--text-primary);
      margin: 0;
    }

    .form-container {
      padding: 2rem;
      max-width: 800px;
    }

    .user-form {
      display: flex;
      flex-direction: column;
      gap: 2rem;
    }

    .form-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
      gap: 1.5rem;
    }

    .form-group {
      display: flex;
      flex-direction: column;
      gap: 0.5rem;
    }

    .form-group label {
      font-size: 0.875rem;
      font-weight: 500;
      color: var(--text-secondary);
    }

    .form-actions {
      display: flex;
      justify-content: flex-end;
      gap: 1rem;
      padding-top: 1rem;
      border-top: 1px solid var(--border-color);
    }

    .btn-secondary {
      background-color: white;
      border: 1px solid var(--border-color);
      color: var(--text-primary);
    }

    .btn-secondary:hover {
      background-color: #f8fafc;
    }

    .spinner-sm {
      width: 1rem;
      height: 1rem;
      border: 2px solid rgba(255, 255, 255, 0.3);
      border-top-color: white;
      border-radius: 50%;
      animation: spin 1s linear infinite;
      display: inline-block;
      margin-right: 0.5rem;
    }

    @keyframes spin {
      to { transform: rotate(360deg); }
    }
  `]
})
export class UserDetailComponent implements OnInit {
    private route = inject(ActivatedRoute);
    private router = inject(Router);
    private userService = inject(UserService);

    user: Partial<UserDTO> = {};
    isLoading = signal(false);
    isSaving = signal(false);

    isNew = computed(() => !this.user.id);

    ngOnInit() {
        const id = this.route.snapshot.paramMap.get('id');
        if (id && id !== 'new') {
            this.loadUser(id);
        }
    }

    async loadUser(id: string) {
        this.isLoading.set(true);
        try {
            this.user = await this.userService.findById(id);
        } catch (error) {
            console.error('Error loading user', error);
            this.router.navigate(['/users']);
        } finally {
            this.isLoading.set(false);
        }
    }

    async onSubmit() {
        this.isSaving.set(true);
        try {
            if (this.user.id) {
                await this.userService.updateUser(this.user as UserDTO);
            } else {
                await this.userService.createUser(this.user);
            }
            this.router.navigate(['/users']);
        } catch (error) {
            console.error('Error saving user', error);
            alert('Failed to save user');
        } finally {
            this.isSaving.set(false);
        }
    }
}
