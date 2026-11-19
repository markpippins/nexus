import { Component, ChangeDetectionStrategy, output, inject, signal, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { UiPreferencesService, UiPreferences, Theme, FontSize } from '../../services/ui-preferences.service.js';

interface PanelPreference {
  key: keyof UiPreferences;
  label: string;
}

@Component({
  selector: 'app-preferences-dialog',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './preferences-dialog.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class PreferencesDialogComponent implements OnInit {
  private uiPreferencesService = inject(UiPreferencesService);
  
  close = output<void>();
  save = output<Partial<UiPreferences>>();

  formState = signal<Partial<UiPreferences>>({});
  activeTab = signal<'appearance' | 'panels'>('appearance');

  themes: { id: Theme; name: string }[] = [
    { id: 'theme-light', name: 'Light' },
    { id: 'theme-steel', name: 'Steel' },
    { id: 'theme-dark', name: 'Dark' },
  ];

  fontSizes: { id: FontSize; name: string }[] = [
    { id: 'sm', name: 'Small' },
    { id: 'base', name: 'Medium' },
    { id: 'lg', name: 'Large' },
  ];

  panels: PanelPreference[] = [
    { key: 'isSidebarVisible', label: 'Show Main Sidebar' },
    { key: 'isTreeVisible', label: 'Show Folder Tree Pane' },
    { key: 'isChatVisible', label: 'Show Chat Pane' },
    { key: 'isNotesVisible', label: 'Show Notes Pane' },
    { key: 'isDetailPaneOpen', label: 'Show Details Pane' },
    { key: 'isSavedItemsVisible', label: 'Show Saved Items Pane' },
    { key: 'isRssFeedVisible', label: 'Show RSS Feed Pane' },
    { key: 'isStreamVisible', label: 'Show Idea Stream Pane' },
    { key: 'isStreamPaneCollapsed', label: 'Collapse Idea Stream by default' },
    { key: 'isConsoleCollapsed', label: 'Collapse Console by default' }
  ];

  ngOnInit(): void {
    // Initialize form state from the service's current preferences
    this.formState.set(this.uiPreferencesService.currentPreferences());
  }

  onValueChange(field: keyof UiPreferences, value: any): void {
    this.formState.update(state => ({ ...state, [field]: value }));
  }

  onCheckboxChange(event: Event, field: keyof UiPreferences): void {
    const checked = (event.target as HTMLInputElement).checked;
    this.onValueChange(field, checked);
  }

  onSave(): void {
    this.save.emit(this.formState());
  }
}
