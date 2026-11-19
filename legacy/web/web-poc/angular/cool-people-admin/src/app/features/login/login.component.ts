import { Component, inject, signal, OnInit } from '@angular/core';
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

          <div class="server-settings">
            <button type="button" class="settings-toggle" (click)="showSettings.set(!showSettings())">
              <span>Server Settings</span>
              <span class="toggle-icon">{{ showSettings() ? '▼' : '▶' }}</span>
            </button>

            @if (showSettings()) {
              <div class="settings-panel">
                <div class="form-group">
                  <label>Gateway IP Address</label>
                  <input type="text" [(ngModel)]="gatewayIp" name="gatewayIp"
                         class="input-field" placeholder="localhost">
                  <span class="help-text">Default: localhost</span>
                </div>
              </div>
            }
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

    .server-settings {
      border-top: 1px solid #e2e8f0;
      padding-top: 1rem;
    }

    .settings-toggle {
      width: 100%;
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 0.5rem 0;
      background: none;
      border: none;
      color: #64748b;
      font-size: 0.875rem;
      cursor: pointer;
      transition: color 0.2s;
    }

    .settings-toggle:hover {
      color: #334155;
    }

    .toggle-icon {
      font-size: 0.75rem;
    }

    .settings-panel {
      padding: 0.75rem 0 0.5rem;
    }

    .help-text {
      font-size: 0.75rem;
      color: #94a3b8;
      margin-top: 0.25rem;
    }
  `]
})
export class LoginComponent implements OnInit {
    private authService = inject(AuthService);

    username = '';
    password = '';
    gatewayIp = 'localhost';
    isLoading = signal(false);
    error = signal<string | null>(null);
    showSettings = signal(false);

    ngOnInit(): void {
        // Load saved gateway IP from auth service
        this.gatewayIp = this.authService.gatewayIp();
    }

    async onSubmit() {
        if (!this.username || !this.password) return;

        // Save the gateway IP
        this.authService.setGatewayIp(this.gatewayIp || 'localhost');

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
