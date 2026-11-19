export type Log = {
  id: string;
  timestamp: string;
  level: "INFO" | "WARNING" | "ERROR" | "DEBUG";
  message: string;
  source: string;
};

export type AnomalousEvent = {
  logEntry: string;
  reason: string;
};

export type LogFile = {
  id: string;
  name: string;
  path: string;
  size: string;
  lastModified: string;
  logCount: number;
};
