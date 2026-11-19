import { Component, inject, signal, OnInit } from '@angular/core';
import { ContentService } from '../../core/services/content.service';
import { PostDTO } from '../../core/models/content.model';
import { RouterLink } from '@angular/router';
import { DatePipe } from '@angular/common';

@Component({
    selector: 'app-post-list',
    standalone: true,
    imports: [RouterLink, DatePipe],
    template: `
    <div class="page-container">
      <div class="page-header">
        <h1 class="page-title">Posts</h1>
      </div>

      <div class="card table-container">
        @if (isLoading()) {
          <div class="loading-state">
            <span class="spinner"></span>
            Loading posts...
          </div>
        } @else {
          <table class="data-table">
            <thead>
              <tr>
                <th>Content</th>
                <th>Author</th>
                <th>Stats</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              @for (post of posts(); track post.id) {
                <tr>
                  <td class="content-cell">
                    <div class="post-text">{{ post.text }}</div>
                  </td>
                  <td>
                    <div class="author">{{ post.postedBy }}</div>
                  </td>
                  <td>
                    <div class="stats">
                      <span class="stat" title="Rating">
                        <span class="material-icons">star</span>
                        {{ post.rating || 0 }}
                      </span>
                      <span class="stat" title="Replies">
                        <span class="material-icons">comment</span>
                        {{ post.replies?.length || 0 }}
                      </span>
                    </div>
                  </td>
                  <td>
                    <div class="actions">
                      <button class="action-btn delete" (click)="deletePost(post)" title="Delete">
                        <span class="material-icons">delete</span>
                      </button>
                    </div>
                  </td>
                </tr>
              } @empty {
                <tr>
                  <td colspan="4" class="empty-state">No posts found.</td>
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
      vertical-align: top;
    }

    .content-cell {
      max-width: 400px;
    }

    .post-text {
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
      color: var(--text-primary);
    }

    .author {
      font-weight: 500;
      color: var(--primary-color);
    }

    .stats {
      display: flex;
      gap: 1rem;
    }

    .stat {
      display: flex;
      align-items: center;
      gap: 0.25rem;
      color: var(--text-secondary);
      font-size: 0.875rem;
    }

    .stat .material-icons {
      font-size: 1rem;
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
export class PostListComponent implements OnInit {
    private contentService = inject(ContentService);

    posts = signal<PostDTO[]>([]);
    isLoading = signal(true);

    ngOnInit() {
        this.loadPosts();
    }

    async loadPosts() {
        try {
            this.isLoading.set(true);
            const page = await this.contentService.findAllPosts();
            this.posts.set(page.content);
        } catch (error) {
            console.error('Error loading posts', error);
        } finally {
            this.isLoading.set(false);
        }
    }

    async deletePost(post: PostDTO) {
        if (confirm('Are you sure you want to delete this post?')) {
            try {
                await this.contentService.deletePost(post.id);
                await this.loadPosts();
            } catch (error) {
                console.error('Error deleting post', error);
                alert('Failed to delete post');
            }
        }
    }
}
