const NEBULA_BASE = process.env.NEBULA_SRV_URL || 'http://localhost:3101';

export async function fetchNebula(endpoint, query = {}) {    const url = new URL(`${NEBULA_BASE}/api${endpoint}`);
  Object.entries(query).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') {
      url.searchParams.set(key, String(value));
    }
  });

  try {
    const response = await fetch(url.toString(), {
      method: 'GET',
      headers: { 'Content-Type': 'application/json' },
    });

    if (!response.ok) {
      throw new Error(`Nebula API error: ${response.status} ${response.statusText}`);
    }

    const data = await response.json();

    // Normalize shape differences for endpoints that have different field names or types
    if (endpoint === '/plans' && data.items) {
      data.items = data.items.map(normalizePlanItem);
    }

    return data;
  } catch (error) {
    throw new Error(`Failed to fetch from Nebula at ${endpoint}: ${error.message}`);
  }
}

function normalizePlanItem(item) {
  return {
    ...item,
    status: item.status || 'draft',
    goal: item.goal || '',
    acceptanceCriteria: item.acceptanceCriteria || [],
    filesAffected: item.filesAffected || [],
    dependencies: item.dependencies || [],
    decompositionNodes: item.decompositionNodes || [],
    openQuestions: item.openQuestions || [],
  };
}

/**
 * Convert snake_case object keys to camelCase
 * Recursively processes nested objects and arrays
 */
export function snakeToCamel(obj) {
  if (obj === null || typeof obj !== 'object') {
    return obj;
  }

  if (Array.isArray(obj)) {
    return obj.map(item => snakeToCamel(item));
  }

  return Object.keys(obj).reduce((acc, key) => {
    const camelKey = key.replace(/_([a-z])/g, (_, letter) => letter.toUpperCase());
    acc[camelKey] = snakeToCamel(obj[key]);
    return acc;
  }, {});
}