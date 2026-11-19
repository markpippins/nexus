import { Component, ChangeDetectionStrategy, input, output, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ServerProfile } from '../../models/server-profile.model.js';

@Component({
  selector: 'app-login-dialog',
  imports: [CommonModule],
  templateUrl: './login-dialog.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class LoginDialogComponent {
  profile = input.required<ServerProfile>();
  close = output<void>();
  submitLogin = output<{ username: string, password: string }>();

  username = signal('');
  password = signal('');

  onUsernameChange(event: Event): void {
    this.username.set((event.target as HTMLInputElement).value);
  }

  onPasswordChange(event: Event): void {
    this.password.set((event.target as HTMLInputElement).value);
  }

  onSubmit(): void {
    if (this.username().trim() && this.password().trim()) {
      this.submitLogin.emit({ username: this.username(), password: this.password() });
    }
  }

  onCancel(): void {
    this.close.emit();
  }
}
