import type { Log, LogFile } from '@/lib/types';

const logMessages = {
  INFO: [
    'User authentication successful for user_id: {id}',
    'New database connection established',
    'API request to /api/users completed in {ms}ms',
    'Data export process started',
    'Configuration file loaded successfully',
    'Scheduled job "daily-backup" started',
    'Server startup complete on port {port}',
  ],
  WARNING: [
    'Database connection pool is reaching its limit',
    'API response time for /api/orders is high: {ms}ms',
    'Disk space is running low on /var/log',
    'Memory usage is at {percent}%',
    'Invalid API token detected from IP: {ip}',
    'Deprecated function "getUser" is being used',
  ],
  ERROR: [
    'Failed to connect to database: Connection refused',
    'Unhandled exception: NullPointerException in module "Payments"',
    'API endpoint /api/products/{id} returned status 500',
    'Failed to write to log file: Permission denied',
    'User authentication failed for user_id: {id}',
    'Service "email-service" is unresponsive',
    'Critical error: Out of memory',
  ],
  DEBUG: [
    'Executing query: SELECT * FROM users WHERE id = ?',
    'Payload for /api/process: {json}',
    'Variable "x" has value: {value}',
    'Function "calculateTotal" entered',
    'Cache miss for key: "user-profile:{id}"',
  ],
};

const sources = ['app-server-1', 'database-primary', 'auth-service', 'worker-process-a', 'api-gateway'];

const getRandomElement = <T>(arr: T[]): T => arr[Math.floor(Math.random() * arr.length)];
const getRandomInt = (min: number, max: number): number => Math.floor(Math.random() * (max - min + 1)) + min;

const generateLogMessage = (level: Log['level']): string => {
  const messageTemplate = getRandomElement(logMessages[level]);
  return messageTemplate
    .replace('{id}', getRandomInt(100, 999).toString())
    .replace('{ms}', getRandomInt(50, 2000).toString())
    .replace('{port}', '8080')
    .replace('{percent}', getRandomInt(80, 99).toString())
    .replace('{ip}', `192.168.1.${getRandomInt(1, 254)}`)
    .replace('{json}', JSON.stringify({ data: 'sample' }))
    .replace('{value}', Math.random().toString(36).substring(7));
};

let logs: Log[] | null = null;

const generateLogs = (): Log[] => {
    if (logs) {
        return logs;
    }

    let logIdCounter = 0;
    const generatedLogs: Log[] = Array.from({ length: 500 }, (_, i) => {
        const levelDistribution = Math.random();
        let level: Log['level'];
        if (levelDistribution < 0.7) {
            level = 'INFO';
        } else if (levelDistribution < 0.85) {
            level = 'WARNING';
        } else if (levelDistribution < 0.95) {
            level = 'ERROR';
        } else {
            level = 'DEBUG';
        }

        const timestamp = new Date(Date.now() - i * getRandomInt(1000, 60000)).toISOString();

        return {
            id: `log-${logIdCounter++}`,
            timestamp,
            level,
            message: generateLogMessage(level),
            source: getRandomElement(sources),
        };
    }).sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime());

    logs = generatedLogs;
    return logs;
}

export const getLogs = (limit?: number): Log[] => {
  const allLogs = generateLogs();
  if (limit) {
    return allLogs.slice(0, limit);
  }
  return allLogs;
};

export const getLogFiles = (): LogFile[] => {
  return [
    {
      id: 'file-1',
      name: 'app-server-1.log',
      path: '/var/log/app/app-server-1.log',
      size: '25.6 MB',
      lastModified: new Date(Date.now() - 3600000).toISOString(),
      logCount: 12450,
    },
    {
      id: 'file-2',
      name: 'database-primary.log',
      path: '/var/log/db/database-primary.log',
      size: '102.1 MB',
      lastModified: new Date(Date.now() - 1800000).toISOString(),
      logCount: 56789,
    },
    {
      id: 'file-3',
      name: 'auth-service.log',
      path: '/var/log/auth/auth-service.log',
      size: '5.2 MB',
      lastModified: new Date(Date.now() - 7200000).toISOString(),
      logCount: 8912,
    },
    {
      id: 'file-4',
      name: 'worker.log',
      path: '/var/log/jobs/worker.log',
      size: '12.8 MB',
      lastModified: new Date(Date.now() - 600000).toISOString(),
      logCount: 3456,
    },
  ];
};
