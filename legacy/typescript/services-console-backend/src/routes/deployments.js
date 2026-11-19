const express = require('express');
const router = express.Router();

// Get all deployments with related data
router.get('/', async (req, res) => {
  try {
    const { prisma } = require('../index');
    const records = await prisma.deployment.findMany({
      where: { active_flag: true },
      include: {
        service: true,
        environment: true,
        server: {
          include: {
            serverType: true,
            environmentType: true,
            operatingSystem: true
          }
        }
      },
      orderBy: {
        deployed_at: 'desc'
      }
    });
    res.json(records);
  } catch (error) {
    console.error('Error fetching deployments:', error);
    res.status(500).json({ error: 'Failed to fetch deployments' });
  }
});

router.get('/:id', async (req, res) => {
  try {
    const { prisma } = require('../index');
    const { id } = req.params;
    const record = await prisma.deployment.findUnique({
      where: { id },
      include: {
        service: true,
        environment: true,
        server: {
          include: {
            serverType: true,
            environmentType: true,
            operatingSystem: true
          }
        }
      }
    });
    
    if (!record) {
      return res.status(404).json({ error: 'Deployment not found' });
    }
    
    res.json(record);
  } catch (error) {
    console.error('Error fetching deployment:', error);
    res.status(500).json({ error: 'Failed to fetch deployment' });
  }
});

router.post('/', async (req, res) => {
  try {
    const { prisma } = require('../index');
    const data = req.body;
    
    // Convert deployed_at to Date if it's a string
    if (data.deployed_at && typeof data.deployed_at === 'string') {
      data.deployed_at = new Date(data.deployed_at);
    }
    
    const record = await prisma.deployment.create({
      data,
      include: {
        service: true,
        environment: true,
        server: true
      }
    });
    res.status(201).json(record);
  } catch (error) {
    console.error('Error creating deployment:', error);
    res.status(500).json({ error: 'Failed to create deployment' });
  }
});

router.put('/:id', async (req, res) => {
  try {
    const { prisma } = require('../index');
    const { id } = req.params;
    const data = req.body;
    
    // Convert deployed_at to Date if it's a string
    if (data.deployed_at && typeof data.deployed_at === 'string') {
      data.deployed_at = new Date(data.deployed_at);
    }
    
    const record = await prisma.deployment.update({
      where: { id },
      data,
      include: {
        service: true,
        environment: true,
        server: true
      }
    });
    
    res.json(record);
  } catch (error) {
    console.error('Error updating deployment:', error);
    res.status(500).json({ error: 'Failed to update deployment' });
  }
});

router.delete('/:id', async (req, res) => {
  try {
    const { prisma } = require('../index');
    const { id } = req.params;
    
    await prisma.deployment.update({
      where: { id },
      data: { active_flag: false }
    });
    
    res.json({ message: 'Deployment deleted successfully' });
  } catch (error) {
    console.error('Error deleting deployment:', error);
    res.status(500).json({ error: 'Failed to delete deployment' });
  }
});

module.exports = router;
