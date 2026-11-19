import { Component, ChangeDetectionStrategy, output, inject, signal, input, computed, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ServerProfileService } from '../../services/server-profile.service.js';
import { ServerProfile } from '../../models/server-profile.model.js';
import { User } from '../../models/user.model.js';
import { LoginDialogComponent } from '../login-dialog/login-dialog.component.js';
import { ToastService } from '../../services/toast.service.js';

type FormState = {
  id: string | null;
  name: string;
  brokerUrl: string;
  imageUrl: string;
  searchUrl: string;
  autoConnect: boolean;
}

@Component({
  selector: 'app-server-profiles-dialog',
  templateUrl: './server-profiles-dialog.component.html',
  imports: [CommonModule, LoginDialogComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ServerProfilesDialogComponent implements OnInit {
  profileService = inject(ServerProfileService);
  toastService = inject(ToastService);
  
  mountedProfileIds = input<string[]>([]);
  mountedProfileUsers = input<Map<string, User>>(new Map());
  initialProfileForEdit = input<ServerProfile | null>(null);

  close = output<void>();
  loginAndMount = output<{ profile: ServerProfile, username: string, password: string }>();
  unmountProfile = output<ServerProfile>();
  profileRenamed = output<{ oldName: string, newName: string, profile: ServerProfile }>();

  selectedProfileId = signal<string | null>(null);
  
  // Form state for editing/creating
  formState = signal<FormState | null>(null);

  // State for login dialog
  isLoginDialogOpen = signal(false);
  profileToLogin = signal<ServerProfile | null>(null);
  
  private originalProfileName: string | null = null;

  // Computed signal to find the full profile object when editing.
  selectedProfile = computed(() => {
    const form = this.formState();
    if (form && form.id) {
      return this.profileService.profiles().find(p => p.id === form.id) ?? null;
    }
    return null;
  });

  ngOnInit(): void {
    const profileToEdit = this.initialProfileForEdit();
    if (profileToEdit) {
      this.startEdit(profileToEdit);
    }
  }

  startAddNew(): void {
    this.formState.set({
      id: null,
      name: '',
      brokerUrl: '',
      imageUrl: '',
      searchUrl: '',
      autoConnect: false,
    });
    this.selectedProfileId.set(null);
    this.originalProfileName = null;
  }
  
  startEdit(profile: ServerProfile): void {
    this.formState.set({ ...profile, searchUrl: profile.searchUrl ?? '', autoConnect: profile.autoConnect ?? false });
    this.selectedProfileId.set(profile.id);
    this.originalProfileName = profile.name;
  }
  
  cancelEdit(): void {
    this.formState.set(null);
    this.selectedProfileId.set(null);
    this.originalProfileName = null;
  }

  async saveProfile(): Promise<void> {
    const state = this.formState();
    if (!state || !state.name.trim()) return;

    if (state.id) { // Editing existing
      const oldName = this.originalProfileName;
      
      const updatedProfile: ServerProfile = {
        id: state.id,
        name: state.name.trim(),
        brokerUrl: state.brokerUrl.trim(),
        imageUrl: state.imageUrl.trim(),
        // Conditionally add searchUrl only if it's not empty, making the persisted object cleaner.
        ...(state.searchUrl.trim() && { searchUrl: state.searchUrl.trim() }),
        autoConnect: state.autoConnect,
      };

      await this.profileService.updateProfile(updatedProfile);
      this.toastService.show(`Profile "${updatedProfile.name}" updated.`);
      
      if (oldName && oldName !== updatedProfile.name) {
          this.profileRenamed.emit({ oldName, newName: updatedProfile.name, profile: updatedProfile });
      }
      this.originalProfileName = updatedProfile.name;
    } else { // Adding new
      const newProfileData = {
        name: state.name.trim(),
        brokerUrl: state.brokerUrl.trim(),
        imageUrl: state.imageUrl.trim(),
        ...(state.searchUrl.trim() && { searchUrl: state.searchUrl.trim() }),
        autoConnect: state.autoConnect,
      };

      await this.profileService.addProfile(newProfileData);
      this.toastService.show(`Profile "${newProfileData.name}" created.`);
      this.formState.set(null);
      this.selectedProfileId.set(null);
      this.originalProfileName = null;
    }
  }
  
  async deleteProfile(id: string): Promise<void> {
    if (confirm('Are you sure you want to delete this profile?')) {
      const profileToUnmount = this.profileService.profiles().find(p => p.id === id);
      if (profileToUnmount && this.mountedProfileIds().includes(id)) {
        this.unmountProfile.emit(profileToUnmount);
      }
      await this.profileService.deleteProfile(id);
      if (this.formState()?.id === id) {
        this.formState.set(null);
      }
      this.selectedProfileId.set(null);
    }
  }

  openLoginDialog(profile: ServerProfile): void {
    this.profileToLogin.set(profile);
    this.isLoginDialogOpen.set(true);
  }

  closeLoginDialog(): void {
    this.isLoginDialogOpen.set(false);
    this.profileToLogin.set(null);
  }

  onLoginSubmitted({ username, password }: { username: string, password: string }): void {
    const profile = this.profileToLogin();
    if (profile) {
      this.loginAndMount.emit({ profile, username, password });
    }
    this.closeLoginDialog();
  }

  handleUnmount(profile: ServerProfile): void {
    this.unmountProfile.emit(profile);
  }

  onFormValueChange(event: Event, field: keyof Omit<FormState, 'id' | 'autoConnect'>): void {
    const value = (event.target as HTMLInputElement).value;
    this.formState.update(state => state ? { ...state, [field]: value } : null);
  }

  onCheckboxChange(event: Event, field: 'autoConnect'): void {
    const checked = (event.target as HTMLInputElement).checked;
    this.formState.update(state => state ? { ...state, [field]: checked } : null);
  }
}