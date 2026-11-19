import { Component, Output, EventEmitter } from '@angular/core';
import { CommonModule } from '@angular/common';

@Component({
  selector: 'app-navigation',
  standalone: true,
  imports: [CommonModule],
  template: `
    <nav class="navigation">
      <div class="nav-brand">
        <h2>🚀 Projman</h2>
      </div>
      <ul class="nav-menu">
        <li class="nav-item" [class.active]="activeTab === 'requirements'">
          <button class="nav-btn" (click)="setActiveTab('requirements')">
            <span class="nav-icon">📋</span>
            Requirements
          </button>
        </li>
        <li class="nav-item" [class.active]="activeTab === 'projects'">
          <button class="nav-btn" (click)="setActiveTab('projects')">
            <span class="nav-icon">📁</span>
            Projects
          </button>
        </li>
        <li class="nav-item" [class.active]="activeTab === 'subsystems'">
          <button class="nav-btn" (click)="setActiveTab('subsystems')">
            <span class="nav-icon">⚙️</span>
            Subsystems
          </button>
        </li>
        <li class="nav-item" [class.active]="activeTab === 'features'">
          <button class="nav-btn" (click)="setActiveTab('features')">
            <span class="nav-icon">⚡</span>
            Features
          </button>
        </li>
        <li class="nav-item" [class.active]="activeTab === 'data-dictionary'">
          <button class="nav-btn" (click)="setActiveTab('data-dictionary')">
            <span class="nav-icon">📚</span>
            Data Dictionary
          </button>
        </li>
      </ul>
    </nav>
  `,
  styles: [`
    .navigation {
      background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
      padding: 1rem 2rem;
      box-shadow: 0 4px 12px rgba(0,0,0,0.1);
      position: sticky;
      top: 0;
      z-index: 1000;
    }

    .nav-brand {
      text-align: center;
      margin-bottom: 1.5rem;
    }

    .nav-brand h2 {
      color: white;
      font-size: 1.5rem;
      font-weight: 700;
      margin: 0;
      text-shadow: 0 2px 4px rgba(0,0,0,0.2);
    }

    .nav-menu {
      display: flex;
      justify-content: center;
      gap: 0.5rem;
      flex-wrap: wrap;
      list-style: none;
      padding: 0;
      margin: 0;
    }

    .nav-item {
      margin: 0;
    }

    .nav-btn {
      background: rgba(255,255,255,0.2);
      border: none;
      color: white;
      padding: 0.75rem 1rem;
      border-radius: 10px;
      cursor: pointer;
      transition: all 0.3s ease;
      display: flex;
      align-items: center;
      gap: 0.5rem;
      font-size: 0.95rem;
      font-weight: 600;
      backdrop-filter: blur(10px);
    }

    .nav-btn:hover {
      background: rgba(255,255,255,0.3);
      transform: translateY(-2px);
      box-shadow: 0 4px 8px rgba(0,0,0,0.2);
    }

    .nav-item.active .nav-btn {
      background: white;
      color: #667eea;
      box-shadow: 0 4px 8px rgba(0,0,0,0.3);
      transform: translateY(-1px);
    }

    .nav-icon {
      font-size: 1.1rem;
    }

    @media (max-width: 768px) {
      .navigation {
        padding: 0.75rem 1rem;
      }

      .nav-menu {
        gap: 0.25rem;
      }

      .nav-btn {
        padding: 0.5rem 0.75rem;
        font-size: 0.85rem;
      }

      .nav-brand h2 {
        font-size: 1.25rem;
      }

      .nav-icon {
        font-size: 1rem;
      }
    }
  `]
})
export class NavigationComponent {
  activeTab: string = 'requirements';
  @Output() tabChange = new EventEmitter<string>();

  setActiveTab(tab: string) {
    this.activeTab = tab;
    this.tabChange.emit(tab);
  }
}