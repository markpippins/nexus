const express = require('express');
const router = express.Router();

// Get all service configs with related data
router.get('/', async (req, res) => {
  try {
    const { prisma } = require('../index');
    const records = await prisma.serviceConfig.findMany({
      where: { active_flag: true },
      include: {
        service: true,
        configType: true,
        environment: true
      }
    });
    res.json(records);
  } catch (error) {
    console.error('Error fetching service configs:', error);
    res.status(500).json({ error: 'Failed to fetch service configs' });
  }
});

router.get('/:id', async (req, res) => {
  try {
    const { prisma } = require('../index');
    const { id } = req.params;
    const record = await prisma.serviceConfig.findUnique({
      where: { id },
      include: {
        service: true,
        configType: true,
        environment: true
      }
    });
    
    if (!record) {
      return res.status(404).json({ error: 'Service config not found' });
    }
    
    res.json(record);
  } catch (error) {
    console.error('Error fetching service config:', error);
    res.status(500).json({ error: 'Failed to fetch service config' });
  }
});

router.post('/', async (req, res) => {
  try {
    const { prisma } = require('../index');
    const data = req.body;
    const record = await prisma.serviceConfig.create({
      data,
      include: {
        service: true,
        configType: true,
        environment: true
      }
    });
    res.status(201).json(record);
  } catch (error) {
    console.error('Error creating service config:', error);
    res.status(500).json({ error: 'Failed to create service config' });
  }
});

router.put('/:id', async (req, res) => {
  try {
    const { prisma } = require('../index');
    const { id } = req.params;
    const data = req.body;
    
    const record = await prisma.serviceConfig.update({
      where: { id },
      data,
      include: {
        service: true,
        configType: true,
        environment: true
      }
    });
    
    res.json(record);
  } catch (error) {
    console.error('Error updating service config:', error);
    res.status(500).json({ error: 'Failed to update service config' });
  }
});

router.delete('/:id', async (req, res) => {
  try {
    const { prisma } = require('../index');
    const { id } = req.params;
    
    await prisma.serviceConfig.update({
      where: { id },
      data: { active_flag: false }
    });
    
    res.json({ message: 'Service config deleted successfully' });
  } catch (error) {
    console.error('Error deleting service config:', error);
    res.status(500).json({ error: 'Failed to delete service config' });
  }
});

module.exports = router;
