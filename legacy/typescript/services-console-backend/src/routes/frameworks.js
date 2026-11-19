const express = require('express');
const GenericController = require('../controllers/generic.controller');

const router = express.Router();
const controller = new GenericController('framework');

// Enhanced routes with includes for related data
router.get('/', async (req, res) => {
  try {
    const { prisma } = require('../index');
    const records = await prisma.framework.findMany({
      where: { active_flag: true },
      include: {
        vendor: true,
        category: true,
        language: true
      }
    });
    res.json(records);
  } catch (error) {
    console.error('Error fetching frameworks:', error);
    res.status(500).json({ error: 'Failed to fetch frameworks' });
  }
});

router.get('/:id', controller.getById);
router.post('/', controller.create);
router.put('/:id', controller.update);
router.delete('/:id', controller.delete);

module.exports = router;