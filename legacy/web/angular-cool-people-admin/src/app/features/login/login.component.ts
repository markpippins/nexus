import { Component, inject, signal } from '@angular/core';
import { AuthService } from '../../core/services/auth.service';
import { FormsModule } from '@angular/forms';

@Component({
    selector: 'app-login',
    standalone: true,
    imports: [FormsModule],
    template: `
    <div class="login-container">
      <div class="login-card">
        <div class="login-header">
          <h1 class="title">Atomic Admin</h1>
          <p class="subtitle">Sign in to manage the platform</p>
        </div>

        <form (ngSubmit)="onSubmit()" class="login-form">
          <div class="form-group">
            <label>Username</label>
            <input type="text" [(ngModel)]="username" name="username" 
                   class="input-field" placeholder="Enter your username" required>
          </div>

          <div class="form-group">
            <label>Password</label>
            <input type="password" [(ngModel)]="password" name="password" 
                   class="input-field" placeholder="Enter your password" required>
          </div>

          @if (error()) {
            <div class="error-message">
              {{ error() }}
            </div>
          }

          <button type="submit" [disabled]="isLoading()" class="btn btn-primary submit-btn">
            @if (isLoading()) {
              <span class="spinner"></span>
              Signing in...
            } @else {
              Sign In
            }
          </button>
        </form>
      </div>
    </div>
  `,
    styles: [`
    .login-container {
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      background-color: #f1f5f9;
    }

    .login-card {
      background: white;
      padding: 2.5rem;
      border-radius: 1rem;
      box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);
      width: 100%;
      max-width: 400px;
    }

    .login-header {
      text-align: center;
      margin-bottom: 2rem;
    }

    .title {
      font-size: 1.5rem;
      font-weight: 700;
      color: #0f172a;
      margin: 0 0 0.5rem;
    }

    .subtitle {
      color: #64748b;
      margin: 0;
    }

    .login-form {
      display: flex;
      flex-direction: column;
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
      color: #334155;
    }

    .error-message {
      padding: 0.75rem;
      background-color: #fef2f2;
      color: #dc2626;
      font-size: 0.875rem;
      border-radius: 0.5rem;
      border: 1px solid #fee2e2;
    }

    .submit-btn {
      width: 100%;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 0.5rem;
    }

    .submit-btn:disabled {
      opacity: 0.7;
      cursor: not-allowed;
    }

    .spinner {
      width: 1rem;
      height: 1rem;
      border: 2px solid rgba(255, 255, 255, 0.3);
      border-top-color: white;
      border-radius: 50%;
      animation: spin 1s linear infinite;
    }

    @keyframes spin {
      to { transform: rotate(360deg); }
    }
  `]
})
export class LoginComponent {
    private authService = inject(AuthService);

    username = '';
    password = '';
    isLoading = signal(false);
    error = signal<string | null>(null);

    async onSubmit() {
        if (!this.username || !this.password) return;

        this.isLoading.set(true);
        this.error.set(null);

        try {
            await this.authService.login(this.username, this.password);
        } catch (err: any) {
            this.error.set(err.message || 'Login failed. Please check your credentials.');
        } finally {
            this.isLoading.set(false);
        }
    }
}
