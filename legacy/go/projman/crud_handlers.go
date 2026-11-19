package main

import (
	"encoding/json"
	"log"
	"net/http"
	"strconv"
	"strings"

	"gorm.io/gorm"
)

// PROJECT HANDLERS

func getProjects(w http.ResponseWriter, r *http.Request) {
	log.Printf("GET /projects - Retrieving all projects")

	var projects []Project
	if err := DB.Find(&projects).Error; err != nil {
		log.Printf("Error retrieving projects: %v", err)
		http.Error(w, "Error retrieving projects", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(projects); err != nil {
		log.Printf("Error encoding projects: %v", err)
		http.Error(w, "Error encoding projects", http.StatusInternalServerError)
		return
	}
	log.Printf("GET /projects - Successfully returned %d projects", len(projects))
}

func getProjectByID(w http.ResponseWriter, r *http.Request) {
	idStr := strings.TrimPrefix(r.URL.Path, "/projects/")
	id, err := strconv.ParseUint(idStr, 10, 32)
	if err != nil {
		log.Printf("GET /projects/%s - Invalid project ID", idStr)
		http.Error(w, "Invalid project ID", http.StatusBadRequest)
		return
	}

	log.Printf("GET /projects/%d - Retrieving project by ID", id)

	var project Project
	if err := DB.Preload("Subsystems").Preload("Requirements").First(&project, id).Error; err != nil {
		if err == gorm.ErrRecordNotFound {
			log.Printf("GET /projects/%d - Project not found", id)
			http.Error(w, "Project not found", http.StatusNotFound)
		} else {
			log.Printf("Error retrieving project %d: %v", id, err)
			http.Error(w, "Error retrieving project", http.StatusInternalServerError)
		}
		return
	}

	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(project); err != nil {
		log.Printf("Error encoding project %d: %v", id, err)
		http.Error(w, "Error encoding project", http.StatusInternalServerError)
		return
	}
	log.Printf("GET /projects/%d - Successfully returned project: %s", id, project.Name)
}

func createProject(w http.ResponseWriter, r *http.Request) {
	log.Printf("POST /projects - Creating new project")

	var project Project
	if err := json.NewDecoder(r.Body).Decode(&project); err != nil {
		log.Printf("Error decoding project: %v", err)
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	log.Printf("Received project creation request: Name=%s", project.Name)

	if err := DB.Create(&project).Error; err != nil {
		log.Printf("Error creating project: %v", err)
		http.Error(w, "Error creating project", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	if err := json.NewEncoder(w).Encode(project); err != nil {
		log.Printf("Error encoding created project %d: %v", project.ID, err)
		return
	}
	log.Printf("POST /projects - Successfully created project: ID=%d, Name=%s", project.ID, project.Name)
}

func updateProject(w http.ResponseWriter, r *http.Request) {
	idStr := strings.TrimPrefix(r.URL.Path, "/projects/")
	id, err := strconv.ParseUint(idStr, 10, 32)
	if err != nil {
		log.Printf("PUT /projects/%s - Invalid project ID", idStr)
		http.Error(w, "Invalid project ID", http.StatusBadRequest)
		return
	}

	log.Printf("PUT /projects/%d - Updating project", id)

	var updatedProject Project
	if err := json.NewDecoder(r.Body).Decode(&updatedProject); err != nil {
		log.Printf("Error decoding project update for %d: %v", id, err)
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	log.Printf("Received project update request: ID=%d, Name=%s", id, updatedProject.Name)

	updatedProject.ID = uint(id)
	if err := DB.Save(&updatedProject).Error; err != nil {
		log.Printf("Error updating project %d: %v", id, err)
		http.Error(w, "Error updating project", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(updatedProject); err != nil {
		log.Printf("Error encoding updated project %d: %v", id, err)
		return
	}
	log.Printf("PUT /projects/%d - Successfully updated project: Name=%s", id, updatedProject.Name)
}

func deleteProject(w http.ResponseWriter, r *http.Request) {
	idStr := strings.TrimPrefix(r.URL.Path, "/projects/")
	id, err := strconv.ParseUint(idStr, 10, 32)
	if err != nil {
		log.Printf("DELETE /projects/%s - Invalid project ID", idStr)
		http.Error(w, "Invalid project ID", http.StatusBadRequest)
		return
	}

	log.Printf("DELETE /projects/%d - Deleting project", id)

	if err := DB.Delete(&Project{}, uint(id)).Error; err != nil {
		log.Printf("Error deleting project %d: %v", id, err)
		http.Error(w, "Error deleting project", http.StatusInternalServerError)
		return
	}

	w.WriteHeader(http.StatusNoContent)
	log.Printf("DELETE /projects/%d - Successfully deleted project", id)
}

// SUBSYSTEM HANDLERS

func getSubsystems(w http.ResponseWriter, r *http.Request) {
	log.Printf("GET /subsystems - Retrieving all subsystems")

	var subsystems []Subsystem
	if err := DB.Preload("Project").Find(&subsystems).Error; err != nil {
		log.Printf("Error retrieving subsystems: %v", err)
		http.Error(w, "Error retrieving subsystems", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(subsystems); err != nil {
		log.Printf("Error encoding subsystems: %v", err)
		http.Error(w, "Error encoding subsystems", http.StatusInternalServerError)
		return
	}
	log.Printf("GET /subsystems - Successfully returned %d subsystems", len(subsystems))
}

func getSubsystemByID(w http.ResponseWriter, r *http.Request) {
	idStr := strings.TrimPrefix(r.URL.Path, "/subsystems/")
	id, err := strconv.ParseUint(idStr, 10, 32)
	if err != nil {
		log.Printf("GET /subsystems/%s - Invalid subsystem ID", idStr)
		http.Error(w, "Invalid subsystem ID", http.StatusBadRequest)
		return
	}

	log.Printf("GET /subsystems/%d - Retrieving subsystem by ID", id)

	var subsystem Subsystem
	if err := DB.Preload("Project").Preload("Features").Preload("Requirements").First(&subsystem, id).Error; err != nil {
		if err == gorm.ErrRecordNotFound {
			log.Printf("GET /subsystems/%d - Subsystem not found", id)
			http.Error(w, "Subsystem not found", http.StatusNotFound)
		} else {
			log.Printf("Error retrieving subsystem %d: %v", id, err)
			http.Error(w, "Error retrieving subsystem", http.StatusInternalServerError)
		}
		return
	}

	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(subsystem); err != nil {
		log.Printf("Error encoding subsystem %d: %v", id, err)
		http.Error(w, "Error encoding subsystem", http.StatusInternalServerError)
		return
	}
	log.Printf("GET /subsystems/%d - Successfully returned subsystem: %s", id, subsystem.Name)
}

func createSubsystem(w http.ResponseWriter, r *http.Request) {
	log.Printf("POST /subsystems - Creating new subsystem")

	var subsystem Subsystem
	if err := json.NewDecoder(r.Body).Decode(&subsystem); err != nil {
		log.Printf("Error decoding subsystem: %v", err)
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	log.Printf("Received subsystem creation request: Name=%s, ProjectID=%d", subsystem.Name, subsystem.ProjectID)

	if err := DB.Create(&subsystem).Error; err != nil {
		log.Printf("Error creating subsystem: %v", err)
		http.Error(w, "Error creating subsystem", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	if err := json.NewEncoder(w).Encode(subsystem); err != nil {
		log.Printf("Error encoding created subsystem %d: %v", subsystem.ID, err)
		return
	}
	log.Printf("POST /subsystems - Successfully created subsystem: ID=%d, Name=%s", subsystem.ID, subsystem.Name)
}

func updateSubsystem(w http.ResponseWriter, r *http.Request) {
	idStr := strings.TrimPrefix(r.URL.Path, "/subsystems/")
	id, err := strconv.ParseUint(idStr, 10, 32)
	if err != nil {
		log.Printf("PUT /subsystems/%s - Invalid subsystem ID", idStr)
		http.Error(w, "Invalid subsystem ID", http.StatusBadRequest)
		return
	}

	log.Printf("PUT /subsystems/%d - Updating subsystem", id)

	var updatedSubsystem Subsystem
	if err := json.NewDecoder(r.Body).Decode(&updatedSubsystem); err != nil {
		log.Printf("Error decoding subsystem update for %d: %v", id, err)
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	log.Printf("Received subsystem update request: ID=%d, Name=%s, ProjectID=%d", id, updatedSubsystem.Name, updatedSubsystem.ProjectID)

	updatedSubsystem.ID = uint(id)
	if err := DB.Save(&updatedSubsystem).Error; err != nil {
		log.Printf("Error updating subsystem %d: %v", id, err)
		http.Error(w, "Error updating subsystem", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(updatedSubsystem); err != nil {
		log.Printf("Error encoding updated subsystem %d: %v", id, err)
		return
	}
	log.Printf("PUT /subsystems/%d - Successfully updated subsystem: Name=%s", id, updatedSubsystem.Name)
}

func deleteSubsystem(w http.ResponseWriter, r *http.Request) {
	idStr := strings.TrimPrefix(r.URL.Path, "/subsystems/")
	id, err := strconv.ParseUint(idStr, 10, 32)
	if err != nil {
		log.Printf("DELETE /subsystems/%s - Invalid subsystem ID", idStr)
		http.Error(w, "Invalid subsystem ID", http.StatusBadRequest)
		return
	}

	log.Printf("DELETE /subsystems/%d - Deleting subsystem", id)

	if err := DB.Delete(&Subsystem{}, uint(id)).Error; err != nil {
		log.Printf("Error deleting subsystem %d: %v", id, err)
		http.Error(w, "Error deleting subsystem", http.StatusInternalServerError)
		return
	}

	w.WriteHeader(http.StatusNoContent)
	log.Printf("DELETE /subsystems/%d - Successfully deleted subsystem", id)
}

// FEATURE HANDLERS

func getFeatures(w http.ResponseWriter, r *http.Request) {
	log.Printf("GET /features - Retrieving all features")

	var features []Feature
	if err := DB.Preload("Subsystem").Preload("Subsystem.Project").Find(&features).Error; err != nil {
		log.Printf("Error retrieving features: %v", err)
		http.Error(w, "Error retrieving features", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(features); err != nil {
		log.Printf("Error encoding features: %v", err)
		http.Error(w, "Error encoding features", http.StatusInternalServerError)
		return
	}
	log.Printf("GET /features - Successfully returned %d features", len(features))
}

func getFeatureByID(w http.ResponseWriter, r *http.Request) {
	idStr := strings.TrimPrefix(r.URL.Path, "/features/")
	id, err := strconv.ParseUint(idStr, 10, 32)
	if err != nil {
		log.Printf("GET /features/%s - Invalid feature ID", idStr)
		http.Error(w, "Invalid feature ID", http.StatusBadRequest)
		return
	}

	log.Printf("GET /features/%d - Retrieving feature by ID", id)

	var feature Feature
	if err := DB.Preload("Subsystem").Preload("Subsystem.Project").Preload("Requirements").First(&feature, id).Error; err != nil {
		if err == gorm.ErrRecordNotFound {
			log.Printf("GET /features/%d - Feature not found", id)
			http.Error(w, "Feature not found", http.StatusNotFound)
		} else {
			log.Printf("Error retrieving feature %d: %v", id, err)
			http.Error(w, "Error retrieving feature", http.StatusInternalServerError)
		}
		return
	}

	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(feature); err != nil {
		log.Printf("Error encoding feature %d: %v", id, err)
		http.Error(w, "Error encoding feature", http.StatusInternalServerError)
		return
	}
	log.Printf("GET /features/%d - Successfully returned feature: %s", id, feature.Name)
}

func createFeature(w http.ResponseWriter, r *http.Request) {
	log.Printf("POST /features - Creating new feature")

	var feature Feature
	if err := json.NewDecoder(r.Body).Decode(&feature); err != nil {
		log.Printf("Error decoding feature: %v", err)
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	log.Printf("Received feature creation request: Name=%s, SubsystemID=%d", feature.Name, feature.SubsystemID)

	if err := DB.Create(&feature).Error; err != nil {
		log.Printf("Error creating feature: %v", err)
		http.Error(w, "Error creating feature", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	if err := json.NewEncoder(w).Encode(feature); err != nil {
		log.Printf("Error encoding created feature %d: %v", feature.ID, err)
		return
	}
	log.Printf("POST /features - Successfully created feature: ID=%d, Name=%s", feature.ID, feature.Name)
}

func updateFeature(w http.ResponseWriter, r *http.Request) {
	idStr := strings.TrimPrefix(r.URL.Path, "/features/")
	id, err := strconv.ParseUint(idStr, 10, 32)
	if err != nil {
		log.Printf("PUT /features/%s - Invalid feature ID", idStr)
		http.Error(w, "Invalid feature ID", http.StatusBadRequest)
		return
	}

	log.Printf("PUT /features/%d - Updating feature", id)

	var updatedFeature Feature
	if err := json.NewDecoder(r.Body).Decode(&updatedFeature); err != nil {
		log.Printf("Error decoding feature update for %d: %v", id, err)
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	log.Printf("Received feature update request: ID=%d, Name=%s, SubsystemID=%d", id, updatedFeature.Name, updatedFeature.SubsystemID)

	updatedFeature.ID = uint(id)
	if err := DB.Save(&updatedFeature).Error; err != nil {
		log.Printf("Error updating feature %d: %v", id, err)
		http.Error(w, "Error updating feature", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(updatedFeature); err != nil {
		log.Printf("Error encoding updated feature %d: %v", id, err)
		return
	}
	log.Printf("PUT /features/%d - Successfully updated feature: Name=%s", id, updatedFeature.Name)
}

func deleteFeature(w http.ResponseWriter, r *http.Request) {
	idStr := strings.TrimPrefix(r.URL.Path, "/features/")
	id, err := strconv.ParseUint(idStr, 10, 32)
	if err != nil {
		log.Printf("DELETE /features/%s - Invalid feature ID", idStr)
		http.Error(w, "Invalid feature ID", http.StatusBadRequest)
		return
	}

	log.Printf("DELETE /features/%d - Deleting feature", id)

	if err := DB.Delete(&Feature{}, uint(id)).Error; err != nil {
		log.Printf("Error deleting feature %d: %v", id, err)
		http.Error(w, "Error deleting feature", http.StatusInternalServerError)
		return
	}

	w.WriteHeader(http.StatusNoContent)
	log.Printf("DELETE /features/%d - Successfully deleted feature", id)
}

// LOOKUP ENDPOINTS

func getSubsystemsByProject(w http.ResponseWriter, r *http.Request) {
	idStr := strings.TrimPrefix(r.URL.Path, "/projects/")
	idStr = strings.TrimSuffix(idStr, "/subsystems")
	id, err := strconv.ParseUint(idStr, 10, 32)
	if err != nil {
		log.Printf("GET /projects/%s/subsystems - Invalid project ID", idStr)
		http.Error(w, "Invalid project ID", http.StatusBadRequest)
		return
	}

	log.Printf("GET /projects/%d/subsystems - Retrieving subsystems for project", id)

	var subsystems []Subsystem
	if err := DB.Where("project_id = ?", uint(id)).Find(&subsystems).Error; err != nil {
		log.Printf("Error retrieving subsystems for project %d: %v", id, err)
		http.Error(w, "Error retrieving subsystems", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(subsystems); err != nil {
		log.Printf("Error encoding subsystems for project %d: %v", id, err)
		http.Error(w, "Error encoding subsystems", http.StatusInternalServerError)
		return
	}
	log.Printf("GET /projects/%d/subsystems - Successfully returned %d subsystems", id, len(subsystems))
}

func getFeaturesBySubsystem(w http.ResponseWriter, r *http.Request) {
	idStr := strings.TrimPrefix(r.URL.Path, "/subsystems/")
	idStr = strings.TrimSuffix(idStr, "/features")
	id, err := strconv.ParseUint(idStr, 10, 32)
	if err != nil {
		log.Printf("GET /subsystems/%s/features - Invalid subsystem ID", idStr)
		http.Error(w, "Invalid subsystem ID", http.StatusBadRequest)
		return
	}

	log.Printf("GET /subsystems/%d/features - Retrieving features for subsystem", id)

	var features []Feature
	if err := DB.Where("subsystem_id = ?", uint(id)).Find(&features).Error; err != nil {
		log.Printf("Error retrieving features for subsystem %d: %v", id, err)
		http.Error(w, "Error retrieving features", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(features); err != nil {
		log.Printf("Error encoding features for subsystem %d: %v", id, err)
		http.Error(w, "Error encoding features", http.StatusInternalServerError)
		return
	}
	log.Printf("GET /subsystems/%d/features - Successfully returned %d features", id, len(features))
}
