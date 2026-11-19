import { Project, FileItem } from '../types';

export const JUDGE0_LANGUAGES = [
  { id: 63, name: 'JavaScript (Node.js 18.15.0)', monacoLang: 'javascript', ext: '.js' },
  { id: 74, name: 'TypeScript (5.0.3)', monacoLang: 'typescript', ext: '.ts' },
  { id: 71, name: 'Python (3.11.2)', monacoLang: 'python', ext: '.py' },
  { id: 54, name: 'C++ (GCC 12.2.0)', monacoLang: 'cpp', ext: '.cpp' },
  { id: 62, name: 'Java (OpenJDK 17.0.6)', monacoLang: 'java', ext: '.java' },
  { id: 60, name: 'Go (1.20.1)', monacoLang: 'go', ext: '.go' },
  { id: 73, name: 'Rust (1.68.2)', monacoLang: 'rust', ext: '.rs' },
  { id: 50, name: 'C (GCC 12.2.0)', monacoLang: 'c', ext: '.c' },
  { id: 82, name: 'SQL (SQLite 3.39.3)', monacoLang: 'sql', ext: '.sql' },
  { id: 43, name: 'HTML (Plain HTML5)', monacoLang: 'html', ext: '.html' },
  { id: 85, name: 'JSON', monacoLang: 'json', ext: '.json' },
  { id: 99, name: 'Markdown', monacoLang: 'markdown', ext: '.md' },
];

export const DEFAULT_FILES: FileItem[] = [
  {
    id: 'f-1',
    name: 'src',
    path: 'src',
    type: 'folder',
    parentId: null,
    children: [
      {
        id: 'f-1-1',
        name: 'index.ts',
        path: 'src/index.ts',
        type: 'file',
        language: 'typescript',
        parentId: 'f-1',
        content: `/**
 * Collaborative Real-Time Studio
 * Welcome to VS Code Collaborative Studio!
 */

interface UserProfile {
  id: string;
  username: string;
  role: 'Developer' | 'Architect' | 'Lead';
  activeProjects: number;
}

function generateWelcomeMessage(user: UserProfile): string {
  const date = new Date().toLocaleDateString('en-US', {
    weekday: 'long',
    year: 'numeric',
    month: 'long',
    day: 'numeric'
  });

  return \`🚀 Welcome \${user.username} [\${user.role}]!
Connected to Collaborative Session on \${date}.
Judge0 execution engine ready.\`;
}

const currentUser: UserProfile = {
  id: "usr_9912",
  username: "DevPilot",
  role: "Lead",
  activeProjects: 4
};

console.log(generateWelcomeMessage(currentUser));

// Fibonacci algorithm demonstration
function fibonacci(n: number): number[] {
  const sequence = [0, 1];
  for (let i = 2; i < n; i++) {
    sequence.push(sequence[i - 1] + sequence[i - 2]);
  }
  return sequence;
}

console.log("Fibonacci(10):", fibonacci(10));
`,
      },
      {
        id: 'f-1-2',
        name: 'algorithms.py',
        path: 'src/algorithms.py',
        type: 'file',
        language: 'python',
        parentId: 'f-1',
        content: `# Python Algorithm Playground - Judge0 Execution Ready
import time
import math

def is_prime(n):
    if n <= 1:
        return False
    for i in range(2, int(math.isqrt(n)) + 1):
        if n % i == 0:
            return False
    return True

def find_primes_up_to(limit):
    start_time = time.time()
    primes = [num for num in range(2, limit) if is_prime(num)]
    duration = (time.time() - start_time) * 1000
    return primes, duration

limit = 100
primes, duration = find_primes_up_to(limit)

print(f"✨ Found {len(primes)} prime numbers up to {limit}")
print(f"Primes: {primes}")
print(f"⚡ Computation completed in {duration:.3f} ms")
`,
      },
      {
        id: 'f-1-3',
        name: 'main.cpp',
        path: 'src/main.cpp',
        type: 'file',
        language: 'cpp',
        parentId: 'f-1',
        content: `// C++ High Performance Example
#include <iostream>
#include <vector>
#include <numeric>

class DataAnalyzer {
public:
    static double calculateAverage(const std::vector<int>& numbers) {
        if (numbers.empty()) return 0.0;
        double sum = std::accumulate(numbers.begin(), numbers.end(), 0.0);
        return sum / numbers.size();
    }
};

int main() {
    std::cout << "🔥 C++ Judge0 Sandbox Execution test" << std::endl;
    std::vector<int> scores = { 95, 88, 92, 100, 78, 85, 91 };
    
    double avg = DataAnalyzer::calculateAverage(scores);
    std::cout << "Data Points Count: " << scores.size() << std::endl;
    std::cout << "Calculated Mean Score: " << avg << std::endl;
    
    return 0;
}
`,
      },
    ],
  },
  {
    id: 'f-2',
    name: 'README.md',
    path: 'README.md',
    type: 'file',
    language: 'markdown',
    parentId: null,
    content: `# 💻 VS Code Collaborative Studio

Welcome to your cloud-based real-time collaborative development workspace!

## ✨ Key Features
- **Monaco Code Editor**: Premium VS Code editor experience with multi-theme support.
- **Judge0 Execution**: Run code in 10+ languages (Python, TypeScript, C++, Java, Go, Rust) with full stdout/stderr capture.
- **Real-Time Collaboration**: Multi-user editing, line/cursor synchronization, presence indicators via WebSockets.
- **Integrated Terminal**: Shell interface, Node REPL, interactive execution logs.
- **Version History & Diffs**: Instant snapshots, visual Monaco side-by-side file diffs, and version restoration.
- **Cloud File System**: Save projects across sessions and access seamlessly on mobile/tablet devices.

## 🚀 Quick Start
1. Open any file in the sidebar tree.
2. Click **Run Code** (Ctrl+Enter / Cmd+Enter) to execute on Judge0.
3. Invite collaborators using your Room Code in the **Collaboration** tab.
`,
  },
  {
    id: 'f-3',
    name: 'package.json',
    path: 'package.json',
    type: 'file',
    language: 'json',
    parentId: null,
    content: `{
  "name": "collaborative-cloud-workspace",
  "version": "1.0.0",
  "description": "Real-time VS Code Studio environment",
  "main": "src/index.ts",
  "scripts": {
    "start": "node dist/index.js",
    "build": "tsc",
    "test": "python3 src/algorithms.py"
  },
  "dependencies": {
    "monaco-editor": "^0.45.0",
    "express": "^4.18.2"
  }
}
`,
  },
];

export const DEFAULT_PROJECT: Project = {
  id: 'proj-default-workspace',
  name: 'Collaborative Workspace',
  description: 'Primary cloud development project',
  files: DEFAULT_FILES,
  createdAt: Date.now() - 86400000,
  updatedAt: Date.now(),
};
