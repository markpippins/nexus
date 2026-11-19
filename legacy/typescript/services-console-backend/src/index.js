const express = require('express');
const cors = require('cors');
const dotenv = require('dotenv');
const { PrismaClient } = require('@prisma/client');

dotenv.config();

const app = express();
const prisma = new PrismaClient();
const PORT = process.env.PORT || 3001;

// Middleware
app.use(cors());
app.use(express.json());

// Import routes
const languagesRouter = require('./routes/languages');
const categoriesRouter = require('./routes/categories');
const vendorsRouter = require('./routes/vendors');
const frameworksRouter = require('./routes/frameworks');
const operatingSystemsRouter = require('./routes/operatingSystems');
const environmentTypesRouter = require('./routes/environmentTypes');
const serverTypesRouter = require('./routes/serverTypes');
const serversRouter = require('./routes/servers');
const serviceTypesRouter = require('./routes/serviceTypes');
const servicesRouter = require('./routes/services');
const configTypesRouter = require('./routes/configTypes');
const serviceConfigsRouter = require('./routes/serviceConfigs');
const serviceDependenciesRouter = require('./routes/serviceDependencies');
const deploymentsRouter = require('./routes/deployments');

// Mount routes
app.use('/api/languages', languagesRouter);
app.use('/api/categories', categoriesRouter);
app.use('/api/vendors', vendorsRouter);
app.use('/api/frameworks', frameworksRouter);
app.use('/api/operatingSystems', operatingSystemsRouter);
app.use('/api/environmentTypes', environmentTypesRouter);
app.use('/api/serverTypes', serverTypesRouter);
app.use('/api/servers', serversRouter);
app.use('/api/serviceTypes', serviceTypesRouter);
app.use('/api/services', servicesRouter);
app.use('/api/configTypes', configTypesRouter);
app.use('/api/serviceConfigs', serviceConfigsRouter);
app.use('/api/serviceDependencies', serviceDependenciesRouter);
app.use('/api/deployments', deploymentsRouter);

// Health check endpoint
app.get('/api/health', (req, res) => {
  res.json({ status: 'OK', timestamp: new Date().toISOString() });
});

// Error handling middleware
app.use((err, req, res, next) => {
  console.error(err.stack);
  res.status(500).json({ error: 'Something went wrong!' });
});

// 404 handler
app.use('*', (req, res) => {
  res.status(404).json({ error: 'Route not found' });
});

// Graceful shutdown
process.on('beforeExit', async () => {
  console.log('Closing Prisma client...');
  await prisma.$disconnect();
});

process.on('SIGINT', async () => {
  console.log('Received SIGINT, closing Prisma client...');
  await prisma.$disconnect();
  process.exit(0);
});

app.listen(PORT, () => {
  console.log(`🚀 Services Console API server running on http://localhost:${PORT}`);
  console.log(`📊 Health check available at http://localhost:${PORT}/api/health`);
});

module.exports = { app, prisma };