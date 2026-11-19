package main

import (
	"log"
	"net/http"
	"strings"
	"time"
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

	// Requirement endpoints
	r.mux.HandleFunc("/requirements", requirementsHandler)
	r.mux.HandleFunc("/requirements/", requirementsHandlerWithID)
	r.mux.HandleFunc("/requirements/status/", requirementsByStatusHandler)

	// Project endpoints
	r.mux.HandleFunc("/projects", projectsHandler)
	r.mux.HandleFunc("/projects/", projectsHandlerWithID)

	// Subsystem endpoints
	r.mux.HandleFunc("/subsystems", subsystemsHandler)
	r.mux.HandleFunc("/subsystems/", subsystemsHandlerWithID)

	// Feature endpoints
	r.mux.HandleFunc("/features", featuresHandler)
	r.mux.HandleFunc("/features/", featuresHandlerWithID)

	return r
}

// LoggingResponseWriter wraps http.ResponseWriter to capture status code
type LoggingResponseWriter struct {
	http.ResponseWriter
	statusCode int
}

func (lrw *LoggingResponseWriter) WriteHeader(code int) {
	lrw.statusCode = code
	lrw.ResponseWriter.WriteHeader(code)
}

func (r *Router) ServeHTTP(w http.ResponseWriter, req *http.Request) {
	start := time.Now()

	// Create a wrapped response writer to capture status code
	lrw := &LoggingResponseWriter{
		ResponseWriter: w,
		statusCode:     http.StatusOK, // Default to 200
	}

	// Add CORS headers
	lrw.Header().Set("Access-Control-Allow-Origin", "*")
	lrw.Header().Set("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
	lrw.Header().Set("Access-Control-Allow-Headers", "Content-Type, Authorization")

	// Handle preflight OPTIONS requests
	if req.Method == "OPTIONS" {
		lrw.WriteHeader(http.StatusOK)
		log.Printf("OPTIONS %s %d %v", req.URL.Path, lrw.statusCode, time.Since(start))
		return
	}

	// Log the incoming request
	log.Printf("Incoming request: %s %s from %s", req.Method, req.URL.Path, req.RemoteAddr)

	// Serve the request
	r.mux.ServeHTTP(lrw, req)

	// Log the completed request
	log.Printf("Completed request: %s %s %d %v", req.Method, req.URL.Path, lrw.statusCode, time.Since(start))
}

func healthHandler(w http.ResponseWriter, r *http.Request) {
	log.Printf("GET /health - Health check endpoint called from %s", r.RemoteAddr)
	w.WriteHeader(http.StatusOK)
	if _, err := w.Write([]byte("OK")); err != nil {
		log.Printf("Error writing health check response: %v", err)
	}
	log.Printf("GET /health - Health check completed successfully")
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

// PROJECT HANDLERS
func projectsHandler(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodGet:
		getProjects(w, r)
	case http.MethodPost:
		createProject(w, r)
	default:
		w.WriteHeader(http.StatusMethodNotAllowed)
	}
}

func projectsHandlerWithID(w http.ResponseWriter, r *http.Request) {
	path := r.URL.Path

	// Check if this is a subsystems lookup for this project
	if strings.HasSuffix(path, "/subsystems") {
		getSubsystemsByProject(w, r)
		return
	}

	id := strings.TrimPrefix(path, "/projects/")
	id = strings.Split(id, "/")[0] // Get just the ID part if there are more segments

	switch r.Method {
	case http.MethodGet:
		getProjectByID(w, r)
	case http.MethodPut:
		updateProject(w, r)
	case http.MethodDelete:
		deleteProject(w, r)
	default:
		w.WriteHeader(http.StatusMethodNotAllowed)
	}
}

// SUBSYSTEM HANDLERS
func subsystemsHandler(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodGet:
		getSubsystems(w, r)
	case http.MethodPost:
		createSubsystem(w, r)
	default:
		w.WriteHeader(http.StatusMethodNotAllowed)
	}
}

func subsystemsHandlerWithID(w http.ResponseWriter, r *http.Request) {
	path := r.URL.Path

	// Check if this is a features lookup for this subsystem
	if strings.HasSuffix(path, "/features") {
		getFeaturesBySubsystem(w, r)
		return
	}

	id := strings.TrimPrefix(path, "/subsystems/")
	id = strings.Split(id, "/")[0] // Get just the ID part if there are more segments

	switch r.Method {
	case http.MethodGet:
		getSubsystemByID(w, r)
	case http.MethodPut:
		updateSubsystem(w, r)
	case http.MethodDelete:
		deleteSubsystem(w, r)
	default:
		w.WriteHeader(http.StatusMethodNotAllowed)
	}
}

// FEATURE HANDLERS
func featuresHandler(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodGet:
		getFeatures(w, r)
	case http.MethodPost:
		createFeature(w, r)
	default:
		w.WriteHeader(http.StatusMethodNotAllowed)
	}
}

func featuresHandlerWithID(w http.ResponseWriter, r *http.Request) {
	id := strings.TrimPrefix(r.URL.Path, "/features/")
	id = strings.Split(id, "/")[0] // Get just the ID part if there are more segments

	switch r.Method {
	case http.MethodGet:
		getFeatureByID(w, r)
	case http.MethodPut:
		updateFeature(w, r)
	case http.MethodDelete:
		deleteFeature(w, r)
	default:
		w.WriteHeader(http.StatusMethodNotAllowed)
	}
}
