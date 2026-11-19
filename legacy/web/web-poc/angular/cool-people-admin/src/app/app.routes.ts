import { Routes } from '@angular/router';
import { authGuard } from './core/guards/auth.guard';

export const routes: Routes = [
    {
        path: 'login',
        loadComponent: () => import('./features/login/login.component').then(m => m.LoginComponent)
    },
    {
        path: '',
        loadComponent: () => import('./layout/shell.component').then(m => m.ShellComponent),
        canActivate: [authGuard],
        children: [
            { path: '', redirectTo: 'dashboard', pathMatch: 'full' },
            {
                path: 'dashboard',
                loadComponent: () => import('./features/dashboard/dashboard.component').then(m => m.DashboardComponent)
            },
            {
                path: 'users',
                loadComponent: () => import('./features/users/user-list.component').then(m => m.UserListComponent)
            },
            {
                path: 'users/new',
                loadComponent: () => import('./features/users/user-detail.component').then(m => m.UserDetailComponent)
            },
            {
                path: 'users/:id',
                loadComponent: () => import('./features/users/user-detail.component').then(m => m.UserDetailComponent)
            },
            {
                path: 'profiles',
                loadComponent: () => import('./features/profiles/profile-list.component').then(m => m.ProfileListComponent)
            },
            {
                path: 'profiles/:id',
                loadComponent: () => import('./features/profiles/profile-detail.component').then(m => m.ProfileDetailComponent)
            },
            {
                path: 'forums',
                loadComponent: () => import('./features/content/forum-list.component').then(m => m.ForumListComponent)
            },
            {
                path: 'posts',
                loadComponent: () => import('./features/content/post-list.component').then(m => m.PostListComponent)
            }
        ]
    },
    { path: '**', redirectTo: 'dashboard' }
];
