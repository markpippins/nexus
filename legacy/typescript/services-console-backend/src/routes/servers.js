const express = require('express');
const router = express.Router();

// Get all servers with related data
router.get('/', async (req, res) => {
  try {
    const { prisma } = require('../index');
    const records = await prisma.server.findMany({
      where: { active_flag: true },
      include: {
        serverType: true,
        environmentType: true,
        operatingSystem: {
          include: {
            vendor: true
          }
        }
      }
    });
    res.json(records);
  } catch (error) {
    console.error('Error fetching servers:', error);
    res.status(500).json({ error: 'Failed to fetch servers' });
  }
});

// Get single server by ID
router.get('/:id', async (req, res) => {
  try {
    const { prisma } = require('../index');
    const { id } = req.params;
    const record = await prisma.server.findUnique({
      where: { id },
      include: {
        serverType: true,
        environmentType: true,
        operatingSystem: {
          include: {
            vendor: true
          }
        }
      }
    });
    
    if (!record) {
      return res.status(404).json({ error: 'Server not found' });
    }
    
    res.json(record);
  } catch (error) {
    console.error('Error fetching server:', error);
    res.status(500).json({ error: 'Failed to fetch server' });
  }
});

// Create new server
router.post('/', async (req, res) => {
  try {
    const { prisma } = require('../index');
    const data = req.body;
    const record = await prisma.server.create({
      data,
      include: {
        serverType: true,
        environmentType: true,
        operatingSystem: true
      }
    });
    res.status(201).json(record);
  } catch (error) {
    console.error('Error creating server:', error);
    res.status(500).json({ error: 'Failed to create server' });
  }
});

// Update server
router.put('/:id', async (req, res) => {
  try {
    const { prisma } = require('../index');
    const { id } = req.params;
    const data = req.body;
    
    const record = await prisma.server.update({
      where: { id },
      data,
      include: {
        serverType: true,
        environmentType: true,
        operatingSystem: true
      }
    });
    
    res.json(record);
  } catch (error) {
    console.error('Error updating server:', error);
    res.status(500).json({ error: 'Failed to update server' });
  }
});

// Delete server (soft delete)
router.delete('/:id', async (req, res) => {
  try {
    const { prisma } = require('../index');
    const { id } = req.params;
    
    await prisma.server.update({
      where: { id },
      data: { active_flag: false }
    });
    
    res.json({ message: 'Server deleted successfully' });
  } catch (error) {
    console.error('Error deleting server:', error);
    res.status(500).json({ error: 'Failed to delete server' });
  }
});

module.exports = router;