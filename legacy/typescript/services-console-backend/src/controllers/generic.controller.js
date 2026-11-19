const { PrismaClient } = require('@prisma/client');
const prisma = new PrismaClient();

class GenericController {
  constructor(modelName) {
    this.modelName = modelName;
    this.model = prisma[modelName];
  }

  // Get all records
  getAll = async (req, res) => {
    try {
      const records = await this.model.findMany({
        where: { active_flag: true }
      });
      res.json(records);
    } catch (error) {
      console.error(`Error fetching ${this.modelName}:`, error);
      res.status(500).json({ error: `Failed to fetch ${this.modelName}` });
    }
  };

  // Get single record by ID
  getById = async (req, res) => {
    try {
      const { id } = req.params;
      const record = await this.model.findUnique({
        where: { id }
      });
      
      if (!record) {
        return res.status(404).json({ error: `${this.modelName} not found` });
      }
      
      res.json(record);
    } catch (error) {
      console.error(`Error fetching ${this.modelName}:`, error);
      res.status(500).json({ error: `Failed to fetch ${this.modelName}` });
    }
  };

  // Create new record
  create = async (req, res) => {
    try {
      const data = req.body;
      const record = await this.model.create({
        data
      });
      res.status(201).json(record);
    } catch (error) {
      console.error(`Error creating ${this.modelName}:`, error);
      res.status(500).json({ error: `Failed to create ${this.modelName}` });
    }
  };

  // Update record
  update = async (req, res) => {
    try {
      const { id } = req.params;
      const data = req.body;
      
      const record = await this.model.update({
        where: { id },
        data
      });
      
      res.json(record);
    } catch (error) {
      console.error(`Error updating ${this.modelName}:`, error);
      res.status(500).json({ error: `Failed to update ${this.modelName}` });
    }
  };

  // Delete record (soft delete by setting active_flag to false)
  delete = async (req, res) => {
    try {
      const { id } = req.params;
      
      const record = await this.model.update({
        where: { id },
        data: { active_flag: false }
      });
      
      res.json({ message: `${this.modelName} deleted successfully` });
    } catch (error) {
      console.error(`Error deleting ${this.modelName}:`, error);
      res.status(500).json({ error: `Failed to delete ${this.modelName}` });
    }
  };
}

module.exports = GenericController;