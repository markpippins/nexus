package main

import (
	"net/http"
	"strings"
)

// Router handles the routing for our requirements service
type Router struct {
	mux *http.ServeMux
}

func NewRouter() *Router {
	r := &Router{
		mux: http.NewServeMux(),
	}

	// Register routes
	r.mux.HandleFunc("/health", healthHandler)
	r.mux.HandleFunc("/requirements", requirementsHandler)
	r.mux.HandleFunc("/requirements/", requirementsHandlerWithID)
	r.mux.HandleFunc("/requirements/status/", requirementsByStatusHandler)

	return r
}

func (r *Router) ServeHTTP(w http.ResponseWriter, req *http.Request) {
	r.mux.ServeHTTP(w, req)
}

func healthHandler(w http.ResponseWriter, r *http.Request) {
	w.WriteHeader(http.StatusOK)
	if _, err := w.Write([]byte("OK")); err != nil {
		// Handle error if needed
	}
}

func requirementsHandler(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodGet:
		getRequirements(w, r)
	case http.MethodPost:
		createRequirement(w, r)
	default:
		w.WriteHeader(http.StatusMethodNotAllowed)
	}
}

// requirementsHandlerWithID handles requirements with specific IDs and sub-items
func requirementsHandlerWithID(w http.ResponseWriter, r *http.Request) {
	path := r.URL.Path

	// Check if this is a sub-items operation
	if strings.Contains(path, "/subitems") {
		subItemsHandler(w, r)
		return
	}

	// Handle specific requirement operations
	id := strings.TrimPrefix(path, "/requirements/")
	id = strings.Split(id, "/")[0] // Get just the ID part if there are more segments

	switch r.Method {
	case http.MethodGet:
		getRequirementByID(w, r)
	case http.MethodPut:
		updateRequirement(w, r)
	case http.MethodDelete:
		deleteRequirement(w, r)
	default:
		w.WriteHeader(http.StatusMethodNotAllowed)
	}
}

func requirementsByStatusHandler(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodGet:
		getRequirementsByStatus(w, r)
	default:
		w.WriteHeader(http.StatusMethodNotAllowed)
	}
}

// subItemsHandler handles all sub-items related operations
func subItemsHandler(w http.ResponseWriter, r *http.Request) {
	path := r.URL.Path

	// Extract requirement ID and sub-item ID from the path
	// Path could be: /requirements/{reqID}/subitems (for adding) or
	//                /requirements/{reqID}/subitems/{subID} (for updating/deleting)

	parts := strings.Split(path, "/")
	if len(parts) < 4 || parts[1] != "requirements" || parts[3] != "subitems" {
		http.Error(w, "Invalid path", http.StatusBadRequest)
		return
	}

	_ = parts[2] // reqID - the handlers extract the reqID from the request path

	if len(parts) == 4 {
		// Adding a new subitem: /requirements/{reqID}/subitems
		switch r.Method {
		case http.MethodPost:
			addSubItem(w, r)
		default:
			w.WriteHeader(http.StatusMethodNotAllowed)
		}
		return
	}

	if len(parts) == 5 {
		// Updating or deleting a subitem: /requirements/{reqID}/subitems/{subID}
		switch r.Method {
		case http.MethodPut:
			updateSubItem(w, r)
		case http.MethodDelete:
			deleteSubItem(w, r)
		default:
			w.WriteHeader(http.StatusMethodNotAllowed)
		}
		return
	}

	http.Error(w, "Invalid path", http.StatusBadRequest)
}