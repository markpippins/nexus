// To run this server, you will need Node.js installed.
// Execute the following command in your terminal:
// node serv/image-serv.js [imageRootDir]
// Note: If you have a TypeScript runner like ts-node, you can use:
// ts-node serv/image-serv.ts [imageRootDir]
// For this environment, we will assume it's compiled to JS and run.

import * as http from 'node:http';
import * as url from 'node:url';
import * as fs from 'node:fs/promises';
import * as path from 'node:path';
import * as dotenv from 'dotenv';
import * as winston from 'winston';

// Configure logging
const logger = winston.createLogger({
  level: process.env.LOG_LEVEL || 'info',
  format: winston.format.combine(
    winston.format.timestamp(),
    winston.format.errors({ stack: true }),
    winston.format.splat(),
    winston.format.json()
  ),
  defaultMeta: { service: 'image-server' },
  transports: [
    new winston.transports.Console({
      format: winston.format.combine(
        winston.format.colorize(),
        winston.format.printf(
          (info) => `${info.timestamp} ${info.level} [${info.service}] ${info.message}`
        )
      ),
    }),
  ],
});

// Load environment variables from .env file
dotenv.config();

const PORT = process.env.IMAGE_SERVER_PORT || 8081;

// --- NEW: Allow command-line override for image root directory ---
// Usage: node serv/image-serv.js [imageRootDir]
const cliRootArg = process.argv[2];
const effectiveRoot = cliRootArg || process.env.IMAGE_ROOT_DIR;

// Ensure IMAGE_ROOT_DIR is an absolute path for security and consistency.
const IMAGE_ROOT_DIR = effectiveRoot
  ? path.resolve(effectiveRoot.trim())
  : path.resolve(process.cwd(), 'images'); // fallback default

// Array of folder locations to search in
const FOLDER_LOCATIONS = [
  IMAGE_ROOT_DIR, // default root location
  // path.join(IMAGE_ROOT_DIR, 'ui', "neon"),
  path.join(IMAGE_ROOT_DIR, 'device'),
  path.join(IMAGE_ROOT_DIR, 'logo'),
  path.join(IMAGE_ROOT_DIR, 'ui', 'shared'),
  path.join(IMAGE_ROOT_DIR, 'ui', '3d-fluency'),
  path.join(IMAGE_ROOT_DIR, 'ui', 'ui', '3d-fluency'), // additional location based on actual structure
  path.join(IMAGE_ROOT_DIR, 'ui', 'ui', 'shared'),
  path.join(IMAGE_ROOT_DIR, 'ui', 'ui', 'neon'),
  path.join(IMAGE_ROOT_DIR, 'ui', 'ui', 'plastina-3d')
  // Add more folder locations here as needed
];

const PREFERRED_EXTENSIONS = ['.svg', '.png', '.jpg', '.jpeg', '.gif'];
const UI_ICON_NAMES = ['Users', 'Home', 'Desktop', 'Documents', 'resources'];

const MIME_TYPES: Record<string, string> = {
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.svg': 'image/svg+xml',
  '.webp': 'image/webp',
};

// --- Server Logic ---

// Helper to serve a static file if it exists
const serveStaticFile = async (baseName: string, res: http.ServerResponse, searchLocations: string[] = [IMAGE_ROOT_DIR]): Promise<boolean> => {
  // Check if the basename already has an extension
  const hasExtension = PREFERRED_EXTENSIONS.some(ext => baseName.toLowerCase().endsWith(ext));

  if (hasExtension) {
    // If it already has an extension, look for that specific file
    for (const location of searchLocations) {
      const filePath = path.join(location, baseName);
      try {
        await fs.access(filePath); // Check for existence

        // Determine content type from extension
        const ext = path.extname(baseName).toLowerCase();
        const contentType = MIME_TYPES[ext] || 'application/octet-stream';

        const fileContent = await fs.readFile(filePath);
        res.writeHead(200, {
          'Content-Type': contentType,
          'Content-Length': fileContent.length,
          'Cache-Control': 'public, max-age=3600' // Cache for 1 hour
        });
        res.end(fileContent);
        logger.info('File served successfully', { baseName, filePath, location });
        return true; // File found and served
      } catch (error) {
        logger.debug(`File not found or inaccessible`, { baseName, filePath, location, error: (error as Error).message });
        // Continue to try next location
      }
    }
  } else {
    // If it doesn't have an extension, try adding all supported extensions
    for (const location of searchLocations) {
      for (const ext of PREFERRED_EXTENSIONS) {
        const fileName = `${baseName}${ext}`;
        const filePath = path.join(location, fileName);
        try {
          await fs.access(filePath); // Check for existence

          const fileContent = await fs.readFile(filePath);
          const contentType = MIME_TYPES[ext] || 'application/octet-stream';

          res.writeHead(200, {
            'Content-Type': contentType,
            'Content-Length': fileContent.length,
            'Cache-Control': 'public, max-age=3600' // Cache for 1 hour
          });
          res.end(fileContent);
          logger.info('File served successfully', { fileName, filePath, location, ext });
          return true; // File found and served
        } catch (error) {
          logger.debug(`File not found or inaccessible`, { fileName, filePath, location, ext, error: (error as Error).message });
          // Continue to try next extension/location
        }
      }
    }
  }
  return false; // No matching file found
};

const server = http.createServer(async (req, res) => {
  // Log incoming requests
  logger.info(`Incoming request`, {
    method: req.method,
    url: req.url,
    headers: req.headers
  });

  // CORS headers - IMPORTANT: Set these for all responses, including errors
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Origin, X-Requested-With, Content-Type, Accept, Range');
  res.setHeader('Access-Control-Expose-Headers', 'Content-Length, Content-Range');
  // Note: Access-Control-Allow-Credentials is omitted when using wildcard origin (*)

  // Handle preflight OPTIONS request
  if (req.method === 'OPTIONS') {
    logger.debug('Handling CORS preflight request');
    res.writeHead(204); // No content
    res.end();
    return;
  }

  const parsedUrl = url.parse(req.url ?? '', true);
  const pathParts = (parsedUrl.pathname ?? '').split('/').filter(p => p);

  // Handle health check endpoint
  if (req.url === '/health' && req.method === 'GET') {
    logger.info('Health check endpoint called');
    try {
      // Check if the image root directory is accessible
      await fs.access(IMAGE_ROOT_DIR);

      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({
        status: 'UP',
        service: 'image-server',
        timestamp: new Date().toISOString(),
        details: {
          imageRootDir: IMAGE_ROOT_DIR,
          port: PORT,
          searchLocations: FOLDER_LOCATIONS.length
        }
      }));
      logger.info('Health check successful');
    } catch (error) {
      logger.error('Health check failed', { error: (error as Error).message });
      res.writeHead(503, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({
        status: 'DOWN',
        service: 'image-server',
        timestamp: new Date().toISOString(),
        error: error instanceof Error ? error.message : 'Image root directory not accessible'
      }));
    }
    return;
  }

  // Handle default route (no prefix) - search through all folder locations
  if (pathParts.length === 0) {
    if (req.url === '/' || req.url === '/favicon.ico') {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ status: 'Image Server is running' }));
      return;
    }
  }

  try {
    let fileServed = false;

    if (pathParts.length > 0) {
      const [endpoint, ...params] = pathParts;

      // Check if this is a special endpoint (ui, name, ext)
      if (endpoint === 'ui') {
        const name = decodeURIComponent(params[0] || '');
        const lowerCaseName = name.toLowerCase();
        const isAllowed = UI_ICON_NAMES.some(n => n.toLowerCase() === lowerCaseName);

        logger.info('Searching UI folder', { requestedName: name });

        if (isAllowed) {
          logger.info('UI icon requested', { name, lowerCaseName });
          // For UI route, also search through all folder locations
          // Try looking for the file directly or in ui subfolder
          fileServed = await serveStaticFile(lowerCaseName, res, FOLDER_LOCATIONS);
        } else {
          logger.info('UI icon not in allowed list', { name, lowerCaseName, allowedIcons: UI_ICON_NAMES });
        }
      } else if (endpoint === 'name') {
        const name = decodeURIComponent(params[0] || '');
        logger.info('Searching name folder', { requestedName: name });
        const lowerCaseName = name.toLowerCase();
        fileServed = await serveStaticFile(lowerCaseName, res);
      } else if (endpoint === 'ext') {
        const ext = decodeURIComponent(params[0] || '');
        logger.info('Searching ext folder', { requestedExtension: ext });
        fileServed = await serveStaticFile(ext, res);
      } else {
        const filePath = pathParts.join('/');
        logger.info('Default Search', { requestedPath: filePath });
        // Default route - treat the entire path as a filename and search through all folder locations
        fileServed = await serveStaticFile(filePath, res, FOLDER_LOCATIONS);
      }
    }

    if (!fileServed) {
      logger.warn('File not found', { path: req.url, pathParts, endpoint: pathParts[0], params: pathParts.slice(1) });
      res.writeHead(404, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: 'Not Found', path: req.url }));
    } else {
      logger.info('Request processed successfully', { path: req.url, pathParts, endpoint: pathParts[0], params: pathParts.slice(1) });
    }
  } catch (e) {
    logger.error('Error processing request:', { error: (e as Error).message, stack: (e as Error).stack, path: req.url, pathParts, endpoint: pathParts[0], params: pathParts.slice(1) });
    res.writeHead(500, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ error: 'Server Error', message: (e as Error).message }));
  }
});

server.listen(PORT, () => {
  logger.info(`Server listening on port ${PORT}`, { port: PORT, host: '0.0.0.0' });
  logger.info(`Serving static images from: ${IMAGE_ROOT_DIR}`, { imageRootDir: IMAGE_ROOT_DIR });
  logger.info(`Additional search locations: ${FOLDER_LOCATIONS.slice(1).join(', ') || 'none'}`, {
    searchLocationsCount: FOLDER_LOCATIONS.length - 1,
    searchLocations: FOLDER_LOCATIONS.slice(1)
  });
  logger.info('Image server started successfully', {
    port: PORT,
    imageRootDir: IMAGE_ROOT_DIR,
    totalSearchLocations: FOLDER_LOCATIONS.length,
    preferredExtensions: PREFERRED_EXTENSIONS
  });
});
