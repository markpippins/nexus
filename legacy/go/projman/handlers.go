package main

import (
	"encoding/json"
	"log"
	"net/http"
	"strconv"
	"strings"

	"gorm.io/gorm"
)

// REQUIREMENT HANDLERS (Updated for database models)

func getRequirements(w http.ResponseWriter, r *http.Request) {
	log.Printf("GET /requirements - Retrieving all requirements")

	var requirements []Requirement
	query := DB.Preload("Project").Preload("Subsystem").Preload("Subsystem.Project").Preload("Feature").Preload("Feature.Subsystem").Preload("SubItems")

	if err := query.Find(&requirements).Error; err != nil {
		log.Printf("Error retrieving requirements: %v", err)
		http.Error(w, "Error retrieving requirements", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(requirements); err != nil {
		log.Printf("Error encoding requirements: %v", err)
		http.Error(w, "Error encoding requirements", http.StatusInternalServerError)
		return
	}
	log.Printf("GET /requirements - Successfully returned %d requirements", len(requirements))
}

func getRequirementByID(w http.ResponseWriter, r *http.Request) {
	idStr := strings.TrimPrefix(r.URL.Path, "/requirements/")
	id, err := strconv.ParseUint(idStr, 10, 32)
	if err != nil {
		log.Printf("GET /requirements/%s - Invalid requirement ID", idStr)
		http.Error(w, "Invalid requirement ID", http.StatusBadRequest)
		return
	}

	log.Printf("GET /requirements/%d - Retrieving requirement by ID", id)

	var requirement Requirement
	if err := DB.Preload("Project").Preload("Subsystem").Preload("Subsystem.Project").Preload("Feature").Preload("Feature.Subsystem").Preload("SubItems").First(&requirement, id).Error; err != nil {
		if err == gorm.ErrRecordNotFound {
			log.Printf("GET /requirements/%d - Requirement not found", id)
			http.Error(w, "Requirement not found", http.StatusNotFound)
		} else {
			log.Printf("Error retrieving requirement %d: %v", id, err)
			http.Error(w, "Error retrieving requirement", http.StatusInternalServerError)
		}
		return
	}

	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(requirement); err != nil {
		log.Printf("Error encoding requirement %d: %v", id, err)
		http.Error(w, "Error encoding requirement", http.StatusInternalServerError)
		return
	}
	log.Printf("GET /requirements/%d - Successfully returned requirement: %s", id, requirement.Name)
}

func createRequirement(w http.ResponseWriter, r *http.Request) {
	log.Printf("POST /requirements - Creating new requirement")

	var requirement Requirement
	if err := json.NewDecoder(r.Body).Decode(&requirement); err != nil {
		log.Printf("Error decoding requirement: %v", err)
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	log.Printf("Received requirement creation request: Name=%s, Description=%s", requirement.Name, requirement.Description)

	// Set defaults
	if requirement.Status == "" {
		requirement.Status = "pending"
	}

	if err := DB.Create(&requirement).Error; err != nil {
		log.Printf("Error creating requirement: %v", err)
		http.Error(w, "Error creating requirement", http.StatusInternalServerError)
		return
	}

	// Load the complete object with relationships
	DB.Preload("Project").Preload("Subsystem").Preload("Feature").First(&requirement, requirement.ID)

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	if err := json.NewEncoder(w).Encode(requirement); err != nil {
		log.Printf("Error encoding created requirement %d: %v", requirement.ID, err)
		return
	}
	log.Printf("POST /requirements - Successfully created requirement: ID=%d, Name=%s", requirement.ID, requirement.Name)
}

func updateRequirement(w http.ResponseWriter, r *http.Request) {
	idStr := strings.TrimPrefix(r.URL.Path, "/requirements/")
	id, err := strconv.ParseUint(idStr, 10, 32)
	if err != nil {
		log.Printf("PUT /requirements/%s - Invalid requirement ID", idStr)
		http.Error(w, "Invalid requirement ID", http.StatusBadRequest)
		return
	}

	log.Printf("PUT /requirements/%d - Updating requirement", id)

	var updatedRequirement Requirement
	if err := json.NewDecoder(r.Body).Decode(&updatedRequirement); err != nil {
		log.Printf("Error decoding requirement update for %d: %v", id, err)
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	log.Printf("Received requirement update request: ID=%d, Name=%s, Status=%s", id, updatedRequirement.Name, updatedRequirement.Status)

	updatedRequirement.ID = uint(id)
	if err := DB.Save(&updatedRequirement).Error; err != nil {
		log.Printf("Error updating requirement %d: %v", id, err)
		http.Error(w, "Error updating requirement", http.StatusInternalServerError)
		return
	}

	// Load the complete object with relationships
	DB.Preload("Project").Preload("Subsystem").Preload("Feature").First(&updatedRequirement, updatedRequirement.ID)

	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(updatedRequirement); err != nil {
		log.Printf("Error encoding updated requirement %d: %v", id, err)
		return
	}
	log.Printf("PUT /requirements/%d - Successfully updated requirement: Name=%s, Status=%s", id, updatedRequirement.Name, updatedRequirement.Status)
}

func deleteRequirement(w http.ResponseWriter, r *http.Request) {
	idStr := strings.TrimPrefix(r.URL.Path, "/requirements/")
	id, err := strconv.ParseUint(idStr, 10, 32)
	if err != nil {
		log.Printf("DELETE /requirements/%s - Invalid requirement ID", idStr)
		http.Error(w, "Invalid requirement ID", http.StatusBadRequest)
		return
	}

	log.Printf("DELETE /requirements/%d - Deleting requirement", id)

	if err := DB.Delete(&Requirement{}, uint(id)).Error; err != nil {
		log.Printf("Error deleting requirement %d: %v", id, err)
		http.Error(w, "Error deleting requirement", http.StatusInternalServerError)
		return
	}

	w.WriteHeader(http.StatusNoContent)
	log.Printf("DELETE /requirements/%d - Successfully deleted requirement", id)
}

func getRequirementsByStatus(w http.ResponseWriter, r *http.Request) {
	status := strings.TrimPrefix(r.URL.Path, "/requirements/status/")
	log.Printf("GET /requirements/status/%s - Retrieving requirements by status", status)

	var requirements []Requirement
	if err := DB.Preload("Project").Preload("Subsystem").Preload("Feature").Where("status = ?", status).Find(&requirements).Error; err != nil {
		log.Printf("Error retrieving requirements with status %s: %v", status, err)
		http.Error(w, "Error retrieving requirements", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(requirements); err != nil {
		log.Printf("Error encoding requirements with status %s: %v", status, err)
		http.Error(w, "Error encoding requirements", http.StatusInternalServerError)
		return
	}
	log.Printf("GET /requirements/status/%s - Successfully returned %d requirements", status, len(requirements))
}

func addSubItem(w http.ResponseWriter, r *http.Request) {
	pathParts := strings.Split(r.URL.Path, "/")
	if len(pathParts) < 4 {
		log.Printf("POST /requirements/%s/subitems - Invalid path", pathParts[2])
		http.Error(w, "Invalid path", http.StatusBadRequest)
		return
	}
	reqIDStr := pathParts[2]
	reqID, err := strconv.ParseUint(reqIDStr, 10, 32)
	if err != nil {
		log.Printf("POST /requirements/%s/subitems - Invalid requirement ID", reqIDStr)
		http.Error(w, "Invalid requirement ID", http.StatusBadRequest)
		return
	}

	log.Printf("POST /requirements/%d/subitems - Adding sub-item to requirement", reqID)

	var subItem SubItem
	if err := json.NewDecoder(r.Body).Decode(&subItem); err != nil {
		log.Printf("Error decoding sub-item for requirement %d: %v", reqID, err)
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	log.Printf("Received sub-item creation request: Name=%s, Status=%s for requirement %d", subItem.Name, subItem.Status, reqID)

	// Set requirement ID and defaults
	subItem.RequirementID = uint(reqID)
	if subItem.Status == "" {
		subItem.Status = "pending"
	}

	if err := DB.Create(&subItem).Error; err != nil {
		log.Printf("Error creating sub-item: %v", err)
		http.Error(w, "Error creating sub-item", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	if err := json.NewEncoder(w).Encode(subItem); err != nil {
		log.Printf("Error encoding created sub-item %d: %v", subItem.ID, err)
		return
	}
	log.Printf("POST /requirements/%d/subitems - Successfully added sub-item: ID=%d, Name=%s", reqID, subItem.ID, subItem.Name)
}

func updateSubItem(w http.ResponseWriter, r *http.Request) {
	pathParts := strings.Split(r.URL.Path, "/")
	if len(pathParts) < 5 {
		log.Printf("PUT /requirements/%s/subitems/%s - Invalid path", pathParts[2], pathParts[4])
		http.Error(w, "Invalid path", http.StatusBadRequest)
		return
	}
	reqIDStr := pathParts[2]
	reqID, err := strconv.ParseUint(reqIDStr, 10, 32)
	if err != nil {
		log.Printf("PUT /requirements/%s/subitems/%s - Invalid requirement ID", reqIDStr, pathParts[4])
		http.Error(w, "Invalid requirement ID", http.StatusBadRequest)
		return
	}
	subIDStr := pathParts[4]
	subID, err := strconv.ParseUint(subIDStr, 10, 32)
	if err != nil {
		log.Printf("PUT /requirements/%d/subitems/%s - Invalid sub-item ID", reqID, subIDStr)
		http.Error(w, "Invalid sub-item ID", http.StatusBadRequest)
		return
	}

	log.Printf("PUT /requirements/%d/subitems/%d - Updating sub-item", reqID, subID)

	var updatedSubItem SubItem
	if err := json.NewDecoder(r.Body).Decode(&updatedSubItem); err != nil {
		log.Printf("Error decoding sub-item update for %d in requirement %d: %v", subID, reqID, err)
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	log.Printf("Received sub-item update request: ID=%d, Name=%s, Status=%s for requirement %d", subID, updatedSubItem.Name, updatedSubItem.Status, reqID)

	updatedSubItem.ID = uint(subID)
	updatedSubItem.RequirementID = uint(reqID)
	if err := DB.Save(&updatedSubItem).Error; err != nil {
		log.Printf("Error updating sub-item %d: %v", subID, err)
		http.Error(w, "Error updating sub-item", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(updatedSubItem); err != nil {
		log.Printf("Error encoding updated sub-item %d: %v", subID, err)
		return
	}
	log.Printf("PUT /requirements/%d/subitems/%d - Successfully updated sub-item: Name=%s, Status=%s", reqID, subID, updatedSubItem.Name, updatedSubItem.Status)
}

func deleteSubItem(w http.ResponseWriter, r *http.Request) {
	pathParts := strings.Split(r.URL.Path, "/")
	if len(pathParts) < 5 {
		log.Printf("DELETE /requirements/%s/subitems/%s - Invalid path", pathParts[2], pathParts[4])
		http.Error(w, "Invalid path", http.StatusBadRequest)
		return
	}
	reqIDStr := pathParts[2]
	reqID, err := strconv.ParseUint(reqIDStr, 10, 32)
	if err != nil {
		log.Printf("DELETE /requirements/%s/subitems/%s - Invalid requirement ID", reqIDStr, pathParts[4])
		http.Error(w, "Invalid requirement ID", http.StatusBadRequest)
		return
	}
	subIDStr := pathParts[4]
	subID, err := strconv.ParseUint(subIDStr, 10, 32)
	if err != nil {
		log.Printf("DELETE /requirements/%d/subitems/%s - Invalid sub-item ID", reqID, subIDStr)
		http.Error(w, "Invalid sub-item ID", http.StatusBadRequest)
		return
	}

	log.Printf("DELETE /requirements/%d/subitems/%d - Deleting sub-item", reqID, subID)

	if err := DB.Delete(&SubItem{}, uint(subID)).Error; err != nil {
		log.Printf("Error deleting sub-item %d: %v", subID, err)
		http.Error(w, "Error deleting sub-item", http.StatusInternalServerError)
		return
	}

	w.WriteHeader(http.StatusNoContent)
	log.Printf("DELETE /requirements/%d/subitems/%d - Successfully deleted sub-item", reqID, subID)
}
