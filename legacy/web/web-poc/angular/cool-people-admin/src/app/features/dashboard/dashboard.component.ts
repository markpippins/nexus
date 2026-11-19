import { Component } from '@angular/core';

@Component({
    selector: 'app-dashboard',
    standalone: true,
    template: `
    <div class="dashboard">
      <h1 class="page-title">Dashboard</h1>
      
      <div class="stats-grid">
        <!-- Stats Cards -->
        <div class="stat-card">
          <div class="stat-label">Total Users</div>
          <div class="stat-value">1,234</div>
          <div class="stat-trend positive">
            <span class="material-icons">trending_up</span>
            +12% this month
          </div>
        </div>

        <div class="stat-card">
          <div class="stat-label">Active Forums</div>
          <div class="stat-value">56</div>
          <div class="stat-trend neutral">
            <span class="material-icons">forum</span>
            Active discussions
          </div>
        </div>

        <div class="stat-card">
          <div class="stat-label">Total Posts</div>
          <div class="stat-value">8,902</div>
          <div class="stat-trend neutral">
            <span class="material-icons">schedule</span>
            Last 24 hours
          </div>
        </div>
      </div>

      <!-- Recent Activity -->
      <div class="activity-section card">
        <div class="section-header">
          <h2 class="section-title">Recent Activity</h2>
        </div>
        <div class="empty-state">
          Activity feed coming soon...
        </div>
      </div>
    </div>
  `,
    styles: [`
    .dashboard {
      display: flex;
      flex-direction: column;
      gap: 2rem;
    }

    .page-title {
      font-size: 1.875rem;
      font-weight: 700;
      color: var(--text-primary);
      margin: 0;
    }

    .stats-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 1.5rem;
    }

    .stat-card {
      background: white;
      padding: 1.5rem;
      border-radius: 0.75rem;
      box-shadow: 0 1px 3px rgba(0,0,0,0.1);
      border: 1px solid var(--border-color);
    }

    .stat-label {
      color: var(--text-secondary);
      font-size: 0.875rem;
      font-weight: 500;
      margin-bottom: 0.5rem;
    }

    .stat-value {
      font-size: 1.875rem;
      font-weight: 700;
      color: var(--text-primary);
    }

    .stat-trend {
      display: flex;
      align-items: center;
      gap: 0.25rem;
      font-size: 0.875rem;
      margin-top: 0.5rem;
    }

    .stat-trend.positive { color: var(--success-color); }
    .stat-trend.neutral { color: var(--primary-color); }

    .stat-trend .material-icons {
      font-size: 1rem;
    }

    .activity-section {
      overflow: hidden;
    }

    .section-header {
      padding: 1.5rem;
      border-bottom: 1px solid var(--border-color);
    }

    .section-title {
      font-size: 1.125rem;
      font-weight: 700;
      color: var(--text-primary);
      margin: 0;
    }

    .empty-state {
      padding: 3rem;
      text-align: center;
      color: var(--text-secondary);
    }
  `]
})
export class DashboardComponent { }
