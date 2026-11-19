import { Component, inject, signal, OnInit } from '@angular/core';
import { ContentService } from '../../core/services/content.service';
import { ForumDTO } from '../../core/models/content.model';
import { RouterLink } from '@angular/router';

@Component({
    selector: 'app-forum-list',
    standalone: true,
    imports: [RouterLink],
    template: `
    <div class="page-container">
      <div class="page-header">
        <h1 class="page-title">Forums</h1>
        <button class="btn btn-primary">
          <span class="material-icons">add</span>
          Create Forum
        </button>
      </div>

      <div class="card table-container">
        @if (isLoading()) {
          <div class="loading-state">
            <span class="spinner"></span>
            Loading forums...
          </div>
        } @else {
          <table class="data-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Description</th>
                <th>Members</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              @for (forum of forums(); track forum.id) {
                <tr>
                  <td>
                    <div class="forum-name">{{ forum.name }}</div>
                  </td>
                  <td>{{ forum.description || 'No description' }}</td>
                  <td>{{ forum.members?.size || 0 }} members</td>
                  <td>
                    <div class="actions">
                      <button class="action-btn edit" title="Edit">
                        <span class="material-icons">edit</span>
                      </button>
                      <button class="action-btn delete" (click)="deleteForum(forum)" title="Delete">
                        <span class="material-icons">delete</span>
                      </button>
                    </div>
                  </td>
                </tr>
              } @empty {
                <tr>
                  <td colspan="4" class="empty-state">No forums found.</td>
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

    .forum-name {
      font-weight: 600;
      color: var(--primary-color);
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
export class ForumListComponent implements OnInit {
    private contentService = inject(ContentService);

    forums = signal<ForumDTO[]>([]);
    isLoading = signal(true);

    ngOnInit() {
        this.loadForums();
    }

    async loadForums() {
        try {
            this.isLoading.set(true);
            const page = await this.contentService.findAllForums();
            this.forums.set(page.content);
        } catch (error) {
            console.error('Error loading forums', error);
        } finally {
            this.isLoading.set(false);
        }
    }

    async deleteForum(forum: ForumDTO) {
        if (confirm(`Are you sure you want to delete forum ${forum.name}?`)) {
            try {
                await this.contentService.deleteForum(forum.id);
                await this.loadForums();
            } catch (error) {
                console.error('Error deleting forum', error);
                alert('Failed to delete forum');
            }
        }
    }
}
