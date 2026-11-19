import { Component, inject, signal, OnInit } from '@angular/core';
import { ProfileService } from '../../core/services/profile.service';
import { ProfileDTO } from '../../core/models/profile.model';
import { RouterLink } from '@angular/router';

@Component({
    selector: 'app-profile-list',
    standalone: true,
    imports: [RouterLink],
    template: `
    <div class="page-container">
      <div class="page-header">
        <h1 class="page-title">Profiles</h1>
        <!-- Profiles are usually created via User registration, but we might allow manual creation -->
      </div>

      <div class="card table-container">
        @if (isLoading()) {
          <div class="loading-state">
            <span class="spinner"></span>
            Loading profiles...
          </div>
        } @else {
          <table class="data-table">
            <thead>
              <tr>
                <th>Profile</th>
                <th>Location</th>
                <th>Skills</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              @for (profile of profiles(); track profile.id) {
                <tr>
                  <td>
                    <div class="user-cell">
                      @if (profile.profileImageUrl) {
                        <img [src]="profile.profileImageUrl" class="avatar-sm" alt="Profile">
                      } @else {
                        <div class="avatar-sm">
                          {{ (profile.firstName?.charAt(0) || '') + (profile.lastName?.charAt(0) || '') }}
                        </div>
                      }
                      <div class="user-info">
                        <div class="username">{{ profile.firstName }} {{ profile.lastName }}</div>
                      </div>
                    </div>
                  </td>
                  <td>
                    @if (profile.city || profile.state) {
                      {{ profile.city }}{{ profile.city && profile.state ? ', ' : '' }}{{ profile.state }}
                    } @else {
                      <span class="text-muted">N/A</span>
                    }
                  </td>
                  <td>
                    <div class="tags">
                      @for (skill of getTopSkills(profile); track skill) {
                        <span class="tag">{{ skill }}</span>
                      }
                      @if ((profile.skills?.size || 0) > 3) {
                        <span class="tag-more">+{{ (profile.skills?.size || 0) - 3 }}</span>
                      }
                    </div>
                  </td>
                  <td>
                    <div class="actions">
                      <a [routerLink]="['/profiles', profile.id]" class="action-btn edit" title="Edit">
                        <span class="material-icons">edit</span>
                      </a>
                    </div>
                  </td>
                </tr>
              } @empty {
                <tr>
                  <td colspan="4" class="empty-state">
                    No profiles found. 
                    <br>
                    <small>Note: Backend might not support listing profiles yet.</small>
                  </td>
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
      object-fit: cover;
    }

    .username {
      font-weight: 500;
    }

    .text-muted {
      color: var(--text-secondary);
      font-style: italic;
    }

    .tags {
      display: flex;
      gap: 0.25rem;
      flex-wrap: wrap;
    }

    .tag {
      background-color: #e2e8f0;
      color: #475569;
      padding: 0.125rem 0.5rem;
      border-radius: 9999px;
      font-size: 0.75rem;
    }

    .tag-more {
      color: var(--text-secondary);
      font-size: 0.75rem;
      align-self: center;
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
export class ProfileListComponent implements OnInit {
    private profileService = inject(ProfileService);

    profiles = signal<ProfileDTO[]>([]);
    isLoading = signal(true);

    ngOnInit() {
        this.loadProfiles();
    }

    async loadProfiles() {
        try {
            this.isLoading.set(true);
            const page = await this.profileService.findAll();
            this.profiles.set(page.content);
        } catch (error) {
            console.error('Error loading profiles', error);
        } finally {
            this.isLoading.set(false);
        }
    }

    getTopSkills(profile: ProfileDTO): string[] {
        if (!profile.skills) return [];
        return Array.from(profile.skills).slice(0, 3);
    }
}
