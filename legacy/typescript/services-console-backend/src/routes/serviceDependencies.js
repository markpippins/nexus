const express = require('express');
const router = express.Router();

// Get all service dependencies with related data
router.get('/', async (req, res) => {
  try {
    const { prisma } = require('../index');
    const records = await prisma.serviceDependency.findMany({
      where: { active_flag: true },
      include: {
        service: true,
        targetService: true
      }
    });
    res.json(records);
  } catch (error) {
    console.error('Error fetching service dependencies:', error);
    res.status(500).json({ error: 'Failed to fetch service dependencies' });
  }
});

router.get('/:id', async (req, res) => {
  try {
    const { prisma } = require('../index');
    const { id } = req.params;
    const record = await prisma.serviceDependency.findUnique({
      where: { id },
      include: {
        service: true,
        targetService: true
      }
    });
    
    if (!record) {
      return res.status(404).json({ error: 'Service dependency not found' });
    }
    
    res.json(record);
  } catch (error) {
    console.error('Error fetching service dependency:', error);
    res.status(500).json({ error: 'Failed to fetch service dependency' });
  }
});

router.post('/', async (req, res) => {
  try {
    const { prisma } = require('../index');
    const data = req.body;
    const record = await prisma.serviceDependency.create({
      data,
      include: {
        service: true,
        targetService: true
      }
    });
    res.status(201).json(record);
  } catch (error) {
    console.error('Error creating service dependency:', error);
    res.status(500).json({ error: 'Failed to create service dependency' });
  }
});

router.put('/:id', async (req, res) => {
  try {
    const { prisma } = require('../index');
    const { id } = req.params;
    const data = req.body;
    
    const record = await prisma.serviceDependency.update({
      where: { id },
      data,
      include: {
        service: true,
        targetService: true
      }
    });
    
    res.json(record);
  } catch (error) {
    console.error('Error updating service dependency:', error);
    res.status(500).json({ error: 'Failed to update service dependency' });
  }
});

router.delete('/:id', async (req, res) => {
  try {
    const { prisma } = require('../index');
    const { id } = req.params;
    
    await prisma.serviceDependency.update({
      where: { id },
      data: { active_flag: false }
    });
    
    res.json({ message: 'Service dependency deleted successfully' });
  } catch (error) {
    console.error('Error deleting service dependency:', error);
    res.status(500).json({ error: 'Failed to delete service dependency' });
  }
});

module.exports = router;
