package main

import (
	"fmt"
	"log"
	"os"
	"time"

	"gorm.io/driver/mysql"
	"gorm.io/gorm"
	"gorm.io/gorm/logger"
)

// Project represents a project in the system
type Project struct {
	ID        uint      `json:"id" gorm:"primaryKey"`
	Name      string    `json:"name" gorm:"not null"`
	CreatedAt time.Time `json:"createdAt"`
	UpdatedAt time.Time `json:"updatedAt"`

	// Relationships
	Subsystems   []Subsystem   `json:"subsystems,omitempty" gorm:"foreignKey:ProjectID"`
	Requirements []Requirement `json:"requirements,omitempty" gorm:"foreignKey:ProjectID"`
}

// Subsystem represents a subsystem within a project
type Subsystem struct {
	ID        uint      `json:"id" gorm:"primaryKey"`
	Name      string    `json:"name" gorm:"not null"`
	ProjectID uint      `json:"projectId" gorm:"not null;index"`
	CreatedAt time.Time `json:"createdAt"`
	UpdatedAt time.Time `json:"updatedAt"`

	// Relationships
	Project      Project       `json:"project,omitempty" gorm:"constraint:OnDelete:CASCADE"`
	Features     []Feature     `json:"features,omitempty" gorm:"foreignKey:SubsystemID"`
	Requirements []Requirement `json:"requirements,omitempty" gorm:"foreignKey:SubsystemID"`
}

// Feature represents a feature within a subsystem
type Feature struct {
	ID          uint      `json:"id" gorm:"primaryKey"`
	Name        string    `json:"name" gorm:"not null"`
	SubsystemID uint      `json:"subsystemId" gorm:"not null;index"`
	CreatedAt   time.Time `json:"createdAt"`
	UpdatedAt   time.Time `json:"updatedAt"`

	// Relationships
	Subsystem    Subsystem     `json:"subsystem,omitempty" gorm:"constraint:OnDelete:CASCADE"`
	Requirements []Requirement `json:"requirements,omitempty" gorm:"foreignKey:FeatureID"`
}

// Requirement represents a service requirement with updated relationships
type Requirement struct {
	ID           uint      `json:"id" gorm:"primaryKey"`
	Name         string    `json:"name" gorm:"not null"`
	Description  string    `json:"description" gorm:"type:text"`
	Status       string    `json:"status" gorm:"default:'pending'"` // pending, in-progress, complete
	Technologies []string  `json:"technologies" gorm:"serializer:json"`
	ProjectID    *uint     `json:"projectId" gorm:"index"`   // Optional foreign key
	SubsystemID  *uint     `json:"subsystemId" gorm:"index"` // Optional foreign key
	FeatureID    *uint     `json:"featureId" gorm:"index"`   // Optional foreign key
	CreatedAt    time.Time `json:"createdAt"`
	UpdatedAt    time.Time `json:"updatedAt"`

	// Relationships
	Project   *Project   `json:"project,omitempty" gorm:"constraint:OnDelete:SET NULL"`
	Subsystem *Subsystem `json:"subsystem,omitempty" gorm:"constraint:OnDelete:SET NULL"`
	Feature   *Feature   `json:"feature,omitempty" gorm:"constraint:OnDelete:SET NULL"`
	SubItems  []SubItem  `json:"subItems,omitempty" gorm:"foreignKey:RequirementID"`
}

// SubItem represents a sub-item of a requirement
type SubItem struct {
	ID            uint      `json:"id" gorm:"primaryKey"`
	Name          string    `json:"name" gorm:"not null"`
	Status        string    `json:"status" gorm:"default:'pending'"` // pending, in-progress, complete
	RequirementID uint      `json:"requirementId" gorm:"not null;index"`
	CreatedAt     time.Time `json:"createdAt"`
	UpdatedAt     time.Time `json:"updatedAt"`

	// Relationships
	Requirement Requirement `json:"requirement,omitempty" gorm:"constraint:OnDelete:CASCADE"`
}

// DB holds the database connection
var DB *gorm.DB

// InitDatabase initializes the MySQL database connection and creates tables
func InitDatabase() {
	var err error

	// Configure GORM logger
	gormConfig := &gorm.Config{
		Logger: logger.Default.LogMode(logger.Info),
	}

	// Get database connection from environment or use defaults from scripts/start-mysql.sh
	dbHost := getEnvOrDefault("DB_HOST", "localhost")
	dbPort := getEnvOrDefault("DB_PORT", "3306")
	dbUser := getEnvOrDefault("DB_USER", "root")
	dbPassword := getEnvOrDefault("DB_PASSWORD", "rootpass")
	dbName := getEnvOrDefault("DB_NAME", "projman_service")

	// First connect to MySQL without specifying database to create schema
	rootDSN := fmt.Sprintf("%s:%s@tcp(%s:%s)/?charset=utf8mb4&parseTime=True&loc=Local", dbUser, dbPassword, dbHost, dbPort)

	DB, err = gorm.Open(mysql.Open(rootDSN), gormConfig)
	if err != nil {
		log.Fatal("Failed to connect to MySQL server:", err)
	}

	// Create the database if it doesn't exist
	createDBSQL := fmt.Sprintf("CREATE DATABASE IF NOT EXISTS %s CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci", dbName)
	if err := DB.Exec(createDBSQL).Error; err != nil {
		log.Fatal("Failed to create database:", err)
	}

	log.Printf("Database '%s' created or already exists", dbName)

	// Close connection and reconnect to the specific database
	sqlDB, err := DB.DB()
	if err != nil {
		log.Fatal("Failed to get underlying sql.DB:", err)
	}
	sqlDB.Close()

	// Connect to the specific database
	dbDSN := fmt.Sprintf("%s:%s@tcp(%s:%s)/%s?charset=utf8mb4&parseTime=True&loc=Local", dbUser, dbPassword, dbHost, dbPort, dbName)
	DB, err = gorm.Open(mysql.Open(dbDSN), gormConfig)
	if err != nil {
		log.Fatal("Failed to connect to database '%s':", err)
	}

	// Auto-migrate all tables
	log.Println("Starting auto-migration of tables...")
	if err := DB.AutoMigrate(&Project{}, &Subsystem{}, &Feature{}, &Requirement{}, &SubItem{}); err != nil {
		log.Fatal("Failed to migrate database:", err)
	}

	log.Printf("Database '%s' connected and tables migrated successfully", dbName)
}

// getEnvOrDefault returns environment variable value or default
func getEnvOrDefault(key, defaultValue string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return defaultValue
}

// SeedData seeds the database with initial sample data
func SeedData() {
	// Check if data already exists
	var count int64
	DB.Model(&Project{}).Count(&count)
	if count > 0 {
		log.Println("Database already contains data, skipping seed")
		return
	}

	// Create sample projects
	projects := []Project{
		{Name: "E-commerce Platform"},
		{Name: "Mobile Banking App"},
		{Name: "Data Analytics Dashboard"},
	}

	for i := range projects {
		if err := DB.Create(&projects[i]).Error; err != nil {
			log.Printf("Error creating project: %v", err)
		}
	}

	// Create sample subsystems
	subsystems := []Subsystem{
		{Name: "User Management", ProjectID: projects[0].ID},
		{Name: "Product Catalog", ProjectID: projects[0].ID},
		{Name: "Payment Processing", ProjectID: projects[0].ID},
		{Name: "Authentication", ProjectID: projects[1].ID},
		{Name: "Account Management", ProjectID: projects[1].ID},
		{Name: "Transaction History", ProjectID: projects[1].ID},
	}

	for i := range subsystems {
		if err := DB.Create(&subsystems[i]).Error; err != nil {
			log.Printf("Error creating subsystem: %v", err)
		}
	}

	// Create sample features
	features := []Feature{
		{Name: "User Registration", SubsystemID: subsystems[0].ID},
		{Name: "Login/Logout", SubsystemID: subsystems[0].ID},
		{Name: "Product Search", SubsystemID: subsystems[1].ID},
		{Name: "Product Details", SubsystemID: subsystems[1].ID},
		{Name: "Credit Card Processing", SubsystemID: subsystems[2].ID},
		{Name: "Digital Wallet", SubsystemID: subsystems[2].ID},
		{Name: "Biometric Login", SubsystemID: subsystems[3].ID},
		{Name: "Two-Factor Auth", SubsystemID: subsystems[3].ID},
	}

	for i := range features {
		if err := DB.Create(&features[i]).Error; err != nil {
			log.Printf("Error creating feature: %v", err)
		}
	}

	log.Println("Sample data seeded successfully")
}

// Helper function to convert uint to string for ID compatibility
func uintToString(u uint) string {
	return fmt.Sprintf("%d", u)
}

// Helper function to convert string to uint
func stringToUint(s string) (uint, error) {
	var u uint
	_, err := fmt.Sscanf(s, "%d", &u)
	return u, err
}
