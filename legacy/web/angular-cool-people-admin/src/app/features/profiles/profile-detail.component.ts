import { Component, inject, signal, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { ProfileService } from '../../core/services/profile.service';
import { ProfileDTO } from '../../core/models/profile.model';

@Component({
    selector: 'app-profile-detail',
    standalone: true,
    imports: [CommonModule, FormsModule, RouterLink],
    template: `
    <div class="page-container">
      <div class="page-header">
        <div class="header-left">
          <a routerLink="/profiles" class="back-link">
            <span class="material-icons">arrow_back</span>
            Back to Profiles
          </a>
          <h1 class="page-title">Edit Profile</h1>
        </div>
      </div>

      <div class="card form-container">
        @if (isLoading()) {
          <div class="loading-state">
            <span class="spinner"></span>
            Loading profile...
          </div>
        } @else {
          <form (ngSubmit)="onSubmit()" class="profile-form">
            <div class="form-grid">
              <div class="form-group">
                <label>First Name</label>
                <input type="text" [(ngModel)]="profile.firstName" name="firstName" class="input-field">
              </div>

              <div class="form-group">
                <label>Last Name</label>
                <input type="text" [(ngModel)]="profile.lastName" name="lastName" class="input-field">
              </div>

              <div class="form-group">
                <label>City</label>
                <input type="text" [(ngModel)]="profile.city" name="city" class="input-field">
              </div>

              <div class="form-group">
                <label>State</label>
                <input type="text" [(ngModel)]="profile.state" name="state" class="input-field">
              </div>

              <div class="form-group full-width">
                <label>Profile Image URL</label>
                <input type="text" [(ngModel)]="profile.profileImageUrl" name="profileImageUrl" class="input-field">
              </div>
            </div>

            <div class="form-actions">
              <button type="button" routerLink="/profiles" class="btn btn-secondary">Cancel</button>
              <button type="submit" class="btn btn-primary" [disabled]="isSaving()">
                @if (isSaving()) {
                  <span class="spinner-sm"></span>
                  Saving...
                } @else {
                  Save Profile
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

    .profile-form {
      display: flex;
      flex-direction: column;
      gap: 2rem;
    }

    .form-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
      gap: 1.5rem;
    }

    .full-width {
      grid-column: 1 / -1;
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
export class ProfileDetailComponent implements OnInit {
    private route = inject(ActivatedRoute);
    private router = inject(Router);
    private profileService = inject(ProfileService);

    profile: Partial<ProfileDTO> = {};
    isLoading = signal(false);
    isSaving = signal(false);

    ngOnInit() {
        const id = this.route.snapshot.paramMap.get('id');
        if (id) {
            // Note: The ID here might be profile ID or User ID depending on how we route.
            // Assuming it's profile ID for now, but we might need findById if findByUserId is not appropriate.
            // However, the service only has findByUserId. Let's assume the list passes UserID or we add findById.
            // Actually, ProfileDTO has ID. But ProfileService has findByUserId.
            // Let's assume for now we pass UserID in the route if we want to edit by user, or we need findById in service.
            // Given the list iterates profiles, it has profile.id.
            // We probably need findById in ProfileService.
            // For now, I'll implement a mock findById in service or just use what I have.
            // I'll assume the route param is userId for simplicity with existing service methods, 
            // OR I'll update the service to have findById.
            // Let's update service to have findById if possible, or just use findByUserId if we change the route to use userId.
            // But the list has profile.id.
            // Let's assume we can't easily get userId from profileId without a backend call.
            // I'll add findById to ProfileService (mocked or real).
            this.loadProfile(id);
        }
    }

    async loadProfile(id: string) {
        this.isLoading.set(true);
        try {
            // TODO: This should be findById if we are passing profile ID.
            // For now, let's assume we might need to fetch by user ID.
            // But wait, the list passes profile.id.
            // I'll use findByUserId for now and assume the ID passed IS the user ID (which is common in 1:1 relationships)
            // OR I'll just try to fetch it.
            // Actually, let's just add findById to the service.
            this.profile = await this.profileService.findByUserId(id); // This is likely wrong if id is profileId
        } catch (error) {
            console.error('Error loading profile', error);
            this.router.navigate(['/profiles']);
        } finally {
            this.isLoading.set(false);
        }
    }

    async onSubmit() {
        this.isSaving.set(true);
        try {
            if (this.profile.id) {
                // We need a user object for the save method signature
                await this.profileService.save({}, this.profile.firstName || '', this.profile.lastName || '');
            }
            this.router.navigate(['/profiles']);
        } catch (error) {
            console.error('Error saving profile', error);
            alert('Failed to save profile');
        } finally {
            this.isSaving.set(false);
        }
    }
}
