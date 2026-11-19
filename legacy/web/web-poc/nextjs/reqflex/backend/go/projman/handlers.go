package main

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
)

// Requirement represents a service requirement
type Requirement struct {
	ID          string    `json:"id"`
	Name        string    `json:"name"`
	Description string    `json:"description"`
	Status      string    `json:"status"` // pending, in-progress, complete
	Technologies []string `json:"technologies"`
	SubItems    []SubItem `json:"subItems"`
	CreatedAt   string    `json:"createdAt"`
	UpdatedAt   string    `json:"updatedAt"`
}

// SubItem represents a sub-item of a requirement
type SubItem struct {
	ID     string `json:"id"`
	Name   string `json:"name"`
	Status string `json:"status"` // pending, in-progress, complete
}

var requirements []Requirement
var nextID = 1

func getRequirements(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(requirements)
}

func getRequirementByID(w http.ResponseWriter, r *http.Request) {
	id := strings.TrimPrefix(r.URL.Path, "/requirements/")
	for _, req := range requirements {
		if req.ID == id {
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(req)
			return
		}
	}
	http.Error(w, "Requirement not found", http.StatusNotFound)
}

func createRequirement(w http.ResponseWriter, r *http.Request) {
	var req Requirement
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	// Generate ID and set defaults
	req.ID = fmt.Sprintf("req-%d", nextID)
	nextID++
	if req.Status == "" {
		req.Status = "pending"
	}

	requirements = append(requirements, req)

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	json.NewEncoder(w).Encode(req)
}

func updateRequirement(w http.ResponseWriter, r *http.Request) {
	id := strings.TrimPrefix(r.URL.Path, "/requirements/")
	var updatedReq Requirement
	if err := json.NewDecoder(r.Body).Decode(&updatedReq); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	for i, req := range requirements {
		if req.ID == id {
			updatedReq.ID = id // Ensure ID doesn't change
			requirements[i] = updatedReq
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(updatedReq)
			return
		}
	}
	http.Error(w, "Requirement not found", http.StatusNotFound)
}

func deleteRequirement(w http.ResponseWriter, r *http.Request) {
	id := strings.TrimPrefix(r.URL.Path, "/requirements/")
	for i, req := range requirements {
		if req.ID == id {
			// Remove requirement from slice
			requirements = append(requirements[:i], requirements[i+1:]...)
			w.WriteHeader(http.StatusNoContent)
			return
		}
	}
	http.Error(w, "Requirement not found", http.StatusNotFound)
}

func getRequirementsByStatus(w http.ResponseWriter, r *http.Request) {
	status := strings.TrimPrefix(r.URL.Path, "/requirements/status/")
	var filtered []Requirement
	for _, req := range requirements {
		if req.Status == status {
			filtered = append(filtered, req)
		}
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(filtered)
}

func addSubItem(w http.ResponseWriter, r *http.Request) {
	pathParts := strings.Split(r.URL.Path, "/")
	if len(pathParts) < 4 {
		http.Error(w, "Invalid path", http.StatusBadRequest)
		return
	}
	reqID := pathParts[2]

	var subItem SubItem
	if err := json.NewDecoder(r.Body).Decode(&subItem); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	// Generate ID and set defaults
	subItem.ID = fmt.Sprintf("sub-%d", nextID)
	nextID++
	if subItem.Status == "" {
		subItem.Status = "pending"
	}

	for i, req := range requirements {
		if req.ID == reqID {
			requirements[i].SubItems = append(requirements[i].SubItems, subItem)
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(subItem)
			return
		}
	}
	http.Error(w, "Requirement not found", http.StatusNotFound)
}

func updateSubItem(w http.ResponseWriter, r *http.Request) {
	pathParts := strings.Split(r.URL.Path, "/")
	if len(pathParts) < 5 {
		http.Error(w, "Invalid path", http.StatusBadRequest)
		return
	}
	reqID := pathParts[2]
	subID := pathParts[4]

	var updatedSubItem SubItem
	if err := json.NewDecoder(r.Body).Decode(&updatedSubItem); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	for i, req := range requirements {
		if req.ID == reqID {
			for j, subItem := range requirements[i].SubItems {
				if subItem.ID == subID {
					updatedSubItem.ID = subID
					requirements[i].SubItems[j] = updatedSubItem
					w.Header().Set("Content-Type", "application/json")
					json.NewEncoder(w).Encode(updatedSubItem)
					return
				}
			}
		}
	}
	http.Error(w, "Sub-item not found", http.StatusNotFound)
}

func deleteSubItem(w http.ResponseWriter, r *http.Request) {
	pathParts := strings.Split(r.URL.Path, "/")
	if len(pathParts) < 5 {
		http.Error(w, "Invalid path", http.StatusBadRequest)
		return
	}
	reqID := pathParts[2]
	subID := pathParts[4]

	for i, req := range requirements {
		if req.ID == reqID {
			for j, subItem := range requirements[i].SubItems {
				if subItem.ID == subID {
					// Remove sub-item from slice
					requirements[i].SubItems = append(requirements[i].SubItems[:j], requirements[i].SubItems[j+1:]...)
					w.WriteHeader(http.StatusNoContent)
					return
				}
			}
		}
	}
	http.Error(w, "Sub-item not found", http.StatusNotFound)
}