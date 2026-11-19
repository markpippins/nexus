const express = require('express');
const router = express.Router();

// Get all services with related data
router.get('/', async (req, res) => {
  try {
    const { prisma } = require('../index');
    const records = await prisma.service.findMany({
      where: { active_flag: true },
      include: {
        framework: {
          include: {
            vendor: true,
            category: true,
            language: true
          }
        },
        serviceType: true
      }
    });
    res.json(records);
  } catch (error) {
    console.error('Error fetching services:', error);
    res.status(500).json({ error: 'Failed to fetch services' });
  }
});

// Get single service by ID
router.get('/:id', async (req, res) => {
  try {
    const { prisma } = require('../index');
    const { id } = req.params;
    const record = await prisma.service.findUnique({
      where: { id },
      include: {
        framework: {
          include: {
            vendor: true,
            category: true,
            language: true
          }
        },
        serviceType: true
      }
    });
    
    if (!record) {
      return res.status(404).json({ error: 'Service not found' });
    }
    
    res.json(record);
  } catch (error) {
    console.error('Error fetching service:', error);
    res.status(500).json({ error: 'Failed to fetch service' });
  }
});

// Create new service
router.post('/', async (req, res) => {
  try {
    const { prisma } = require('../index');
    const data = req.body;
    const record = await prisma.service.create({
      data,
      include: {
        framework: true,
        serviceType: true
      }
    });
    res.status(201).json(record);
  } catch (error) {
    console.error('Error creating service:', error);
    res.status(500).json({ error: 'Failed to create service' });
  }
});

// Update service
router.put('/:id', async (req, res) => {
  try {
    const { prisma } = require('../index');
    const { id } = req.params;
    const data = req.body;
    
    const record = await prisma.service.update({
      where: { id },
      data,
      include: {
        framework: true,
        serviceType: true
      }
    });
    
    res.json(record);
  } catch (error) {
    console.error('Error updating service:', error);
    res.status(500).json({ error: 'Failed to update service' });
  }
});

// Delete service (soft delete)
router.delete('/:id', async (req, res) => {
  try {
    const { prisma } = require('../index');
    const { id } = req.params;
    
    await prisma.service.update({
      where: { id },
      data: { active_flag: false }
    });
    
    res.json({ message: 'Service deleted successfully' });
  } catch (error) {
    console.error('Error deleting service:', error);
    res.status(500).json({ error: 'Failed to delete service' });
  }
});

module.exports = router;