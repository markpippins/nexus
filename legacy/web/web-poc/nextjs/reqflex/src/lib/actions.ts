'use server';

import { revalidatePath } from 'next/cache';
import { requirementsData } from './data';
import type { Requirement, Status, SubItem } from './types';
import { z } from 'zod';

const RequirementSchema = z.object({
  name: z.string().min(3, 'Name must be at least 3 characters'),
  description: z.string().min(10, 'Description must be at least 10 characters'),
  technologies: z.string().min(1, 'Please add at least one technology'),
  status: z.enum(['pending', 'in-progress', 'complete']),
});

const SubItemSchema = z.object({
  name: z.string().min(3, 'Name must be at least 3 characters'),
  status: z.enum(['pending', 'in-progress', 'complete']),
});

const simulateLatency = () => new Promise(res => setTimeout(res, 500));

export async function getRequirements(status?: Status) {
  await simulateLatency();
  if (status) {
    return requirementsData.filter(req => req.status === status);
  }
  return requirementsData;
}

export async function getRequirementById(id: string) {
  await simulateLatency();
  return requirementsData.find(req => req.id === id) || null;
}

export async function createRequirement(formData: FormData) {
  const validatedFields = RequirementSchema.safeParse({
    name: formData.get('name'),
    description: formData.get('description'),
    technologies: formData.get('technologies'),
    status: formData.get('status'),
  });

  if (!validatedFields.success) {
    return {
      errors: validatedFields.error.flatten().fieldErrors,
    };
  }
  
  const { name, description, technologies, status } = validatedFields.data;

  const newRequirement: Requirement = {
    id: `REQ-${Date.now()}`,
    name,
    description,
    status,
    technologies: technologies.split(',').map(t => t.trim()),
    subItems: [],
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
  };

  requirementsData.unshift(newRequirement);
  revalidatePath('/');
  
  return { requirement: newRequirement };
}

export async function updateRequirement(id: string, formData: FormData) {
  const requirementIndex = requirementsData.findIndex(req => req.id === id);
  if (requirementIndex === -1) {
    throw new Error('Requirement not found');
  }

  const validatedFields = RequirementSchema.safeParse({
    name: formData.get('name'),
    description: formData.get('description'),
    technologies: formData.get('technologies'),
    status: formData.get('status'),
  });

  if (!validatedFields.success) {
    return {
      errors: validatedFields.error.flatten().fieldErrors,
    };
  }

  const { name, description, technologies, status } = validatedFields.data;

  const currentRequirement = requirementsData[requirementIndex];
  const updatedRequirement = {
    ...currentRequirement,
    name,
    description,
    status,
    technologies: technologies.split(',').map(t => t.trim()),
    updatedAt: new Date().toISOString(),
  };
  
  requirementsData[requirementIndex] = updatedRequirement;
  revalidatePath('/');
  revalidatePath(`/requirements/${id}`);

  return { requirement: updatedRequirement };
}

export async function deleteRequirement(id: string) {
  const index = requirementsData.findIndex(req => req.id === id);
  if (index > -1) {
    requirementsData.splice(index, 1);
    revalidatePath('/');
    return { success: true };
  }
  return { success: false, message: "Requirement not found" };
}

export async function addSubItem(requirementId: string, formData: FormData) {
  const requirement = requirementsData.find(req => req.id === requirementId);
  if (!requirement) {
    throw new Error('Requirement not found');
  }

  const validatedFields = SubItemSchema.safeParse({
    name: formData.get('name'),
    status: formData.get('status'),
  });

  if (!validatedFields.success) {
    return {
      errors: validatedFields.error.flatten().fieldErrors,
    };
  }

  const newSubItem: SubItem = {
    id: `SUB-${requirementId}-${Date.now()}`,
    name: validatedFields.data.name,
    status: validatedFields.data.status,
  };

  requirement.subItems.push(newSubItem);
  requirement.updatedAt = new Date().toISOString();
  revalidatePath(`/requirements/${requirementId}`);
  return { subItem: newSubItem };
}

export async function updateSubItem(requirementId: string, subItemId: string, formData: FormData) {
  const requirement = requirementsData.find(req => req.id === requirementId);
  if (!requirement) {
    throw new Error('Requirement not found');
  }

  const subItemIndex = requirement.subItems.findIndex(sub => sub.id === subItemId);
  if (subItemIndex === -1) {
    throw new Error('Sub-item not found');
  }

  const validatedFields = SubItemSchema.safeParse({
    name: formData.get('name'),
    status: formData.get('status'),
  });
  
  if (!validatedFields.success) {
    return {
      errors: validatedFields.error.flatten().fieldErrors,
    };
  }
  
  requirement.subItems[subItemIndex] = {
    ...requirement.subItems[subItemIndex],
    ...validatedFields.data,
  };

  requirement.updatedAt = new Date().toISOString();
  revalidatePath(`/requirements/${requirementId}`);
  return { subItem: requirement.subItems[subItemIndex] };
}

export async function deleteSubItem(requirementId: string, subItemId: string) {
  const requirement = requirementsData.find(req => req.id === requirementId);
  if (!requirement) {
    throw new Error('Requirement not found');
  }

  const subItemIndex = requirement.subItems.findIndex(sub => sub.id === subItemId);
  if (subItemIndex > -1) {
    requirement.subItems.splice(subItemIndex, 1);
    requirement.updatedAt = new Date().toISOString();
    revalidatePath(`/requirements/${requirementId}`);
    return { success: true };
  }
  return { success: false, message: 'Sub-item not found' };
}
